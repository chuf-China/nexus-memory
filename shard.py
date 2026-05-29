"""nexus_shard.py — User-based database sharding router.

Routes queries to per-user databases for isolation.
Transparent to callers via ShardedNexusCore wrapper.

Usage:
  from .shard import ShardRouter
  router = ShardRouter(base_dir="~/.hermes/data/nexus")
  conn = router.get_conn("user_123")
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ShardRouter:
    """Routes to per-user SQLite databases.

    Layout:
      base_dir/
        default.db          — shared/default user
        user_{user_id}.db   — per-user shards
    """

    def __init__(self, base_dir: str = ""):
        self.base_dir = Path(base_dir) if base_dir else (
            Path.home() / ".hermes" / "data" / "nexus"
        )
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._conns: Dict[str, sqlite3.Connection] = {}

    def _db_path(self, user_id: str) -> Path:
        if user_id == "default":
            return self.base_dir / "default.db"
        # Sanitize user_id for filesystem
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in user_id)
        return self.base_dir / f"user_{safe}.db"

    def get_conn(self, user_id: str = "default") -> sqlite3.Connection:
        """Get or create a connection for a user."""
        key = user_id or "default"
        if key not in self._conns:
            path = self._db_path(key)
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-64000")
            self._conns[key] = conn
            logger.debug("ShardRouter: opened %s", path.name)
        return self._conns[key]

    def close(self, user_id: Optional[str] = None):
        """Close connection(s)."""
        if user_id:
            conn = self._conns.pop(user_id or "default", None)
            if conn:
                conn.close()
        else:
            for conn in self._conns.values():
                conn.close()
            self._conns.clear()

    def list_shards(self) -> Dict[str, int]:
        """List shard files and their sizes."""
        shards = {}
        for f in self.base_dir.glob("*.db"):
            shards[f.name] = f.stat().st_size
        return shards

    def merge_to_default(self, user_id: str) -> Dict[str, Any]:
        """Merge a user shard into default.db (for consolidation).

        Copies active facts and knowledge from user shard to default.
        """
        user_path = self._db_path(user_id)
        default_path = self._db_path("default")

        if not user_path.exists():
            return {"status": "error", "reason": "shard not found"}

        user_conn = sqlite3.connect(str(user_path))
        default_conn = sqlite3.connect(str(default_path))

        merged = 0
        try:
            # Merge facts
            rows = user_conn.execute(
                "SELECT * FROM facts WHERE superseded_by IS NULL"
            ).fetchall()
            for row in rows:
                try:
                    default_conn.execute(
                        "INSERT OR IGNORE INTO facts "
                        "(subject, predicate, object, confidence, source, user_id) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (row["subject"], row["predicate"], row["object"],
                         row["confidence"], row["source"], row["user_id"])
                    )
                    merged += 1
                except Exception:
                    pass
            default_conn.commit()
        except Exception as e:
            logger.error("Merge failed: %s", e)
        finally:
            user_conn.close()
            default_conn.close()

        return {"status": "ok", "merged": merged}
