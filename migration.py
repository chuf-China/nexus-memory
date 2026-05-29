"""nexus_migration.py — Schema version tracking and migration engine

Manages schema versions in nexus_meta table and applies incremental
migrations when the database schema needs to evolve.

Usage:
  from .migration import SchemaMigration
  mig = SchemaMigration(conn)
  mig.run()  # applies any pending migrations
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Migration registry ─────────────────────────────────────

# Each migration: (version, description, sql_or_fn)
# sql_or_fn: str → raw SQL executed as executescript
#             callable(conn) → function called for complex migrations
_MIGRATIONS: List[Tuple[int, str, Any]] = []


def _register(version: int, description: str, sql_or_fn):
    _MIGRATIONS.append((version, description, sql_or_fn))


# ── migrations ─────────────────────────────────────────────

_register(1, "Base schema (schema.sql)", """
-- Applied from schema.sql at init time; this is a no-op marker.
""")

_register(2, "Add temporal columns", """
ALTER TABLE unified_knowledge ADD COLUMN valid_from TIMESTAMP;
ALTER TABLE unified_knowledge ADD COLUMN valid_to TIMESTAMP;
ALTER TABLE unified_knowledge ADD COLUMN event_time TIMESTAMP;
""")

_register(3, "Add confidence column", """
ALTER TABLE unified_knowledge ADD COLUMN confidence REAL DEFAULT 0.5;
""")

_register(4, "Add updated_at trigger", """
CREATE TRIGGER IF NOT EXISTS knowledge_updated_at
AFTER UPDATE ON unified_knowledge
BEGIN
    UPDATE unified_knowledge SET updated_at = datetime('now')
    WHERE id = NEW.id;
END;
""")

_register(5, "Knowledge conflicts table", """
CREATE TABLE IF NOT EXISTS knowledge_conflicts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_id_a  INTEGER REFERENCES unified_knowledge(id),
    knowledge_id_b  INTEGER REFERENCES unified_knowledge(id),
    conflict_type   TEXT DEFAULT 'contradiction',
    description     TEXT,
    resolved        INTEGER DEFAULT 0,
    resolution      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_kc_unresolved ON knowledge_conflicts(resolved, created_at);
""")

_register(6, "Interaction log table", """
CREATE TABLE IF NOT EXISTS interaction_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT,
    user_id         TEXT DEFAULT 'default',
    role            TEXT,
    content         TEXT,
    tool_name       TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_il_session ON interaction_log(session_id);
CREATE INDEX IF NOT EXISTS idx_il_user ON interaction_log(user_id);
""")

_register(7, "Knowledge embeddings table", """
CREATE TABLE IF NOT EXISTS knowledge_embeddings (
    entry_id        INTEGER PRIMARY KEY REFERENCES unified_knowledge(id),
    embedding       BLOB,
    embed_dim       INTEGER,
    model_name      TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")

_register(8, "Entity relations table", """
CREATE TABLE IF NOT EXISTS entity_relations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entry    INTEGER REFERENCES unified_knowledge(id),
    target_entry    INTEGER REFERENCES unified_knowledge(id),
    entity_a        TEXT NOT NULL,
    entity_b        TEXT NOT NULL,
    relation_type   TEXT DEFAULT '共现',
    weight          REAL DEFAULT 1.0,
    first_seen      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hit_count       INTEGER DEFAULT 1,
    UNIQUE(entity_a, entity_b, relation_type)
);
CREATE INDEX IF NOT EXISTS idx_er_entity_a ON entity_relations(entity_a);
CREATE INDEX IF NOT EXISTS idx_er_entity_b ON entity_relations(entity_b);
""")

_register(9, "Nexus metrics table", """
CREATE TABLE IF NOT EXISTS nexus_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_type     TEXT NOT NULL,
    latency_ms      REAL,
    sources         TEXT,
    hit_count       INTEGER DEFAULT 1,
    query_len       INTEGER,
    content_len     INTEGER,
    extra           TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nm_type ON nexus_metrics(metric_type, created_at);
""")

_register(10, "Audit log table", """
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action          TEXT NOT NULL,
    knowledge_id    INTEGER,
    user_id         TEXT,
    detail          TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_al_action ON audit_log(action, created_at);
""")


# ── SchemaMigration ────────────────────────────────────────


class SchemaMigration:
    """Schema version tracker and migration runner.

    Uses nexus_meta table to track current version.
    Applies pending migrations in order on run().
    """

    VERSION_KEY = "schema_version"

    def __init__(self, conn):
        self.conn = conn
        self._ensure_meta()

    def _ensure_meta(self):
        """Ensure nexus_meta table exists."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS nexus_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        self.conn.commit()

    def current_version(self) -> int:
        """Read current schema version from nexus_meta."""
        row = self.conn.execute(
            "SELECT value FROM nexus_meta WHERE key = ?",
            (self.VERSION_KEY,)
        ).fetchone()
        if row:
            try:
                return int(row[0])
            except (ValueError, TypeError):
                return 0
        return 0

    def set_version(self, version: int):
        """Set schema version in nexus_meta."""
        self.conn.execute(
            "INSERT OR REPLACE INTO nexus_meta (key, value) VALUES (?, ?)",
            (self.VERSION_KEY, str(version))
        )
        self.conn.commit()

    def pending(self) -> List[Tuple[int, str]]:
        """Return list of pending migrations (version, description)."""
        current = self.current_version()
        return [(v, d) for v, d, _ in _MIGRATIONS if v > current]

    def run(self, target: Optional[int] = None) -> int:
        """Apply pending migrations up to target version.

        Args:
            target: Stop at this version (None = apply all pending)

        Returns: number of migrations applied
        """
        current = self.current_version()
        applied = 0

        for version, description, sql_or_fn in sorted(_MIGRATIONS, key=lambda x: x[0]):
            if version <= current:
                continue
            if target is not None and version > target:
                break

            logger.info("Migration %d: %s", version, description)
            try:
                if callable(sql_or_fn):
                    sql_or_fn(self.conn)
                else:
                    # Filter out no-op / comment-only migrations
                    stripped = sql_or_fn.strip()
                    if stripped and not stripped.startswith("--"):
                        self.conn.executescript(stripped)

                self.set_version(version)
                applied += 1
                logger.info("Migration %d applied successfully", version)

            except Exception as e:
                # Some migrations are idempotent (ALTER TABLE ADD COLUMN
                # fails if column exists). Log and continue.
                if "duplicate column" in str(e).lower():
                    logger.debug("Migration %d: column already exists, skipping", version)
                    self.set_version(version)
                    applied += 1
                else:
                    logger.error("Migration %d failed: %s", version, e)
                    raise

        return applied

    def status(self) -> Dict[str, Any]:
        """Return migration status info."""
        current = self.current_version()
        pending = self.pending()
        return {
            "current_version": current,
            "latest_version": max(v for v, _, _ in _MIGRATIONS) if _MIGRATIONS else 0,
            "pending_count": len(pending),
            "pending": [{"version": v, "description": d} for v, d in pending],
        }
