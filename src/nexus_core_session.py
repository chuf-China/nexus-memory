"""
nexus_core_session.py — Cross-session identity resolution and alias management.

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


class SessionMixin:
    """Mixin providing cross-session identity resolution and alias management."""

    def _init_session_resolver(self, conn):
        """Create user_fingerprints table for cross-session identity tracking."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_fingerprints (
                fingerprint  TEXT PRIMARY KEY,
                user_id      TEXT NOT NULL,
                platform     TEXT DEFAULT '',
                hostname     TEXT DEFAULT '',
                first_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                hit_count    INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_uf_user ON user_fingerprints(user_id)
        """)
        conn.commit()

    def resolve_session(self, session_id: str = "",
                         platform: str = "",
                         hostname: str = "") -> str:
        """Resolve a session to a known user_id using fingerprint matching.

        Strategy:
          1. Compute fingerprint from (session_id + platform + hostname)
          2. If fingerprint exists → return linked user_id, update last_seen
          3. If not → look for fingerprint with matching hostname+platform
             (same computer, different session)
          4. If match found → link new fingerprint to same user
          5. If nothing → create new fingerprint with anonymous user_id

        Returns: resolved user_id (always a string)
        """
        conn = self._conn()

        # Compute fingerprint
        fp_material = f"{session_id}|{platform}|{hostname}"
        fingerprint = hashlib.sha256(fp_material.encode()).hexdigest()[:16]

        # 1. Exact fingerprint match
        row = conn.execute(
            "SELECT user_id FROM user_fingerprints WHERE fingerprint = ?",
            (fingerprint,)
        ).fetchone()
        if row:
            # Update last_seen
            conn.execute(
                "UPDATE user_fingerprints SET last_seen = datetime('now'), "
                "hit_count = MIN(hit_count + 1, 10000) WHERE fingerprint = ?",
                (fingerprint,)
            )
            conn.commit()
            return row["user_id"]

        # 2. Same hostname+platform → same user
        if hostname:
            match = conn.execute(
                "SELECT user_id FROM user_fingerprints "
                "WHERE hostname = ? AND platform = ? "
                "ORDER BY hit_count DESC LIMIT 1",
                (hostname, platform)
            ).fetchone()
            if match:
                resolved_user = match["user_id"]
                # Link new fingerprint to same user
                conn.execute(
                    "INSERT OR IGNORE INTO user_fingerprints "
                    "(fingerprint, user_id, platform, hostname) "
                    "VALUES (?, ?, ?, ?)",
                    (fingerprint, resolved_user, platform, hostname)
                )
                conn.commit()
                return resolved_user

        # 3. No match → create new anonymous user
        import uuid
        new_user = f"anon_{uuid.uuid4().hex[:8]}"
        try:
            conn.execute(
                "INSERT INTO user_fingerprints "
                "(fingerprint, user_id, platform, hostname) "
                "VALUES (?, ?, ?, ?)",
                (fingerprint, new_user, platform, hostname)
            )
            conn.commit()
        except Exception:
            # Race condition: another thread created it
            row = conn.execute(
                "SELECT user_id FROM user_fingerprints WHERE fingerprint = ?",
                (fingerprint,)
            ).fetchone()
            if row:
                return row["user_id"]
        return new_user

    # ── Temporal knowledge graph ─────────────────────────────
