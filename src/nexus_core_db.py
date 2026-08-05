"""
nexus_core_db.py — DB connection, backup, integrity check, lifecycle.

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

try:
    from tools.threat_patterns import scan_for_threats as _scan_for_threats
except ImportError:
    _scan_for_threats = None

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


class DbMixin:
    """Mixin providing db connection, backup, integrity check, lifecycle."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        self._backup()  # Backup healthy DB before init
        self._init_db()

    # -- Connection management ------------------------------------------------

    @staticmethod

    def _backup_db(path: str) -> bool:
        """Copy nexus.db → nexus.db.bak if healthy."""
        bak = path + ".bak"
        try:
            c = sqlite3.connect(path)
            cur = c.execute("PRAGMA integrity_check")
            if cur.fetchone()[0] == "ok":
                import shutil
                shutil.copy2(path, bak)
                c.close()
                return True
            c.close()
        except Exception:
            pass
        return False

    def _backup(self):
        """Public wrapper. Safe to call anytime."""
        self._backup_db(self.db_path)

    def _check_integrity(self) -> bool:
        """Return True if DB is healthy."""
        try:
            c = sqlite3.connect(self.db_path)
            cur = c.execute("PRAGMA integrity_check")
            ok = cur.fetchone()[0] == "ok"
            c.close()
            return ok
        except Exception:
            return False

    def _auto_repair(self) -> bool:
        """Try to restore from .bak if DB is corrupted. Returns True on success."""
        bak = self.db_path + ".bak"
        if not os.path.exists(bak):
            logger.error("Nexus DB corrupted and no backup found at %s", bak)
            return False
        try:
            # Check if backup itself is healthy
            c = sqlite3.connect(bak)
            cur = c.execute("PRAGMA integrity_check")
            bak_ok = cur.fetchone()[0] == "ok"
            c.close()
            if not bak_ok:
                logger.error("Nexus backup also corrupted")
                return False
            # Corrupt the current DB to force recovery
            import shutil
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.move(self.db_path, self.db_path + f".corrupted_{ts}")
            shutil.copy2(bak, self.db_path)
            logger.info("Nexus DB restored from backup (corrupted file saved as .corrupted_%s)", ts)
            # Recreate WAL
            conn = self._conn()
            conn.execute("PRAGMA journal_mode=WAL")
            conn.close()
            return True
        except Exception as e:
            logger.error("Nexus auto-repair failed: %s", e)
            return False

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.execute("PRAGMA cache_size=-64000")   # 64MB
            self._local.conn.execute("PRAGMA mmap_size=268435456")  # 256MB
        return self._local.conn

    def _init_db(self):
        """Create tables from schema.sql if not exist."""
        schema_path = Path(__file__).parent.parent / "plugins" / "memory" / "nexus" / "schema.sql"
        if not schema_path.exists():
            logger.warning("schema.sql not found at %s", schema_path)
            return

        conn = self._conn()

        # Drop stale triggers (no more knowledge_ai — handled in code)
        for trig in ('knowledge_ai', 'knowledge_ad', 'knowledge_au'):
            conn.execute(f"DROP TRIGGER IF EXISTS {trig}")

        # FTS table: use CREATE IF NOT EXISTS — no destructive drop.
        # The schema DDL recreates it only when the VIRTUAL TABLE definition
        # changes (which requires a rebuild). Normal init skips it.

        schema = schema_path.read_text()
        conn.executescript(schema)
        conn.commit()

        # Check FTS integrity: rebuild if count mismatch
        self._ensure_fts_integrity(conn)

        # Initialize cross-session resolver
        self._init_session_resolver(conn)

        # Initialize temporal tracking (idempotent migration)
        self._init_temporal_tracking(conn)

        # Initialize audit layer
        self._init_audit_layer(conn)

        # Initialize constitution (idempotent)
        self._init_constitution(conn)

    def close(self):
        if hasattr(self._local, 'conn') and self._local.conn:
            try:
                self._local.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            self._local.conn.close()
        self._backup()

    # ── Cross-session identity resolver ───────────────────────
