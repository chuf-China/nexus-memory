"""
nexus_core_search_ext.py — Search with domain scoring, context building, domain-based retrieval.

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



class SearchExtMixin:
    """Mixin providing search with domain scoring, context building, domain-based retrieval."""

    def search(self, query: str, user_id: str = "default",
               limit: int = 5, mode: str = "fts") -> List[Dict[str, Any]]:
        """Search knowledge entries.

        Modes:
          'fts' (default): FTS5 MATCH + LIKE %% fallback.
          'semantic': Vector similarity via fastembed/Ollama
                      (falls back to FTS if unavailable).
          'graph': Entity-relationship graph traversal
                   (finds entries linked via shared entities).
          'hybrid': All three strategies merged + reranked.
        """
        results: List[Dict[str, Any]] = []
        conn = self._conn()

        if mode in ("semantic", "hybrid"):
            semantic_results = self._search_semantic(query, user_id, limit * 2)
            if semantic_results:
                results.extend(semantic_results)

        if mode in ("graph", "hybrid"):
            try:
                from .nexus_graph import EntityGraph
                eg = EntityGraph(self._conn())
                graph_results = eg.search_by_graph(query, limit=limit * 2)
                for r in graph_results:
                    r["_source"] = "graph"
                    # graph results use entry_id; normalize to id for
                    # downstream consumers (_update_domain_scores etc.)
                    if "id" not in r and "entry_id" in r:
                        r["id"] = r["entry_id"]
                results.extend(graph_results)
            except Exception:
                pass

        if mode == "fts" or (mode in ("hybrid", "semantic") and not results):
            # FTS5 search always available as fallback
            pass  # fall through to FTS below

        if mode == "fts" or (mode in ("hybrid", "semantic") and len(results) < limit) or mode not in ("semantic", "graph", "hybrid"):
            clean = _CONTENT_WHITESPACE.sub(' ', query).strip()
        else:
            clean = ""
        if not clean:
            return results if results else []

        # Segment query for FTS5
        seg_query = _segment_fts(clean)

        fts_results = []

        # ── Primary: FTS5 MATCH ──
        try:
            rows = conn.execute(
                """SELECT uk.id, uk.content, uk.domain_scores, uk.layer,
                          uk.positive_feedback, uk.negative_feedback,
                          uk.active_summary, uk.user_id
                   FROM unified_knowledge uk
                   JOIN knowledge_fts kfts ON uk.id = kfts.rowid
                   WHERE kfts.content MATCH ?
                     AND uk.status = 'active'
                     AND (uk.user_id = ? OR uk.user_id = 'default')
                   ORDER BY rank
                   LIMIT ?""",
                (seg_query, user_id, limit)
            ).fetchall()

            for row in rows:
                item = dict(row)
                try:
                    item["domain_scores"] = json.loads(item["domain_scores"])
                except (json.JSONDecodeError, TypeError):
                    item["domain_scores"] = {}
                fts_results.append(item)

            logger.debug("Nexus search: FTS5 returned %d results for '%s'", len(fts_results), clean)
        except Exception as fts_err:
            logger.debug("Nexus search: FTS5 MATCH failed, falling back to LIKE: %s", fts_err)
            fts_results = []

        # ── Fallback: LIKE %% (zero results from FTS5 or FTS5 error) ──
        if not fts_results:
            try:
                # 按词拆分 OR 匹配：中文整句 LIKE 几乎永不命中，
                # 拆词后任一 token 命中即返回（fts 与 semantic 兜底共用）
                tokens = [t for t in seg_query.split() if t]
                like_clause = " OR ".join(["uk.content LIKE ?"] * len(tokens)) if tokens else "1=0"
                rows = conn.execute(
                    f"""SELECT uk.id, uk.content, uk.domain_scores, uk.layer,
                              uk.positive_feedback, uk.negative_feedback,
                              uk.active_summary, uk.user_id
                       FROM unified_knowledge uk
                       WHERE ({like_clause})
                         AND uk.status = 'active'
                         AND (uk.user_id = ? OR uk.user_id = 'default')
                       ORDER BY (uk.positive_feedback - uk.negative_feedback * 2) DESC,
                                uk.last_accessed DESC
                       LIMIT ?""",
                    [f"%{t}%" for t in tokens] + [user_id, limit * 2]
                ).fetchall()

                for row in rows:
                    item = dict(row)
                    try:
                        item["domain_scores"] = json.loads(item["domain_scores"])
                    except (json.JSONDecodeError, TypeError):
                        item["domain_scores"] = {}
                    fts_results.append(item)

                logger.debug("Nexus search: LIKE fallback returned %d results for '%s'", len(fts_results), clean)
            except Exception as like_err:
                logger.warning("Nexus search: both FTS5 and LIKE failed: %s", like_err)
                if not results:
                    return []
                return results

        # Merge FTS results into accumulated results (dedup by id)
        seen_ids = {r.get("entry_id") or r.get("id") for r in results}
        for r in fts_results:
            rid = r.get("id")
            if rid not in seen_ids:
                seen_ids.add(rid)
                r["_source"] = "fts"
                results.append(r)

        # ── Enhanced recall: query expansion + multi-hop + negation ──
        # (only for hybrid mode — boosts recall beyond single-query FTS5)
        if mode == "hybrid":
            try:
                from .nexus_search import expand_query, is_negation_query
                from .nexus_search import extract_entities, needs_relative_time
                seen_ids = {r.get("entry_id") or r.get("id") for r in results}

                # Query expansion: synonyms + entities + keywords
                expanded = expand_query(query)
                for eq in expanded:
                    if eq == query:
                        continue
                    seg_eq = _segment_fts(_CONTENT_WHITESPACE.sub(' ', eq).strip())
                    if not seg_eq:
                        continue
                    try:
                        ex_rows = conn.execute(
                            """SELECT uk.id, uk.content, uk.domain_scores, uk.layer,
                                      uk.positive_feedback, uk.negative_feedback,
                                      uk.active_summary, uk.user_id
                               FROM unified_knowledge uk
                               JOIN knowledge_fts kfts ON uk.id = kfts.rowid
                               WHERE kfts.content MATCH ?
                                 AND uk.status = 'active'
                                 AND (uk.user_id = ? OR uk.user_id = 'default')
                               ORDER BY rank
                               LIMIT ?""",
                            (seg_eq, user_id, limit)
                        ).fetchall()
                        for row in ex_rows:
                            item = dict(row)
                            rid = item.get("id")
                            if rid not in seen_ids:
                                seen_ids.add(rid)
                                try:
                                    item["domain_scores"] = json.loads(item["domain_scores"])
                                except (json.JSONDecodeError, TypeError):
                                    item["domain_scores"] = {}
                                item["_source"] = "expanded"
                                results.append(item)
                    except Exception:
                        pass

                # Multi-hop: relative time → search entities + date
                has_rel, _ = needs_relative_time(results)
                if has_rel:
                    entities = extract_entities(query)
                    for ent in entities[:2]:
                        ent_q = f"{ent} date time when"
                        seg_ent = _segment_fts(ent_q)
                        if seg_ent:
                            try:
                                hop_rows = conn.execute(
                                    """SELECT uk.id, uk.content, uk.domain_scores, uk.layer,
                                              uk.positive_feedback, uk.negative_feedback,
                                              uk.active_summary, uk.user_id
                                       FROM unified_knowledge uk
                                       JOIN knowledge_fts kfts ON uk.id = kfts.rowid
                                       WHERE kfts.content MATCH ?
                                         AND uk.status = 'active'
                                         AND (uk.user_id = ? OR uk.user_id = 'default')
                                       ORDER BY rank
                                       LIMIT ?""",
                                    (seg_ent, user_id, 5)
                                ).fetchall()
                                for row in hop_rows:
                                    item = dict(row)
                                    rid = item.get("id")
                                    if rid not in seen_ids:
                                        seen_ids.add(rid)
                                        try:
                                            item["domain_scores"] = json.loads(item["domain_scores"])
                                        except (json.JSONDecodeError, TypeError):
                                            item["domain_scores"] = {}
                                        item["_source"] = "multi_hop"
                                        results.append(item)
                            except Exception:
                                pass

                # Negation: negated query → search without negation terms
                if is_negation_query(query):
                    from .nexus_search import _NEGATION_WORDS
                    neg_terms = _NEGATION_WORDS.sub("", query).strip()
                    if neg_terms:
                        seg_neg = _segment_fts(neg_terms)
                        if seg_neg:
                            try:
                                neg_rows = conn.execute(
                                    """SELECT uk.id, uk.content, uk.domain_scores, uk.layer,
                                              uk.positive_feedback, uk.negative_feedback,
                                              uk.active_summary, uk.user_id
                                       FROM unified_knowledge uk
                                       JOIN knowledge_fts kfts ON uk.id = kfts.rowid
                                       WHERE kfts.content MATCH ?
                                         AND uk.status = 'active'
                                         AND (uk.user_id = ? OR uk.user_id = 'default')
                                       ORDER BY rank
                                       LIMIT ?""",
                                    (seg_neg, user_id, limit)
                                ).fetchall()
                                for row in neg_rows:
                                    item = dict(row)
                                    rid = item.get("id")
                                    if rid not in seen_ids:
                                        seen_ids.add(rid)
                                        try:
                                            item["domain_scores"] = json.loads(item["domain_scores"])
                                        except (json.JSONDecodeError, TypeError):
                                            item["domain_scores"] = {}
                                        item["_source"] = "negation_hop"
                                        results.append(item)
                            except Exception:
                                pass
            except ImportError:
                pass  # nexus_search not available — skip enhanced recall

        # Update domain scores for retrieved entries
        self._update_domain_scores(results, user_id)

        # ── Rerank: cross-encoder + score fusion ──────────
        try:
            from .nexus_embedder import Reranker
            reranker = Reranker()
            results = reranker.rerank(query, results, top_k=limit)
        except Exception:
            pass

        # ── Auto record domain hit (top 3) ──────────────
        if results:
            for r in results[:3]:
                try:
                    domain = self._infer_domain(query, r)
                    self.record_domain_hit(r["id"], domain)
                except Exception:
                    pass

        # ── Add version history to results ──────────────
        for r in results:
            try:
                history = self.get_history(r["id"])
                if history and len(history) > 1:
                    r["version"] = len(history)
                    r["last_updated"] = history[-1].get("changed_at", "")[:10]
            except Exception:
                pass

        return results

    def _infer_domain(self, query: str, result: Dict) -> str:
        """从查询和结果推断领域。"""
        domain_scores = result.get("domain_scores", {})
        if isinstance(domain_scores, str):
            try:
                domain_scores = json.loads(domain_scores)
            except Exception:
                domain_scores = {}
        if domain_scores:
            return max(domain_scores, key=domain_scores.get)
        # 从 query 推断
        if any(k in query for k in ["代码", "函数", "bug", "编程"]):
            return "workflow"
        if any(k in query for k in ["喜欢", "偏好", "风格", "习惯"]):
            return "behavior"
        return "raw_fact"

    def build_context(self, results: List[Dict],
                      max_tokens: int = 2000,
                      question: str = "",
                      session_dates: Optional[List[str]] = None) -> str:
        """Build LLM-readable context from search results.

        Delegates to nexus_search.build_context_v2 for time resolution
        (relative words → absolute dates), negation annotation, and dedup.

        Args:
            results: Search results from search().
            max_tokens: Approximate token budget.
            question: Original query for negation/time detection.
            session_dates: Session timestamps for relative time resolution.

        Returns:
            Formatted context string ready for LLM injection.
        """
        try:
            from .nexus_search import build_context_v2
            return build_context_v2(
                results, max_tokens=max_tokens,
                question=question, session_dates=session_dates
            )
        except ImportError:
            # Fallback: simple concatenation
            parts = []
            for i, r in enumerate(results[:10]):
                content = r.get("content", "") or ""
                parts.append(f"[{i+1}] {content[:500]}")
            return "\n".join(parts) if parts else "[No relevant context found.]"

    def _update_domain_scores(self, results: List[Dict], user_id: str):
        """Increment domain scores for searched entries based on query context.

        Simplified: we just mark them as accessed. Full domain inference
        requires knowing the query's domain context from the agent.
        """
        conn = self._conn()
        now = datetime.now(timezone.utc).isoformat()
        for r in results:
            conn.execute(
                "UPDATE unified_knowledge SET last_accessed = ? WHERE id = ?",
                (now, r["id"])
            )
        conn.commit()

    def search_by_domain(self, domain: str, user_id: str = "default",
                         limit: int = 5) -> List[Dict[str, Any]]:
        """Search by domain score threshold."""
        conn = self._conn()
        rows = conn.execute(
            """SELECT id, content, domain_scores, layer,
                      positive_feedback, negative_feedback,
                      active_summary
               FROM unified_knowledge
               WHERE json_extract(domain_scores, '$.""" + domain + """') > 0
                 AND status = 'active'
                 AND (user_id = ? OR user_id = 'default')
               ORDER BY (positive_feedback - negative_feedback * 2) DESC
               LIMIT ?""",
            (user_id, limit)
        ).fetchall()

        results = []
        for row in rows:
            item = dict(row)
            try:
                item["domain_scores"] = json.loads(item["domain_scores"])
            except (json.JSONDecodeError, TypeError):
                item["domain_scores"] = {}
            results.append(item)
        return results

    # -- Domain score update (from agent context) -----------------------------

    def record_domain_hit(self, knowledge_id: int, domain: str):
        """Called when a knowledge entry is used in a domain context."""
        conn = self._conn()
        row = conn.execute(
            "SELECT domain_scores FROM unified_knowledge WHERE id = ?",
            (knowledge_id,)
        ).fetchone()
        if not row:
            return

        try:
            scores = json.loads(row["domain_scores"])
        except (json.JSONDecodeError, TypeError):
            scores = _empty_scores()

        scores = _incr_score(scores, domain)
        conn.execute(
            "UPDATE unified_knowledge SET domain_scores = ?, last_query_domain = ? WHERE id = ?",
            (json.dumps(scores), domain, knowledge_id)
        )
        conn.commit()

    # -- Version management ---------------------------------------------------
