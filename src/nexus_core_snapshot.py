"""
nexus_core_snapshot.py — Point-in-time knowledge and audit snapshots.

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


class SnapshotMixin:
    """Mixin providing point-in-time knowledge and audit snapshots."""

    def knowledge_snapshot(self, at_time: str,
                           user_id: str = "default",
                           limit: int = 20) -> List[Dict[str, Any]]:
        """Reconstruct Nexus knowledge state at a point in time.

        Returns all knowledge entries that were active at `at_time`:
          - event_time <= at_time (the fact existed by then)
          - AND (valid_to IS NULL OR valid_to > at_time) (not yet superseded)

        Args:
            at_time: ISO 8601 timestamp string
        """
        conn = self._conn()

        # Check if event_time column exists (added via migration, may not exist in fresh DBs)
        has_et = False
        try:
            conn.execute("SELECT event_time FROM unified_knowledge LIMIT 0")
            has_et = True
        except Exception:
            pass

        if has_et:
            rows = conn.execute(
                """SELECT uk.id, uk.content, uk.domain_scores, uk.layer,
                          uk.positive_feedback, uk.negative_feedback,
                          uk.active_summary, uk.user_id,
                          uk.event_time, uk.valid_from, uk.valid_to,
                          uk.created_at
                   FROM unified_knowledge uk
                   WHERE uk.status IN ('active', 'superseded')
                     AND (uk.event_time IS NULL OR uk.event_time <= ?)
                     AND (uk.valid_from IS NULL OR uk.valid_from <= ?)
                     AND (uk.valid_to IS NULL OR uk.valid_to > ?)
                     AND (uk.user_id = ? OR uk.user_id = 'default')
                   ORDER BY uk.event_time ASC, uk.created_at ASC
                   LIMIT ?""",
                (at_time, at_time, at_time, user_id, limit)
            ).fetchall()
        else:
            # Fallback: no event_time column, use created_at as proxy
            rows = conn.execute(
                """SELECT uk.id, uk.content, uk.domain_scores, uk.layer,
                          uk.positive_feedback, uk.negative_feedback,
                          uk.active_summary, uk.user_id,
                          uk.created_at as event_time,
                          uk.valid_from, uk.valid_to,
                          uk.created_at
                   FROM unified_knowledge uk
                   WHERE uk.status IN ('active', 'superseded')
                     AND (uk.created_at <= ?)
                     AND (uk.valid_from IS NULL OR uk.valid_from <= ?)
                     AND (uk.valid_to IS NULL OR uk.valid_to > ?)
                     AND (uk.user_id = ? OR uk.user_id = 'default')
                   ORDER BY uk.created_at ASC
                   LIMIT ?""",
                (at_time, at_time, at_time, user_id, limit)
            ).fetchall()

        results = []
        for r in rows:
            item = dict(r)
            try:
                item["domain_scores"] = json.loads(item["domain_scores"])
            except (json.JSONDecodeError, TypeError):
                item["domain_scores"] = {}
            results.append(item)
        return results

    def audit_snapshot(self, at_time: str,
                        user_id: str = "default") -> Dict[str, Any]:
        """Generate an audit snapshot at a specific point in time.

        Shows: knowledge state, interactions up to that point, correction rate.
        Useful for tracing "what did the system know at time X" for compliance.
        """
        knowledge = self.knowledge_snapshot(at_time, user_id)

        conn = self._conn()

        # Check if event_time column exists on interaction_log
        has_il_et = False
        try:
            conn.execute("SELECT event_time FROM interaction_log LIMIT 0")
            has_il_et = True
        except Exception:
            pass

        if has_il_et:
            interactions = conn.execute(
                """SELECT id, session_id, user_query, model_response,
                          correction_of, created_at, event_time
                   FROM interaction_log
                   WHERE user_id = ?
                     AND (event_time IS NULL OR event_time <= ?)
                   ORDER BY event_time ASC, created_at ASC
                   LIMIT 200""",
                (user_id, at_time)
            ).fetchall()
        else:
            interactions = conn.execute(
                """SELECT id, session_id, user_query, model_response,
                          correction_of, created_at,
                          created_at as event_time
                   FROM interaction_log
                   WHERE user_id = ?
                     AND (created_at <= ?)
                   ORDER BY created_at ASC
                   LIMIT 200""",
                (user_id, at_time)
            ).fetchall()

        total = len(interactions)
        corrections = sum(1 for r in interactions if r["correction_of"] is not None)

        return {
            "snapshot_at": at_time,
            "active_knowledge_count": len(knowledge),
            "active_knowledge": [
                {"id": k["id"], "content": k["content"][:100],
                 "layer": k["layer"]}
                for k in knowledge
            ],
            "interaction_count": total,
            "correction_count": corrections,
            "correction_rate": round(corrections / total, 3) if total > 0 else 0,
            "earliest_interaction": interactions[0]["created_at"] if interactions else None,
            "latest_interaction": interactions[-1]["created_at"] if interactions else None,
        }

    # -- Write ----------------------------------------------------------------
