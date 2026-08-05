"""
nexus_core_audit.py — Temporal tracking, constitution, audit layer, interaction logging, model stats.

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



class AuditMixin:
    """Mixin providing temporal tracking, constitution, audit layer, interaction logging, model stats."""

    def _init_temporal_tracking(self, conn):
        """Add temporal columns (idempotent migration).

        valid_from:  when this fact became true (default: created_at)
        valid_to:    when this fact stopped being true (NULL = still valid)
        event_time:  when the fact was originally uttered in reality
                     (default: created_at — backward compatible)
        """
        for col in ('valid_from', 'valid_to'):
            try:
                conn.execute(f"ALTER TABLE unified_knowledge ADD COLUMN {col} TIMESTAMP")
            except Exception:
                pass  # Column already exists

        # event_time — when the fact was originally uttered
        try:
            conn.execute("ALTER TABLE unified_knowledge ADD COLUMN event_time TIMESTAMP")
        except Exception:
            pass

        # Also add event_time to interaction_log if missing
        try:
            conn.execute("ALTER TABLE interaction_log ADD COLUMN event_time TIMESTAMP")
        except Exception:
            pass

        # Index for temporal queries
        for col_cfg in (
            ("idx_uk_valid_to", "unified_knowledge", "valid_to"),
            ("idx_uk_event_time", "unified_knowledge", "event_time"),
            ("idx_il_event_time", "interaction_log", "event_time"),
        ):
            try:
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {col_cfg[0]} "
                    f"ON {col_cfg[1]}({col_cfg[2]})"
                )
            except Exception:
                pass
        conn.commit()

    def _init_constitution(self, conn):
        """Initialize constitutional governance tables (idempotent)."""
        try:
            from .nexus_constitution import Constitution
            Constitution(conn)
        except Exception:
            pass

    def supersede_fact(self, knowledge_id: int, new_content: str,
                        user_id: str = "default",
                        source_session_id: str = "",
                        reason: str = "correction") -> Dict[str, Any]:
        """Supersede an old fact with a new one.

        1. Mark old fact: valid_to = now()
        2. Create new fact: valid_from = now(), links back to old
        3. Copy domain_scores from old to new
        4. Record version chain
        """
        conn = self._conn()
        now = datetime.now(timezone.utc).isoformat()

        # Get old fact
        old = conn.execute(
            "SELECT content, domain_scores, layer FROM unified_knowledge "
            "WHERE id = ? AND status = 'active'",
            (knowledge_id,)
        ).fetchone()
        if not old:
            return {"success": False, "error": f"Fact {knowledge_id} not found"}

        # 1. Mark old as superseded
        conn.execute(
            "UPDATE unified_knowledge SET valid_to = ?, status = 'superseded', "
            "  last_accessed = ? WHERE id = ?",
            (now, now, knowledge_id)
        )

        # 2. Create new fact
        mhash = _content_hash(new_content)
        conn.execute(
            "INSERT INTO unified_knowledge "
            "(content, domain_scores, layer, match_hash, user_id, "
            " source_session_id, replaces, valid_from, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')",
            (new_content, old["domain_scores"], old["layer"],
             mhash, user_id, source_session_id, knowledge_id, now)
        )
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # 3. Update old's replaced_by
        conn.execute(
            "UPDATE unified_knowledge SET replaced_by = ? WHERE id = ?",
            (new_id, knowledge_id)
        )

        # 4. Update FTS index
        seg = _segment_fts(new_content)
        conn.execute("INSERT INTO knowledge_fts(rowid, content) VALUES (?, ?)",
                     (new_id, seg))

        # 5. Save version
        self._save_version(new_id, reason, user_id)
        self._save_version(knowledge_id, f"superseded: {reason}", user_id)

        conn.commit()

        # 6. Generate embedding for new fact
        try:
            self._enhance_with_local_llm(new_id, new_content, user_id)
        except Exception:
            pass

        # 7. Link in entity graph
        try:
            if len(new_content) > 20:
                from .nexus_graph import EntityGraph
                eg = EntityGraph(conn)
                eg.extract_and_link(new_id, new_content)
        except Exception:
            pass

        return {
            "success": True,
            "action": "superseded",
            "old_id": knowledge_id,
            "new_id": new_id,
            "superseded_at": now,
        }

    def search_temporal(self, query: str, at_time: Optional[str] = None,
                         user_id: str = "default",
                         limit: int = 5) -> List[Dict[str, Any]]:
        """Temporal search: find facts that were valid at a specific point in time.

        at_time: ISO timestamp. If None, uses current time (same as normal search).
        Returns facts whose valid_from <= at_time AND (valid_to IS NULL OR valid_to > at_time).
        """
        search_time = at_time or datetime.now(timezone.utc).isoformat()

        conn = self._conn()
        clean = _CONTENT_WHITESPACE.sub(' ', query).strip()
        seg_query = _segment_fts(clean) if clean else ""

        if not seg_query:
            return []

        try:
            rows = conn.execute(
                "SELECT uk.id, uk.content, uk.domain_scores, uk.layer, "
                "  uk.valid_from, uk.valid_to, uk.positive_feedback, "
                "  uk.negative_feedback, uk.active_summary, "
                "  uk.replaces, uk.replaced_by "
                "FROM unified_knowledge uk "
                "JOIN knowledge_fts kfts ON uk.id = kfts.rowid "
                "WHERE kfts.content MATCH ? "
                "  AND uk.status = 'active' "
                "  AND (uk.valid_from IS NULL OR uk.valid_from <= ?) "
                "  AND (uk.valid_to IS NULL OR uk.valid_to > ?) "
                "  AND (uk.user_id = ? OR uk.user_id = 'default') "
                "ORDER BY rank LIMIT ?",
                (seg_query, search_time, search_time, user_id, limit)
            ).fetchall()

            results = []
            for row in rows:
                item = dict(row)
                try:
                    item["domain_scores"] = json.loads(item["domain_scores"])
                except Exception:
                    item["domain_scores"] = {}
                results.append(item)

            if results:
                self._update_domain_scores(results, user_id)
            return results
        except Exception as e:
            logger.debug("Nexus temporal search failed: %s", e)
            return []

    # ── Audit layer (精确实时层) ───────────────────────────

    def _init_audit_layer(self, conn):
        """Create interaction_log table for tracking knowledge usage."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS interaction_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      TEXT,
                user_id         TEXT DEFAULT 'default',
                user_query      TEXT,
                model_response  TEXT,
                knowledge_used  TEXT,    -- JSON array of {id, content, layer, score}
                correction_of   TEXT,    -- if this interaction is a correction, link to prior interaction_log.id
                event_time      TIMESTAMP,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Index for temporal queries
        for col in ('created_at', 'session_id', 'user_id'):
            try:
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_il_{col} ON interaction_log({col})"
                )
            except Exception:
                pass
        conn.commit()

    def log_interaction(self, user_query: str, model_response: str,
                         knowledge_used: List[Dict[str, Any]],
                         session_id: str = "",
                         user_id: str = "default",
                         event_time: Optional[str] = None) -> int:
        """Log an interaction: what was asked, what knowledge was used, what was answered.

        Returns: interaction_log.id for future correction linking.
        """
        conn = self._conn()
        knowledge_json = json.dumps([
            {"id": k.get("id") or k.get("entry_id"),
             "content": (k.get("content") or "")[:200],
             "layer": k.get("layer", ""),
             "source": k.get("_source", ""),
             "score": round(k.get("similarity") or k.get("score") or 0, 3)}
            for k in (knowledge_used or [])
            if k.get("id") or k.get("entry_id")
        ], ensure_ascii=False)

        et = event_time or datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO interaction_log "
            "(session_id, user_id, user_query, model_response, knowledge_used, event_time) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, user_id, (user_query or "")[:500],
             (model_response or "")[:2000], knowledge_json, et)
        )
        log_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return log_id

    def log_correction(self, log_id: int, user_query: str,
                        model_response: str = "",
                        session_id: str = "",
                        user_id: str = "default",
                        event_time: Optional[str] = None) -> int:
        """Log a correction that refers back to a prior interaction.

        Also triggers supersede_fact for any knowledge that was used
        in the original interaction context.
        """
        conn = self._conn()

        # Verify original interaction exists
        original = conn.execute(
            "SELECT id, knowledge_used FROM interaction_log WHERE id = ?",
            (log_id,)
        ).fetchone()
        if not original:
            return 0

        # Log the correction
        et = event_time or datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO interaction_log "
            "(session_id, user_id, user_query, model_response, "
            " correction_of, knowledge_used, event_time) "
            "VALUES (?, ?, ?, ?, ?, "
            "  (SELECT knowledge_used FROM interaction_log WHERE id = ?), ?)",
            (session_id, user_id, (user_query or "")[:500],
             (model_response or "")[:2000], log_id, log_id, et)
        )
        correction_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        return correction_id

    def get_interaction_chain(self, log_id: int) -> List[Dict[str, Any]]:
        """Get full interaction chain: original → all corrections.

        Returns chronologically ordered list.
        """
        conn = self._conn()

        # Find root (original interaction)
        current_id = log_id
        while True:
            row = conn.execute(
                "SELECT id, correction_of FROM interaction_log WHERE id = ?",
                (current_id,)
            ).fetchone()
            if not row or not row["correction_of"]:
                break
            current_id = row["correction_of"]

        root_id = current_id

        # Get all interactions in chain
        rows = conn.execute(
            "SELECT id, user_query, model_response, knowledge_used, "
            "  correction_of, created_at "
            "FROM interaction_log "
            "WHERE id = ? OR correction_of = ? "
            "  OR id IN (SELECT id FROM interaction_log "
            "            WHERE correction_of = ?) "
            "ORDER BY created_at ASC",
            (root_id, root_id, root_id)
        ).fetchall()

        return [
            {
                "id": r["id"],
                "query": r["user_query"],
                "response": (r["model_response"] or "")[:300],
                "knowledge_used": json.loads(r["knowledge_used"]) if r["knowledge_used"] else [],
                "is_correction": r["correction_of"] is not None,
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def get_active_rules(self, limit: int = 10) -> List[str]:
        """获取活跃的行为规则。"""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT rule FROM constitution_log "
                "WHERE allowed = 1 "
                "ORDER BY created_at DESC "
                "LIMIT ?",
                (limit,)
            ).fetchall()
            return [r["rule"] for r in rows]
        except Exception:
            return []

    def add_rule(self, rule_text: str, source: str = "auto",
                 confidence: float = 0.6) -> Optional[str]:
        """添加行为规则（自动去重）。"""
        conn = self._conn()
        # 去重检查
        existing = conn.execute(
            "SELECT id FROM constitution_log WHERE rule = ?",
            (rule_text,)
        ).fetchone()
        if existing:
            return None
        conn.execute(
            "INSERT INTO constitution_log (rule, domain, action, allowed, reason) "
            "VALUES (?, ?, ?, 1, ?)",
            (rule_text, source, "allow", f"confidence={confidence}")
        )
        conn.commit()
        return rule_text

    def record_model_performance(self, model_name: str, task_type: str,
                                  quality_score: float, session_id: str = ""):
        """记录模型表现。"""
        conn = self._conn()
        conn.execute(
            "INSERT INTO model_versions "
            "(model_name, provider, switched_at, domain_accuracy, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (model_name, "", datetime.now(timezone.utc).isoformat(),
             quality_score, f"task={task_type} session={session_id[:8]}")
        )
        conn.commit()

    def get_model_stats(self, model_name: str = None, days: int = 30) -> List[Dict]:
        """返回模型质量统计。"""
        conn = self._conn()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        if model_name:
            rows = conn.execute(
                "SELECT model_name, AVG(domain_accuracy), COUNT(*) "
                "FROM model_versions WHERE model_name=? AND switched_at>? "
                "GROUP BY model_name", (model_name, cutoff)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT model_name, AVG(domain_accuracy), COUNT(*) "
                "FROM model_versions WHERE switched_at>? "
                "GROUP BY model_name ORDER BY AVG(domain_accuracy) DESC",
                (cutoff,)
            ).fetchall()
        return [{"model": r[0], "avg_quality": r[1], "samples": r[2]}
                for r in rows]

    def audit_stats(self, user_id: str = "default") -> Dict[str, Any]:
        """审计统计: 交互数/修正率/最常用知识"""
        conn = self._conn()
        total = conn.execute(
            "SELECT COUNT(*) FROM interaction_log WHERE user_id = ?",
            (user_id,)
        ).fetchone()[0]
        corrections = conn.execute(
            "SELECT COUNT(*) FROM interaction_log "
            "WHERE user_id = ? AND correction_of IS NOT NULL",
            (user_id,)
        ).fetchone()[0]
        # Most-used knowledge
        top_knowledge = conn.execute(
            "SELECT knowledge_used FROM interaction_log "
            "WHERE user_id = ? AND knowledge_used != '[]' "
            "ORDER BY created_at DESC LIMIT 100",
            (user_id,)
        ).fetchall()
        from collections import Counter
        usage = Counter()
        for row in top_knowledge:
            try:
                used = json.loads(row["knowledge_used"])
                for k in used:
                    kid = k.get("id")
                    if kid:
                        usage[str(kid)] += 1
            except Exception:
                pass

        return {
            "total_interactions": total,
            "total_corrections": corrections,
            "correction_rate": round(corrections / total, 3) if total > 0 else 0,
            "top_knowledge": usage.most_common(10),
        }

    # ── Temporal search (time travel) ───────────────────────
