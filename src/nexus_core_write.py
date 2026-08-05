"""
nexus_core_write.py — Write, belief initialization, LLM enhancement, feedback, promotion/demotion, conflict detection.

Mixin for NexusCore. Do NOT import directly; use nexus_core.NexusCore instead.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import hashlib
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from .security import scan_for_threats as _scan_for_threats

try:
    from .nexus_local import get_client as _get_llm_client
    _HAS_LOCAL_LLM = True
except Exception:
    _HAS_LOCAL_LLM = False
    _get_llm_client = lambda: None

from .nexus_utils import (
    CONTENT_WHITESPACE,
    content_hash,
    empty_scores,
    generate_summary,
    incr_score,
    max_domain,
    segment_fts,
)

# Backward compatibility aliases
_CONTENT_WHITESPACE = CONTENT_WHITESPACE
_normalize = lambda text: CONTENT_WHITESPACE.sub(' ', text).strip().lower()
_content_hash = content_hash
_empty_scores = empty_scores
_generate_summary = generate_summary
_incr_score = incr_score
_max_domain = max_domain
_segment_fts = segment_fts



class WriteMixin:
    """Mixin providing write, belief initialization, llm enhancement, feedback, promotion/demotion, conflict detection."""

    def write(self, content: str, user_id: str = "default",
              source_session_id: str = "", source_snippet: str = "",
              skip_conflict_detection: bool = False,
              event_time: Optional[str] = None,
              initial_confidence: Optional[float] = None) -> Dict[str, Any]:
        """Write a knowledge entry. Auto-dedup by match_hash.

        Args:
            skip_conflict_detection: If True, skip _detect_conflicts.
                Used by sync_turn (batch detection in consolidate instead).
            initial_confidence: Override default belief confidence (0.40).
                LLM-extracted knowledge uses 0.25-0.45 based on level.
        """
        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}

        mhash = _content_hash(content)
        conn = self._conn()

        # Check for existing active entry
        row = conn.execute(
            "SELECT id, positive_feedback, negative_feedback, layer FROM unified_knowledge "
            "WHERE match_hash = ? AND status = 'active' AND user_id = ?",
            (mhash, user_id)
        ).fetchone()

        if row:
            # Existing entry — update last_accessed only (NOT positive_feedback)
            # feedback only changes on explicit user confirmation
            self._save_version(row["id"], "re-encountered", user_id)
            conn.execute(
                "UPDATE unified_knowledge SET last_accessed = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), row["id"])
            )
            conn.commit()

            # Belief reinforcement
            try:
                from .nexus_belief import BeliefEngine
                BeliefEngine(conn).on_encounter(row["id"])
            except Exception:
                pass

            return {
                "success": True, "action": "updated_existing",
                "id": row["id"], "layer": row["layer"]
            }

        # ── Write-time merge (knowledge evolution) ──────────────
        if not skip_conflict_detection:
            try:
                from .nexus_evolve import evolve_on_write
                try:
                    from .nexus_embedder import get_embedder
                    _embedder = get_embedder()
                except Exception:
                    _embedder = None
                logger.debug("Write-time merge embedder: %s available=%s",
                             type(_embedder).__name__ if _embedder else "None",
                             getattr(_embedder, 'available', 'N/A'))
                merge = evolve_on_write(content, user_id, conn, embedder=_embedder)
                if merge["action"] == "exact_dup":
                    # Already handled above, but safety catch
                    return {
                        "success": True, "action": "updated_existing",
                        "id": merge["target_id"]
                    }
                if merge["action"] in ("fuzzy_dup", "complement"):
                    # evolve_on_write already mutated the DB
                    return {
                        "success": True, "action": merge["action"],
                        "id": merge["target_id"]
                    }
            except Exception:
                pass

        # New entry
        et = event_time or datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO unified_knowledge
               (content, domain_scores, layer, match_hash,
                source_session_id, source_snippet, user_id, event_time)
               VALUES (?, ?, 'instant', ?, ?, ?, ?, ?)""",
            (content, json.dumps(_empty_scores()), mhash,
             source_session_id, source_snippet, user_id, et)
        )
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Write jieba-segmented content directly to FTS5 index.
        # Skip 'delete' — it requires a pre-existing FTS row (from the
        # content= trigger pattern), but we handle FTS entirely in code.
        seg_content = _segment_fts(content)
        conn.execute(
            "INSERT INTO knowledge_fts(rowid, content) VALUES (?, ?)",
            (new_id, seg_content)
        )
        conn.commit()
        # Run conflict detection for new entries (non-blocking)
        if not skip_conflict_detection:
            try:
                self._detect_conflicts(new_id, content, user_id)
            except Exception:
                pass

        # ── AI summary + embedding (optional, non-blocking) ──
        try:
            self._enhance_with_local_llm(new_id, content, user_id)
        except Exception:
            pass

        # ── Entity graph linking (optional, non-blocking) ──
        try:
            if len(content) > 20:  # 短内容没必要建图
                from .nexus_graph import EntityGraph
                eg = EntityGraph(conn)
                eg.extract_and_link(new_id, content)
        except Exception:
            pass

        # ── Belief initialization ──
        self._init_belief(new_id, initial_confidence or 0.40)

        return {"success": True, "action": "created", "id": new_id, "layer": "instant"}

    def _init_belief(self, knowledge_id: int, initial_confidence: float = 0.40) -> None:
        """Initialize belief record for a new knowledge entry (non-blocking)."""
        try:
            from .nexus_belief import BeliefEngine
            be = BeliefEngine(self._conn())
            be.init_belief(knowledge_id, initial_confidence)
        except Exception:
            pass

    # ── AI enhancement (optional, backed by local Ollama) ──

    def _enhance_with_local_llm(self, entry_id: int, content: str,
                                 user_id: str = "default") -> None:
        """Generate AI summary + embedding for a new entry.

        Embedding uses fastembed (local ONNX) as primary engine.
        AI summary uses Ollama on Windows if available.
        Both are best-effort: failures are silently ignored.
        """
        conn = self._conn()

        # 1. Embedding (for semantic search) — fastembed primary, Ollama fallback
        try:
            from .nexus_embedder import get_embedder
            embedder = get_embedder()
            if embedder.available:
                vec = embedder.embed(content)
                embed_dim = len(vec) if vec else 0
            else:
                vec = None
                embed_dim = 0

            # Fallback: Ollama embedding (if fastembed unavailable)
            if not vec and _HAS_LOCAL_LLM:
                client = _get_llm_client()
                if client and client.ping():
                    ollama_vec = client.embed(content)
                    if ollama_vec and len(ollama_vec) == 768:
                        vec = ollama_vec
                        embed_dim = 768

            if vec and embed_dim > 0:
                import struct
                blob = struct.pack(f"{embed_dim}f", *vec)
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS knowledge_embeddings ("
                    "  entry_id INTEGER PRIMARY KEY,"
                    "  embedding BLOB,"
                    "  embed_dim INTEGER DEFAULT 512,"
                    "  updated_at TIMESTAMP"
                    ")"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO knowledge_embeddings "
                    "(entry_id, embedding, embed_dim, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (entry_id, blob, embed_dim, datetime.now(timezone.utc).isoformat())
                )
                conn.commit()
                logger.debug("Nexus: embedding saved for entry %d (dim=%d)", entry_id, embed_dim)
        except Exception:
            pass

        # 2. AI summary (Ollama only — fastembed doesn't do summaries)
        if not _HAS_LOCAL_LLM:
            return
        client = _get_llm_client()
        if not client or not client.ping():
            return
        try:
            summary = client.summarize(content, max_length=200)
            if summary and len(summary) > 10:
                conn.execute(
                    "UPDATE unified_knowledge SET active_summary = ? WHERE id = ?",
                    (summary.strip(), entry_id)
                )
                conn.commit()
                logger.debug("Nexus: AI summary saved for entry %d", entry_id)
        except Exception:
            pass

    def _search_semantic(self, query: str, user_id: str = "default",
                          limit: int = 5) -> List[Dict[str, Any]]:
        """Vector similarity search using fastembed (primary) or Ollama (fallback).

        Procedure:
          1. Get query embedding (fastembed → Ollama)
          2. Load all stored embeddings
          3. Cosine similarity → top K
          4. Update domain scores on matched entries
        """
        # Step 1: Get query embedding
        q_embed = None
        embed_dim = 0

        # Primary: fastembed
        try:
            from .nexus_embedder import get_embedder
            embedder = get_embedder()
            if embedder.available:
                q_embed = embedder.embed(query)
                embed_dim = len(q_embed) if q_embed else 0
        except Exception:
            pass

        # Fallback: Ollama
        if not q_embed and _HAS_LOCAL_LLM:
            try:
                client = _get_llm_client()
                if client and client.ping():
                    q_embed = client.embed(query)
                    embed_dim = 768 if q_embed and len(q_embed) == 768 else 0
            except Exception:
                pass

        if not q_embed or embed_dim == 0:
            return []

        # ── HNSW 加速的近似最近邻搜索 ────────────────
        try:
            from .nexus_hnsw import HNSWIndex
            hnsw = HNSWIndex(self._conn(), dim=embed_dim)
            hnsw.build()
            if hnsw.available:
                hnsw_results = hnsw.search(q_embed, k=limit)
                if hnsw_results:
                    scores = {}
                    for eid, sim in hnsw_results:
                        scores[eid] = sim
                    # 按相似度读取实际条目
                    placeholders = ",".join("?" for _ in range(len(scores)))
                    rows = self._conn().execute(
                        f"""SELECT uk.id, uk.content, uk.domain_scores, uk.layer,
                                  uk.positive_feedback, uk.negative_feedback,
                                  uk.active_summary, uk.user_id
                           FROM unified_knowledge uk
                           WHERE uk.id IN ({placeholders})
                             AND uk.status = 'active'
                             AND (uk.user_id = ? OR uk.user_id = 'default')
                           ORDER BY CASE uk.id
                             {' '.join(f'WHEN ? THEN {i}' for i, eid in enumerate(scores))}
                           END
                           LIMIT ?""",
                        list(scores.keys()) + [user_id] + list(scores.keys()) + [limit]
                    ).fetchall()

                    results = []
                    for r in rows:
                        item = dict(r)
                        item["similarity"] = scores.get(r["id"], 0.0)
                        try:
                            item["domain_scores"] = json.loads(item["domain_scores"])
                        except (json.JSONDecodeError, TypeError):
                            item["domain_scores"] = {}
                        item["_source"] = "hnsw"
                        results.append(item)

                    self._update_domain_scores(results, user_id)
                    return results
        except Exception:
            pass

        # ── 降级: 线性扫描（HNSW 不可用时） ──────────
        try:
            import math
            import struct
            conn = self._conn()

            # Load all embeddings
            rows = conn.execute(
                "SELECT ke.entry_id, ke.embedding, ke.embed_dim, "
                "  uk.id, uk.content, "
                "  uk.domain_scores, uk.layer, uk.positive_feedback, "
                "  uk.negative_feedback, uk.active_summary "
                "FROM knowledge_embeddings ke "
                "JOIN unified_knowledge uk ON ke.entry_id = uk.id "
                "WHERE uk.status = 'active' "
                "  AND (uk.user_id = ? OR uk.user_id = 'default')",
                (user_id,)
            ).fetchall()

            if not rows:
                return []

            # Compute cosine similarity
            q_norm = math.sqrt(sum(v * v for v in q_embed))
            if q_norm == 0:
                return []

            scored = []
            for row in rows:
                raw = row["embedding"]
                dim = row["embed_dim"] or embed_dim
                try:
                    vec = struct.unpack(f"{dim}f", raw)
                except Exception:
                    continue
                dot = sum(a * b for a, b in zip(q_embed, vec))
                v_norm = math.sqrt(sum(v * v for v in vec))
                if v_norm == 0:
                    continue
                score = dot / (q_norm * v_norm)
                item = dict(row)
                item["similarity"] = score
                try:
                    item["domain_scores"] = json.loads(item["domain_scores"])
                except Exception:
                    item["domain_scores"] = {}
                scored.append(item)

            # Sort by similarity
            scored.sort(key=lambda x: -x["similarity"])
            results = scored[:limit]

            # Update domain scores
            self._update_domain_scores(results, user_id)
            return results

        except Exception as e:
            logger.debug("Nexus: semantic search failed: %s", e)
            return []

    # -- Feedback -------------------------------------------------------------

    def feedback(self, knowledge_id: int, feedback_type: str,
                 session_id: str = "", user_id: str = "default",
                 source: str = "") -> Dict[str, Any]:
        """Record explicit positive/negative feedback from user."""
        valid_types = {'explicit_positive', 'explicit_negative', 'correction', 'system_conflict'}
        if feedback_type not in valid_types:
            return {"success": False, "error": f"Invalid feedback_type: {feedback_type}"}

        conn = self._conn()

        # Check entry exists
        row = conn.execute(
            "SELECT id, positive_feedback, negative_feedback FROM unified_knowledge WHERE id = ?",
            (knowledge_id,)
        ).fetchone()
        if not row:
            return {"success": False, "error": f"Knowledge {knowledge_id} not found."}

        # Update counter
        if feedback_type == 'explicit_positive' or feedback_type == 'correction':
            self._save_version(knowledge_id, f"feedback_{feedback_type}", user_id)
            conn.execute(
                "UPDATE unified_knowledge SET positive_feedback = positive_feedback + 1, "
                "last_accessed = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), knowledge_id)
            )
        elif feedback_type == 'explicit_negative':
            self._save_version(knowledge_id, f"feedback_{feedback_type}", user_id)
            conn.execute(
                "UPDATE unified_knowledge SET negative_feedback = negative_feedback + 1, "
                "last_accessed = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), knowledge_id)
            )

        # Log feedback
        conn.execute(
            """INSERT INTO feedback_log
               (knowledge_id, feedback_type, session_id, user_id, source)
               VALUES (?, ?, ?, ?, ?)""",
            (knowledge_id, feedback_type, session_id, user_id, source)
        )
        conn.commit()

        # Check if this triggers promotion/demotion
        self._check_promotion(knowledge_id, user_id)

        return {"success": True, "new_feedback": feedback_type}

    def _check_promotion(self, knowledge_id: int, user_id: str):
        """Evaluate whether this entry should be promoted or demoted."""
        conn = self._conn()
        row = conn.execute(
            "SELECT id, layer, positive_feedback, negative_feedback, status, content "
            "FROM unified_knowledge WHERE id = ?", (knowledge_id,)
        ).fetchone()
        if not row or row["status"] != 'active':
            return

        weight = row["positive_feedback"] - (row["negative_feedback"] * 2)
        current_layer = row["layer"]

        if weight >= 5 and current_layer == 'instant':
            self._save_version(knowledge_id, "promoted_to_candidate", user_id)
            conn.execute(
                "UPDATE unified_knowledge SET layer = 'candidate', updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), knowledge_id)
            )
            conn.commit()
            logger.info("Nexus: knowledge %d promoted to candidate (weight=%d)", knowledge_id, weight)

        elif weight >= 8 and current_layer == 'candidate':
            self._save_version(knowledge_id, "promoted_to_consolidated", user_id)
            summary = _generate_summary(row["content"])
            conn.execute(
                """UPDATE unified_knowledge
                   SET layer = 'consolidated', updated_at = ?, active_summary = ?
                   WHERE id = ?""",
                (datetime.now(timezone.utc).isoformat(), summary, knowledge_id)
            )
            conn.commit()
            logger.info("Nexus: knowledge %d promoted to consolidated (weight=%d, summary=%s)",
                        knowledge_id, weight, summary[:60])

        elif weight <= -3 and current_layer in ('instant', 'candidate', 'consolidated'):
            # Demote
            self._demote(knowledge_id, "negative_feedback", user_id)

    def _demote(self, knowledge_id: int, reason: str, user_id: str):
        conn = self._conn()
        row = conn.execute(
            "SELECT id, layer, content FROM unified_knowledge WHERE id = ?",
            (knowledge_id,)
        ).fetchone()
        if not row:
            return

        # Save version before demoting
        self._save_version(knowledge_id, f"demoted_{reason}", user_id)

        if row["layer"] == 'consolidated':
            conn.execute(
                "UPDATE unified_knowledge SET layer = 'candidate', updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), knowledge_id)
            )
        else:
            conn.execute(
                "UPDATE unified_knowledge SET status = 'superseded', updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), knowledge_id)
            )
        conn.commit()
        logger.info("Nexus: knowledge %d demoted (%s)", knowledge_id, reason)

    # -- Conflict detection ----------------------------------------------------

    _METRIC_RE = re.compile(
        r'(Sharpe|胜率|平均收益|最大回撤|交易次数|'
        r'年化收益|夏普|信息比|Calmar|收益波动比|'
        r'PE|PB|ROE|ROA|'
        r'毛利率|净利率|营收增长|净利润增长|'
        r'资产负债率|流动比率|速动比率|'
        r'每股收益|净资产|总资产|总负债|'
        r'营业收入|净利润|现金流|股息率|'
        r'Beta|Alpha|R[²2]|跟踪误差|'
        r'市值|市盈率|市净率|市销率)'
        r'\s*[:：]?\s*([+-]?\d+\.?\d*)%?'
        r'(?:\s*(亿|万|元|倍))?'
    )
    _ENTITY_RE = re.compile(
        r'(?:策略|股票|代码|ETF|板块|行业|'
        r'公司|企业|基金|指数|组合|'
        r'产品|标的|品种|合约)'
        r'\s*[:：]\s*(.+?)(?:\n|$)'
    )
    _CONDITION_RE = re.compile(
        r'(?:在|当|如果|若|条件|适用于|有效于|'
        r'限|仅限|需|须|注意|警告|谨慎)'
        r'[：:]?\s*(.+?)(?:\n|$|。|；)'
    )

    @staticmethod
    @staticmethod

    def _extract_metrics(content: str) -> List[tuple]:
        """Extract (entity_name, metric_name, value_float, condition_text) from structured content.

        Condition is the applicability context (e.g. '震荡市场', '趋势市场').
        """
        results = []
        entity_match = WriteMixin._ENTITY_RE.search(content)
        entity = entity_match.group(1).strip() if entity_match else None
        if not entity:
            return results

        # Extract condition from the same entry
        cond_match = WriteMixin._CONDITION_RE.search(content)
        condition = cond_match.group(1).strip() if cond_match else ""

        for m in WriteMixin._METRIC_RE.finditer(content):
            metric = m.group(1)
            try:
                val = float(m.group(2))
                results.append((entity, metric, val, condition))
            except ValueError:
                continue
        return results

    def _detect_conflicts(self, new_id: int, content: str, user_id: str):
        """Compare new entry's metrics against all existing active entries.

        Respects conditions: only flag as conflict if both entries apply
        under the same condition (or no condition is specified).
        Write system_conflict feedback for conflicting values.
        """
        metrics = self._extract_metrics(content)
        if not metrics:
            return

        conn = self._conn()
        all_rows = conn.execute(
            "SELECT id, content FROM unified_knowledge "
            "WHERE id != ? AND status = 'active' AND (user_id = ? OR user_id = 'default')",
            (new_id, user_id)
        ).fetchall()

        for row in all_rows:
            existing_metrics = self._extract_metrics(row["content"])
            for entity, metric, new_val, new_cond in metrics:
                for e_entity, e_metric, e_val, e_cond in existing_metrics:
                    if entity != e_entity or metric != e_metric:
                        continue
                    if abs(new_val - e_val) < 0.01:
                        continue  # Same value — not a conflict

                    # Condition overlap check: skip if conditions are
                    # explicitly different and non-overlapping
                    if new_cond and e_cond:
                        conds_overlap = (
                            new_cond in e_cond or e_cond in new_cond
                            or new_cond[:4] == e_cond[:4]  # Same start = likely same context
                        )
                        if not conds_overlap:
                            logger.debug(
                                "Nexus: skip conflict — conditions don't overlap: "
                                "'%s' vs '%s'", new_cond, e_cond
                            )
                            continue

                    # Real conflict — log as system_conflict
                    self.feedback(
                        new_id, "system_conflict",
                        session_id="conflict_detector",
                        user_id=user_id,
                        source=f"auto: {entity} {metric}: {new_val} vs {e_val} (entry {row['id']})"
                    )

                    # Auto-supersede: 新内容默认更可信（用户最近说的）
                    new_row = conn.execute(
                        "SELECT created_at, positive_feedback FROM unified_knowledge WHERE id=?",
                        (new_id,)
                    ).fetchone()
                    old_row = conn.execute(
                        "SELECT created_at, positive_feedback FROM unified_knowledge WHERE id=?",
                        (row["id"],)
                    ).fetchone()

                    new_weight = new_row["positive_feedback"] if new_row else 0
                    old_weight = old_row["positive_feedback"] if old_row else 0

                    if new_weight >= old_weight:
                        # 新内容取代旧内容
                        try:
                            self.supersede_fact(row["id"], content, user_id)
                            logger.info("Nexus: auto-superseded %d → %d", row["id"], new_id)
                        except Exception as e:
                            logger.debug("Nexus: supersede failed: %s", e)
                    else:
                        # 旧内容更可信，标记新内容为 conflict
                        conn.execute(
                            "UPDATE unified_knowledge SET status='conflict' WHERE id=?",
                            (new_id,)
                        )
                    conn.commit()
                    return  # One alert per write is enough

    def _validate_by_layer(self, user_id: str = "default") -> Dict[str, Any]:
        """Cross-validate candidate & consolidated entries for silent conflicts.

        Called during sleep-time compute. Returns conflict summary.
        """
        conn = self._conn()
        rows = conn.execute(
            "SELECT id, content FROM unified_knowledge "
            "WHERE status = 'active' AND layer IN ('candidate', 'consolidated') "
            "AND (user_id = ? OR user_id = 'default')",
            (user_id,)
        ).fetchall()

        conflicts_found = 0
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                existing_metrics_i = self._extract_metrics(rows[i]["content"])
                existing_metrics_j = self._extract_metrics(rows[j]["content"])
                if not existing_metrics_i or not existing_metrics_j:
                    continue

                for _, metric_i, val_i, cond_i in existing_metrics_i:
                    for _, metric_j, val_j, cond_j in existing_metrics_j:
                        if metric_i != metric_j:
                            continue
                        if abs(val_i - val_j) < 0.01:
                            continue

                        # Condition overlap
                        if cond_i and cond_j:
                            conds_overlap = (
                                cond_i in cond_j or cond_j in cond_i
                                or cond_i[:4] == cond_j[:4]
                            )
                            if not conds_overlap:
                                continue

                        # Conflict confirmed in persisted layer
                        self.feedback(
                            rows[i]["id"], "system_conflict",
                            session_id="layer_validation",
                            user_id=user_id,
                            source=f"layer_validate: {metric_i}: {val_i} vs {val_j} "
                                   f"(entry {rows[i]['id']} vs {rows[j]['id']})"
                        )
                        conflicts_found += 1

        return {"conflicts_found": conflicts_found}

    def _get_coldstart_stats(self, user_id: str = "default") -> Dict[str, Any]:
        """Return daily stats for cold start experience."""
        conn = self._conn()

        total = conn.execute(
            "SELECT COUNT(*) FROM unified_knowledge "
            "WHERE status = 'active' AND (user_id = ? OR user_id = 'default')",
            (user_id,)
        ).fetchone()[0]

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_count = conn.execute(
            "SELECT COUNT(*) FROM unified_knowledge "
            "WHERE DATE(created_at) = ? AND (user_id = ? OR user_id = 'default')",
            (today, user_id)
        ).fetchone()[0]

        first_entry = conn.execute(
            "SELECT MIN(DATE(created_at)) FROM unified_knowledge "
            "WHERE user_id = ? OR user_id = 'default'",
            (user_id,)
        ).fetchone()[0]

        patterns = conn.execute(
            "SELECT COUNT(*) FROM knowledge_versions WHERE change_reason = 'merge'"
        ).fetchone()[0]

        conflicts = conn.execute(
            "SELECT COUNT(*) FROM feedback_log WHERE feedback_type = 'system_conflict'"
        ).fetchone()[0]

        days_active = 0
        if first_entry:
            from datetime import timezone as tz
            first_dt = datetime.strptime(first_entry, "%Y-%m-%d").replace(tzinfo=tz.utc)
            delta = datetime.now(timezone.utc) - first_dt
            days_active = delta.days + 1

        return {
            "total_entries": total,
            "today_entries": today_count,
            "days_active": days_active,
            "patterns_found": patterns,
            "pending_conflicts": conflicts
        }

    # -- Search ---------------------------------------------------------------
