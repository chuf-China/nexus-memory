"""nexus_episodes.py — Episode Store (情景记忆)

Stores raw conversation turns, enabling:
  - Original dialogue retrieval
  - Time-range queries
  - Topic-based browsing
  - Session replay

Usage:
  from .episodes import EpisodeStore
  es = EpisodeStore(conn)
  es.record(session_id, role="user", content="What is Nexus?")
  results = es.search("Nexus architecture")
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS episodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    role            TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system', 'tool')),
    content         TEXT NOT NULL,
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata        TEXT DEFAULT '{}',
    topic           TEXT,
    user_id         TEXT DEFAULT 'default'
);

CREATE INDEX IF NOT EXISTS idx_ep_session ON episodes(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_ep_topic ON episodes(topic);
CREATE INDEX IF NOT EXISTS idx_ep_time ON episodes(timestamp);
CREATE INDEX IF NOT EXISTS idx_ep_user ON episodes(user_id);
"""

# FTS on episodes content
_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    content,
    content='episodes', content_rowid='id'
);
"""

_FTS_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, content) VALUES ('delete', old.id, '');
END;
CREATE TRIGGER IF NOT EXISTS episodes_au AFTER UPDATE ON episodes
    WHEN old.content != new.content
BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, content) VALUES ('delete', old.id, '');
    INSERT INTO episodes_fts(rowid, content) VALUES (new.id, '');
END;
"""


class EpisodeStore:
    """Raw conversation memory store."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript(_SCHEMA_SQL)
        try:
            self.conn.executescript(_FTS_SQL)
            self.conn.executescript(_FTS_TRIGGER_SQL)
        except Exception:
            pass  # FTS already exists
        self.conn.commit()

    def record(self, session_id: str, role: str, content: str,
               metadata: Optional[Dict] = None,
               topic: Optional[str] = None,
               user_id: str = "default") -> int:
        """Record a single conversation turn.

        Returns: episode ID
        """
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            "INSERT INTO episodes (session_id, role, content, metadata, topic, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, role, content, json.dumps(metadata or {}), topic, user_id)
        )
        ep_id = cur.lastrowid

        # Update FTS index
        try:
            self.conn.execute(
                "INSERT INTO episodes_fts(rowid, content) VALUES (?, ?)",
                (ep_id, content)
            )
        except Exception:
            pass

        self.conn.commit()
        return ep_id

    def record_batch(self, session_id: str, turns: List[Dict],
                     user_id: str = "default") -> int:
        """Record multiple turns at once. Returns count recorded."""
        count = 0
        for turn in turns:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if content:
                self.record(session_id, role, content,
                           metadata=turn.get("metadata"),
                           topic=turn.get("topic"),
                           user_id=user_id)
                count += 1
        return count

    def get_session(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all turns in a session, ordered by time."""
        rows = self.conn.execute(
            "SELECT * FROM episodes WHERE session_id = ? ORDER BY timestamp",
            (session_id,)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def search(self, query: str, limit: int = 10,
               user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Full-text search across all episodes."""
        try:
            sql = (
                "SELECT e.*, ep.rank FROM episodes e "
                "JOIN episodes_fts ep ON e.id = ep.rowid "
                "WHERE ep.content MATCH ?"
            )
            params: list = [query]
            if user_id:
                sql += " AND (e.user_id = ? OR e.user_id = 'default')"
                params.append(user_id)
            sql += " ORDER BY ep.rank LIMIT ?"
            params.append(limit)
            rows = self.conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except Exception as e:
            logger.debug("Episode FTS search failed: %s", e)
            # Fallback: LIKE
            rows = self.conn.execute(
                "SELECT * FROM episodes WHERE content LIKE ? ORDER BY timestamp DESC LIMIT ?",
                (f"%{query}%", limit)
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def get_by_time_range(self, start: str, end: str,
                          limit: int = 50) -> List[Dict[str, Any]]:
        """Get episodes within a time range (ISO timestamps)."""
        rows = self.conn.execute(
            "SELECT * FROM episodes WHERE timestamp BETWEEN ? AND ? "
            "ORDER BY timestamp LIMIT ?",
            (start, end, limit)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_by_topic(self, topic: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get episodes matching a topic."""
        rows = self.conn.execute(
            "SELECT * FROM episodes WHERE topic = ? ORDER BY timestamp DESC LIMIT ?",
            (topic, limit)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """List recent sessions with turn counts."""
        rows = self.conn.execute(
            "SELECT session_id, COUNT(*) as turns, "
            "MIN(timestamp) as started, MAX(timestamp) as ended, "
            "topic "
            "FROM episodes GROUP BY session_id "
            "ORDER BY ended DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_session(self, session_id: str) -> int:
        """Delete all episodes in a session."""
        cur = self.conn.execute(
            "DELETE FROM episodes WHERE session_id = ?", (session_id,)
        )
        self.conn.commit()
        return cur.rowcount

    def stats(self) -> Dict[str, Any]:
        """Episode store statistics."""
        total = self.conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        sessions = self.conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM episodes"
        ).fetchone()[0]
        topics = self.conn.execute(
            "SELECT COUNT(DISTINCT topic) FROM episodes WHERE topic IS NOT NULL"
        ).fetchone()[0]
        return {
            "total_episodes": total,
            "total_sessions": sessions,
            "total_topics": topics,
        }

    @staticmethod
    def _row_to_dict(row) -> Dict[str, Any]:
        d = dict(row)
        try:
            d["metadata"] = json.loads(d.get("metadata") or "{}")
        except Exception:
            d["metadata"] = {}
        return d
