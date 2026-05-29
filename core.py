"""
Nexus core — Unified Knowledge Store database manager.

Handles: CRUD, FTS5 search, domain score updates, promotion/demotion,
version chain, feedback logging, sleep-time consolidation.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Threat scanning for system prompt injection defense
try:
    from tools.threat_patterns import scan_for_threats as _scan_for_threats
except ImportError:
    _scan_for_threats = None  # graceful fallback if tools/ not on path

# Local LLM integration (optional — graceful fallback if unavailable)
try:
    from .local import get_client as _get_llm_client
    _HAS_LOCAL_LLM = True
except Exception:
    _HAS_LOCAL_LLM = False
    _get_llm_client = lambda: None

# Import from utils
from .utils import (
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

# ── Write guardrails ───────────────────────────────────────

# Garbage content patterns
_GARBAGE_PATTERNS = [
    re.compile(r'^[\d\s]+$'),               # 纯数字
    re.compile(r'^[^\w一-鿿]+$'),    # 纯标点/符号
    re.compile(r'^(.)\1{4,}$', re.DOTALL),   # 重复字符 5+ 次
]

# Rate limiter state: {user_id: [timestamp, ...]}
_RATE_LIMIT_STATE: Dict[str, List[float]] = {}
_RATE_LIMIT_MAX = 10     # max writes per window
_RATE_LIMIT_WINDOW = 60  # seconds


def _is_garbage(content: str) -> bool:
    """Check if content is garbage (pure digits, punctuation, repeated chars)."""
    stripped = content.strip()
    if len(stripped) < 2:
        return False  # short content handled separately
    for pat in _GARBAGE_PATTERNS:
        if pat.match(stripped):
            return True
    return False


def _check_rate_limit(user_id: str) -> bool:
    """Return True if rate limit is exceeded."""
    now = time.time()
    window_start = now - _RATE_LIMIT_WINDOW

    timestamps = _RATE_LIMIT_STATE.get(user_id, [])
    # Prune old entries
    timestamps = [t for t in timestamps if t > window_start]

    if len(timestamps) >= _RATE_LIMIT_MAX:
        _RATE_LIMIT_STATE[user_id] = timestamps
        return True

    timestamps.append(now)
    _RATE_LIMIT_STATE[user_id] = timestamps
    return False


# ---------------------------------------------------------------------------
# NexusCore
# ---------------------------------------------------------------------------

def _ensure_hf_env():
    """Set HF_ENDPOINT from config if not already set.

    Uses ~/.hermes/config.yaml → nexus.hf_mirror to resolve the mirror URL.
    Must be called before any fastembed/huggingface model loading.
    """
    if os.environ.get("HF_ENDPOINT"):
        return
    try:
        from hermes_cli.config import load_config
        cfg = load_config()
        mirror = (cfg.get("nexus") or {}).get("hf_mirror", "")
        if mirror:
            os.environ["HF_ENDPOINT"] = mirror
    except Exception:
        pass


class NexusCore:
    """Manages the nexus.db SQLite database."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        _ensure_hf_env()
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

    def _ensure_embedding_integrity(self, conn: sqlite3.Connection) -> None:
        """Check knowledge_embeddings for corrupt rows (dim<=0 or wrong blob length)."""
        try:
            # Table might not exist yet
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_embeddings'"
            ).fetchone()
            if not exists:
                return

            bad_rows = conn.execute(
                "SELECT entry_id, embed_dim, LENGTH(embedding) as blob_len "
                "FROM knowledge_embeddings "
                "WHERE embed_dim IS NULL OR embed_dim <= 0 "
                "   OR embedding IS NULL "
                "   OR LENGTH(embedding) < embed_dim * 4"
            ).fetchall()

            if bad_rows:
                logger.warning(
                    "Nexus: found %d corrupt embedding rows, removing",
                    len(bad_rows),
                )
                conn.execute(
                    "DELETE FROM knowledge_embeddings "
                    "WHERE embed_dim IS NULL OR embed_dim <= 0 "
                    "   OR embedding IS NULL "
                    "   OR LENGTH(embedding) < embed_dim * 4"
                )
                conn.commit()
        except Exception as e:
            logger.debug("Nexus: embedding integrity check skipped: %s", e)

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, timeout=10)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.execute("PRAGMA cache_size=-64000")   # 64MB
            self._local.conn.execute("PRAGMA mmap_size=268435456")  # 256MB
        return self._local.conn

    def _init_db(self):
        """Create tables from schema.sql if not exist."""
        schema_path = Path.home() / ".hermes" / "hermes-agent" / "plugins" / "memory" / "nexus" / "schema.sql"
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

        # Check embedding integrity
        self._ensure_embedding_integrity(conn)

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
        import hashlib
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

        # confidence — inline confidence score (0-1), mirrors knowledge_beliefs
        try:
            conn.execute(
                "ALTER TABLE unified_knowledge ADD COLUMN confidence REAL DEFAULT 1.0"
            )
        except Exception:
            pass

        # access_count — how many times this fact has been retrieved
        try:
            conn.execute(
                "ALTER TABLE unified_knowledge ADD COLUMN access_count INTEGER DEFAULT 0"
            )
        except Exception:
            pass

        # Index for temporal queries
        for col_cfg in (
            ("idx_uk_valid_to", "unified_knowledge", "valid_to"),
            ("idx_uk_event_time", "unified_knowledge", "event_time"),
            ("idx_uk_confidence", "unified_knowledge", "confidence"),
            ("idx_uk_access_count", "unified_knowledge", "access_count"),
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
            from .constitution import Constitution
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
                from .graph import EntityGraph
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
            "(session_id, user_id, user_query, model_response, knowledge_used, created_at) "
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
            " correction_of, knowledge_used, created_at) "
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
        _ensure_hf_env()
        _write_start = time.monotonic()
        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}

        # ── Write guardrails ──────────────────────────
        # 1. Content length check
        if len(content) < 5:
            return {"success": False, "error": "Content too short (min 5 chars)."}

        # 2. Garbage detection
        if _is_garbage(content):
            return {"success": False, "error": "Content rejected: garbage pattern detected."}

        # 3. Rate limiting
        if _check_rate_limit(user_id):
            return {"success": False, "error": f"Rate limit exceeded ({_RATE_LIMIT_MAX} writes per {_RATE_LIMIT_WINDOW}s)."}

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
                "UPDATE unified_knowledge SET last_accessed = ?, "
                "access_count = access_count + 1 WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), row["id"])
            )
            conn.commit()

            # Belief reinforcement
            try:
                from .belief import BeliefEngine
                BeliefEngine(conn).on_encounter(row["id"])
            except Exception:
                pass

            # ── Metrics ──
            _write_ms = round((time.monotonic() - _write_start) * 1000, 1)
            try:
                from .metrics import NexusMetrics
                NexusMetrics(conn).record_write(_write_ms, len(content), action="updated_existing")
            except Exception:
                pass

            return {
                "success": True, "action": "updated_existing",
                "id": row["id"], "layer": row["layer"]
            }

        # ── Write-time merge (knowledge evolution) ──────────────
        if not skip_conflict_detection:
            try:
                from .evolve import evolve_on_write
                try:
                    from .embedder import get_embedder
                    _embedder = get_embedder()
                except Exception:
                    _embedder = None
                logger.debug("Write-time merge embedder: %s available=%s",
                             type(_embedder).__name__ if _embedder else "None",
                             getattr(_embedder, 'available', 'N/A'))
                merge = evolve_on_write(content, user_id, conn, embedder=_embedder)
                if merge["action"] == "exact_dup":
                    # Already handled above, but safety catch
                    _write_ms = round((time.monotonic() - _write_start) * 1000, 1)
                    try:
                        from .metrics import NexusMetrics
                        NexusMetrics(conn).record_write(_write_ms, len(content), action="exact_dup")
                    except Exception:
                        pass
                    return {
                        "success": True, "action": "updated_existing",
                        "id": merge["target_id"]
                    }
                if merge["action"] in ("fuzzy_dup", "complement", "update"):
                    # evolve_on_write already mutated the DB
                    _write_ms = round((time.monotonic() - _write_start) * 1000, 1)
                    try:
                        from .metrics import NexusMetrics
                        NexusMetrics(conn).record_write(_write_ms, len(content), action=merge["action"])
                    except Exception:
                        pass
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
                from .graph import EntityGraph
                eg = EntityGraph(conn)
                eg.extract_and_link(new_id, content)
                # LLM-enhanced entity extraction (async, best-effort)
                try:
                    from .miner import auto_build_graph
                    auto_build_graph(content, conn=conn)
                except Exception:
                    pass

                # ── Fact extraction + entity resolution (best-effort) ──
                try:
                    from .facts import FactExtractor
                    fe = FactExtractor(conn)
                    fe.extract(content, source=f"entry:{new_id}")
                except Exception:
                    pass
                try:
                    from .resolve import EntityResolver
                    er = EntityResolver(conn)
                    from .miner import extract_entities
                    for ent in extract_entities(content)[:10]:
                        er.resolve(ent)
                except Exception:
                    pass
        except Exception:
            pass

        # ── Belief initialization ──
        self._init_belief(new_id, initial_confidence or 0.40)

        # ── Metrics ──
        _write_ms = round((time.monotonic() - _write_start) * 1000, 1)
        try:
            from .metrics import NexusMetrics
            NexusMetrics(conn).record_write(_write_ms, len(content), action="created")
        except Exception:
            pass

        return {"success": True, "action": "created", "id": new_id, "layer": "instant"}

    def _init_belief(self, knowledge_id: int, initial_confidence: float = 0.40) -> None:
        """Initialize belief record for a new knowledge entry (non-blocking)."""
        try:
            from .belief import BeliefEngine
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
            from .embedder import get_embedder
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
            from .embedder import get_embedder
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
            from .hnsw import get_hnsw_index
            hnsw = get_hnsw_index(self._conn(), dim=embed_dim)
            if not hnsw.available:
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
                "last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), knowledge_id)
            )
        elif feedback_type == 'explicit_negative':
            self._save_version(knowledge_id, f"feedback_{feedback_type}", user_id)
            conn.execute(
                "UPDATE unified_knowledge SET negative_feedback = negative_feedback + 1, "
                "last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
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
        entity_match = NexusCore._ENTITY_RE.search(content)
        entity = entity_match.group(1).strip() if entity_match else None
        if not entity:
            return results

        # Extract condition from the same entry
        cond_match = NexusCore._CONDITION_RE.search(content)
        condition = cond_match.group(1).strip() if cond_match else ""

        for m in NexusCore._METRIC_RE.finditer(content):
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

    def search(self, query: str, user_id: str = "default",
               limit: int = 5, mode: str = "fts",
               include_debug: bool = False) -> List[Dict[str, Any]]:
        """Search knowledge entries.

        Modes:
          'fts' (default): FTS5 MATCH + LIKE %% fallback.
          'semantic': Vector similarity via fastembed/Ollama
                      (falls back to FTS if unavailable).
          'graph': Entity-relationship graph traversal
                   (finds entries linked via shared entities).
          'hybrid': All three strategies merged + reranked.

        Args:
            include_debug: If True, attach _debug field with latency/source info.
        """
        _search_start = time.monotonic()
        _source_counts: Dict[str, int] = {}
        # Per-source ranked lists for RRF fusion (hybrid mode)
        _fts_ranked: List[int] = []    # entry_ids in FTS rank order
        _sem_ranked: List[int] = []    # entry_ids in semantic rank order
        _graph_ranked: List[int] = []  # entry_ids in graph rank order
        results: List[Dict[str, Any]] = []
        conn = self._conn()

        if mode in ("semantic", "hybrid"):
            semantic_results = self._search_semantic(query, user_id, limit * 2)
            if semantic_results:
                results.extend(semantic_results)
                _source_counts["semantic"] = len(semantic_results)
                _sem_ranked = [r.get("entry_id") or r.get("id") for r in semantic_results]

        if mode in ("graph", "hybrid"):
            try:
                from .graph import EntityGraph
                eg = EntityGraph(self._conn())
                graph_results = eg.search_by_graph(query, limit=limit * 2)
                for r in graph_results:
                    r["_source"] = "graph"
                results.extend(graph_results)
                _source_counts["graph"] = len(graph_results)
                _graph_ranked = [r.get("entry_id") or r.get("id") for r in graph_results]
            except Exception:
                pass

        if mode == "fts" or (mode == "hybrid" and not results):
            # FTS5 search always available as fallback
            pass  # fall through to FTS below

        if mode == "fts" or (mode == "hybrid" and len(results) < limit) or mode not in ("semantic", "graph", "hybrid"):
            clean = _CONTENT_WHITESPACE.sub(' ', query).strip()
        else:
            clean = ""

        if clean:
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
                    rows = conn.execute(
                        """SELECT uk.id, uk.content, uk.domain_scores, uk.layer,
                                  uk.positive_feedback, uk.negative_feedback,
                                  uk.active_summary, uk.user_id
                           FROM unified_knowledge uk
                           WHERE uk.content LIKE ?
                             AND uk.status = 'active'
                             AND (uk.user_id = ? OR uk.user_id = 'default')
                           ORDER BY (uk.positive_feedback - uk.negative_feedback * 2) DESC,
                                    uk.last_accessed DESC
                           LIMIT ?""",
                        (f"%{clean}%", user_id, limit * 2)
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
            fts_added = 0
            for r in fts_results:
                rid = r.get("id")
                if rid not in seen_ids:
                    seen_ids.add(rid)
                    r["_source"] = "fts"
                    results.append(r)
                    fts_added += 1
                    _fts_ranked.append(rid)
            if fts_added:
                _source_counts["fts"] = fts_added

        # ── Enhanced recall: query expansion + multi-hop + negation ──
        # (only for hybrid mode — boosts recall beyond single-query FTS5)
        if mode == "hybrid":
            try:
                from .search import expand_query, is_negation_query
                from .search import extract_entities, needs_relative_time
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
                    import re
                    from .search import _NEGATION_WORDS
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

        # ── Intent-weighted fusion (hybrid mode) ──────────
        # Classify query intent → select fusion weights → score = Σ weight_i * norm_score_i
        if mode == "hybrid" and results:
            try:
                from .search import get_intent_weights, normalize_scores
                weights = get_intent_weights(query, mode="auto")
            except Exception:
                weights = {"fts": 0.40, "vec": 0.35, "graph": 0.25}

            # Build per-source score maps (normalized to 0-1)
            fts_scores = {}
            for eid in _fts_ranked:
                if eid:
                    fts_scores[eid] = 1.0  # already rank-ordered

            vec_scores = {}
            for r in results:
                if r.get("_source") == "hnsw" or r.get("similarity"):
                    eid = r.get("id") or r.get("entry_id")
                    if eid:
                        vec_scores[eid] = r.get("similarity", 0.5)

            graph_scores = {}
            for r in results:
                if r.get("_source") == "graph":
                    eid = r.get("id") or r.get("entry_id")
                    if eid:
                        graph_scores[eid] = r.get("score", 0.5)

            # Weighted fusion
            for r in results:
                eid = r.get("id")
                if not eid:
                    continue
                fts_s = fts_scores.get(eid, 0.0)
                vec_s = vec_scores.get(eid, 0.0)
                graph_s = graph_scores.get(eid, 0.0)

                fused = (weights["fts"] * fts_s
                         + weights["vec"] * vec_s
                         + weights["graph"] * graph_s)

                # Blend with existing score
                base = r.get("similarity") or r.get("score") or 0.5
                r["fusion_score"] = round(fused, 4)
                r["similarity"] = round(base * 0.3 + fused * 0.7, 4)
                r["_intent_weights"] = weights

        # ── Rerank: cross-encoder + score fusion ──────────
        try:
            from .embedder import Reranker
            reranker = Reranker()
            results = reranker.rerank(query, results, top_k=limit)
        except Exception:
            pass

        # ── Auto record domain hit (top 3) ──────────────
        if results:
            for r in results[:3]:
                try:
                    rid = r.get("id") or r.get("entry_id")
                    if rid:
                        domain = self._infer_domain(query, r)
                        self.record_domain_hit(rid, domain)
                except Exception:
                    pass

        # ── Add version history to results ──────────────
        for r in results:
            try:
                rid = r.get("id") or r.get("entry_id")
                if not rid:
                    continue
                history = self.get_history(rid)
                if history and len(history) > 1:
                    r["version"] = len(history)
                    r["last_updated"] = history[-1].get("changed_at", "")[:10]
            except Exception:
                pass

        # ── Metrics + debug ─────────────────────────────
        _search_ms = round((time.monotonic() - _search_start) * 1000, 1)
        try:
            from .metrics import NexusMetrics
            NexusMetrics(conn).record_search(
                _search_ms,
                list(_source_counts.keys()),
                len(results),
                query_len=len(query),
            )
        except Exception:
            pass

        # Attach _debug when requested (e.g. from search tool)
        if include_debug:
            for r in results:
                r["_debug"] = {
                    "fts_hits": _source_counts.get("fts", 0),
                    "hnsw_hits": _source_counts.get("semantic", 0),
                    "graph_hits": _source_counts.get("graph", 0),
                    "total_latency_ms": _search_ms,
                    "mode": mode,
                }

        # Log empty results for query optimization
        if not results:
            logger.debug("Nexus search: empty result for query=%r mode=%s", query[:80], mode)

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
            from .search import build_context_v2
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
            rid = r.get("id") or r.get("entry_id")
            if rid:
                conn.execute(
                    "UPDATE unified_knowledge SET last_accessed = ?, "
                    "access_count = access_count + 1 WHERE id = ?",
                    (now, rid)
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

    def _save_version(self, knowledge_id: int, reason: str, user_id: str):
        """Snapshot knowledge content into knowledge_versions for history tracking."""
        conn = self._conn()
        row = conn.execute(
            "SELECT content, active_summary, status FROM unified_knowledge WHERE id = ?",
            (knowledge_id,)
        ).fetchone()
        if not row:
            return
        conn.execute(
            """INSERT INTO knowledge_versions
               (knowledge_id, content, active_summary, status, change_reason, user_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (knowledge_id, row["content"], row["active_summary"],
             row["status"], reason, user_id)
        )
        conn.commit()

    def get_history(self, knowledge_id: int) -> List[Dict[str, Any]]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM knowledge_versions WHERE knowledge_id = ? ORDER BY created_at DESC",
            (knowledge_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Stats ----------------------------------------------------------------

    def stats(self, user_id: str = "default") -> Dict[str, Any]:
        conn = self._conn()
        total = conn.execute(
            "SELECT COUNT(*) FROM unified_knowledge WHERE user_id = ? OR user_id = 'default'",
            (user_id,)
        ).fetchone()[0]
        by_layer = {}
        for layer in ('instant', 'candidate', 'consolidated'):
            cnt = conn.execute(
                "SELECT COUNT(*) FROM unified_knowledge WHERE layer = ? AND status = 'active' AND (user_id = ? OR user_id = 'default')",
                (layer, user_id)
            ).fetchone()[0]
            by_layer[layer] = cnt
        return {"total": total, "by_layer": by_layer}

    def get_alerts(self, user_id: str = "default") -> List[Dict[str, Any]]:
        """Return actionable alerts: unresolved conflicts, instant pileup, etc."""
        conn = self._conn()
        alerts = []

        # 1. Unresolved system conflicts (feedback_log)
        conflicts = conn.execute(
            "SELECT fb.id, fb.knowledge_id, fb.source, fb.created_at, "
            "substr(uk.content, 1, 100) as content "
            "FROM feedback_log fb "
            "JOIN unified_knowledge uk ON uk.id = fb.knowledge_id "
            "WHERE fb.feedback_type = 'system_conflict' "
            "AND uk.status = 'active' "
            "ORDER BY fb.created_at DESC LIMIT 5"
        ).fetchall()
        for cf in conflicts:
            alerts.append({
                "type": "conflict",
                "severity": "medium",
                "knowledge_id": cf["knowledge_id"],
                "detail": cf["source"],
                "content": cf["content"],
                "created_at": cf["created_at"],
            })

        # 2. Instant layer pileup (>10 entries)
        inst_count = conn.execute(
            "SELECT COUNT(*) FROM unified_knowledge "
            "WHERE layer='instant' AND status='active' "
            "AND (user_id=? OR user_id='default')", (user_id,)
        ).fetchone()[0]
        if inst_count > 10:
            alerts.append({
                "type": "instant_pileup",
                "severity": "low",
                "detail": f"{inst_count} instant entries pending consolidation",
            })

        # 3. Feedback-driven entries with excess negative
        neg_overload = conn.execute(
            "SELECT id, substr(content, 1, 80), negative_feedback, positive_feedback "
            "FROM unified_knowledge "
            "WHERE negative_feedback > positive_feedback + 3 "
            "AND status='active' AND layer='consolidated' "
            "AND (user_id=? OR user_id='default') "
            "LIMIT 3", (user_id,)
        ).fetchall()
        for no in neg_overload:
            alerts.append({
                "type": "negative_overload",
                "severity": "medium",
                "knowledge_id": no["id"],
                "detail": f"consolidated entry {no['id']} has {no['negative_feedback']} negative vs {no['positive_feedback']} positive",
                "content": no[1],
            })

        return alerts

    _PROMPT_BLOCK_SEPARATOR = "═" * 46

    def system_prompt_block(self, memory_enabled: bool = True,
                            user_enabled: bool = True,
                            user_id: str = "default",
                            char_limit: int = 2200) -> str:
        """Build the MEMORY block for system prompt injection.
        Entries are sorted by feedback weight (highest first)."""
        parts = []

        # ── Cold start hint (first 30 days only) ──
        try:
            stats = self._get_coldstart_stats(user_id)
            if 0 < stats["days_active"] <= 30 and stats["total_entries"] > 0:
                cold = (
                    f"[Day {stats['days_active']}] Nexus 已记录 {stats['total_entries']} 条知识"
                )
                if stats["today_entries"] > 0:
                    cold += f"，今日 +{stats['today_entries']}"
                if stats["patterns_found"] > 0:
                    cold += f"，发现 {stats['patterns_found']} 个模式"
                if stats["pending_conflicts"] > 0:
                    cold += f"，{stats['pending_conflicts']} 个待确认冲突"
                cold += "。"
                if stats["days_active"] <= 7:
                    cold += " 系统正在学习你的使用模式。"
                parts.append(cold)
        except Exception:
            pass

        if not memory_enabled and not user_enabled:
            return "\n".join(parts) if parts else ""
        if memory_enabled:
            entries = self.search_by_domain("identity", user_id=user_id, limit=20)
            if entries:
                # Sort by feedback weight descending
                entries.sort(key=lambda e: e.get("positive_feedback", 0) - e.get("negative_feedback", 0) * 2, reverse=True)
                lines = []
                total_chars = 0
                for e in entries:
                    line = e["content"]
                    # Threat-scan before injecting into system prompt
                    if _scan_for_threats is not None:
                        threats = _scan_for_threats(line, scope="context")
                        if threats:
                            logger.warning(
                                "Nexus: blocked entry %s (threats: %s)",
                                e.get("id", "?"), ", ".join(threats),
                            )
                            line = f"[BLOCKED: Nexus entry contained potential injection ({', '.join(threats)}). Content redacted.]"
                    if total_chars + len(line) + 3 > char_limit:
                        break
                    lines.append(line)
                    total_chars += len(line) + 3
                if lines:
                    content = "\n§\n".join(lines)
                    current = len(content)
                    pct = min(100, int((current / char_limit) * 100)) if char_limit > 0 else 0
                    header = f"MEMORY (your personal notes) [{pct}% — {current:,}/{char_limit:,} chars]"
                    parts.append(f"{self._PROMPT_BLOCK_SEPARATOR}\n{header}\n{self._PROMPT_BLOCK_SEPARATOR}\n{content}")
        if user_enabled:
            u_entries = self.search_by_domain("identity", user_id=user_id, limit=20)
            if u_entries:
                u_entries.sort(key=lambda e: e.get("positive_feedback", 0) - e.get("negative_feedback", 0) * 2, reverse=True)
                lines = []
                total_chars = 0
                user_limit = 1375
                for e in u_entries:
                    line = e["content"]
                    # Threat-scan before injecting into system prompt
                    if _scan_for_threats is not None:
                        threats = _scan_for_threats(line, scope="context")
                        if threats:
                            logger.warning(
                                "Nexus: blocked user entry %s (threats: %s)",
                                e.get("id", "?"), ", ".join(threats),
                            )
                            line = f"[BLOCKED: Nexus entry contained potential injection ({', '.join(threats)}). Content redacted.]"
                    if total_chars + len(line) + 3 > user_limit:
                        break
                    lines.append(line)
                    total_chars += len(line) + 3
                if lines:
                    content = "\n§\n".join(lines)
                    current = len(content)
                    pct = min(100, int((current / user_limit) * 100)) if user_limit > 0 else 0
                    header = f"USER PROFILE (who the user is) [{pct}% — {current:,}/{user_limit:,} chars]"
                    parts.append(f"{self._PROMPT_BLOCK_SEPARATOR}\n{header}\n{self._PROMPT_BLOCK_SEPARATOR}\n{content}")
        return "\n\n".join(parts)

    def get_subsystem_views(self, user_id: str = "default") -> Dict[str, Any]:
        """Return organized views of consolidated knowledge by domain."""
        conn = self._conn()
        domains = {
            "identity": "用户画像与 Agent 身份",
            "workflow": "工作流技能与操作模式",
            "strategy": "交易策略与分析逻辑",
            "behavior": "行为偏好与沟通风格",
            "rule": "合规规则与约束",
            "raw_fact": "事实与数据",
        }
        views = {}
        for d, label in domains.items():
            rows = conn.execute(
                "SELECT id, substr(content, 1, 120) as preview, "
                "positive_feedback, negative_feedback, "
                "layer, active_summary, last_accessed "
                "FROM unified_knowledge "
                "WHERE json_extract(domain_scores, ?) > 0 "
                "AND status = 'active' "
                "AND (user_id = ? OR user_id = 'default') "
                "ORDER BY (positive_feedback - negative_feedback * 2) DESC "
                "LIMIT 20",
                (f'$.{d}', user_id)
            ).fetchall()
            views[d] = {
                "label": label,
                "count": len(rows),
                "entries": [dict(r) for r in rows],
            }
        # Health summary
        alerts = []
        for d, data in views.items():
            neg = sum(
                1 for e in data["entries"]
                if e["negative_feedback"] > e["positive_feedback"]
            )
            if neg > 3:
                alerts.append(f"{d}: {neg} entries with excess negative feedback")
        return {"domains": views, "alerts": alerts}

    # -- FTS index integrity ---------------------------------------------------

    def _ensure_fts_integrity(self, conn: sqlite3.Connection) -> None:
        """Check FTS index health and rebuild if needed.

        Called during _init_db(). Rebuilds when:
        - FTS schema version mismatch (migration marker)
        - FTS table is empty (first run / after DROP)
        - FTS count != unified_knowledge count (desync after schema change)
        """
        # Check FTS segmentation version marker
        version = conn.execute(
            "SELECT value FROM nexus_meta WHERE key = 'fts_seg_version'"
        ).fetchone()

        if not version or version[0] < '2':
            logger.info(
                "Nexus: FTS segmentation v2 roll-out (was %s), rebuilding...",
                version[0] if version else 'none'
            )
            self.rebuild_fts(conn)
            conn.execute(
                "INSERT OR REPLACE INTO nexus_meta (key, value) "
                "VALUES ('fts_seg_version', '2')"
            )
            conn.commit()
            return

        try:
            uk_count = conn.execute(
                "SELECT COUNT(*) FROM unified_knowledge WHERE status = 'active'"
            ).fetchone()[0]
            fts_count = conn.execute(
                "SELECT COUNT(*) FROM knowledge_fts"
            ).fetchone()[0]
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            logger.warning("Nexus: FTS table not accessible, rebuilding...")
            fts_count = -1
            uk_count = 0

        if uk_count > 0 and fts_count != uk_count:
            logger.info(
                "Nexus: FTS index out of sync (UK=%d, FTS=%d), rebuilding...",
                uk_count, fts_count
            )
            self.rebuild_fts(conn)

    def rebuild_fts(self, conn: Optional[sqlite3.Connection] = None) -> int:
        """Rebuild the FTS index with jieba segmentation for all entries.

        Drops triggers temporarily, purges FTS, re-inserts with segmented
        content, then restores triggers.

        Returns: number of indexed entries.
        """
        if conn is None:
            conn = self._conn()

        # Temporarily disable triggers to avoid double-writes
        for trig in ('knowledge_ai', 'knowledge_ad', 'knowledge_au'):
            conn.execute(f"DROP TRIGGER IF EXISTS {trig}")

        # Purge FTS index
        # NOTE: DELETE FROM knowledge_fts fails on SQLite 3.46.1 with
        # external-content FTS5 tables ("database disk image is malformed").
        # Using 'rebuild' command instead — the correct approach for
        # content-sync FTS5 tables per SQLite docs.
        conn.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')")

        # Re-index all active entries with jieba segmentation
        rows = conn.execute(
            "SELECT id, content FROM unified_knowledge WHERE status = 'active'"
        ).fetchall()
        count = 0
        for row in rows:
            seg = _segment_fts(row["content"])
            conn.execute(
                "INSERT INTO knowledge_fts(rowid, content) VALUES (?, ?)",
                (row["id"], seg)
            )
            count += 1

        # Restore triggers from schema
        schema_path = Path.home() / ".hermes" / "hermes-agent" / "plugins" / "memory" / "nexus" / "schema.sql"
        if schema_path.exists():
            conn.executescript(schema_path.read_text())

        conn.commit()
        logger.info("Nexus: FTS index rebuilt with %d entries (jieba segmented)", count)
        return count

    # -- Sleep-time consolidation (basic) -------------------------------------

    def consolidate(self, user_id: str = "default") -> Dict[str, Any]:
        """Run one consolidation pass. Call from background thread."""
        conn = self._conn()
        now = datetime.now(timezone.utc).isoformat()
        actions = []

        # Stage 1: clean up instant entries older than 30 days
        deleted = conn.execute(
            "DELETE FROM unified_knowledge WHERE layer = 'instant' AND status = 'active' "
            "AND last_accessed < datetime('now', '-30 days') "
            "AND (user_id = ? OR user_id = 'default')",
            (user_id,)
        ).rowcount
        if deleted:
            actions.append(f"cleaned_{deleted}_stale_instant")

        # Stage 2: promote entries based on weight (candidate → consolidated)
        promoted = conn.execute(
            """UPDATE unified_knowledge SET layer = 'consolidated', updated_at = ?
               WHERE layer = 'candidate' AND status = 'active'
               AND (positive_feedback - negative_feedback * 2) >= 8
               AND (user_id = ? OR user_id = 'default')""",
            (now, user_id)
        ).rowcount
        if promoted:
            actions.append(f"promoted_{promoted}_to_consolidated")

        # Stage 3: supersede entries with excessive negative feedback
        superseded = conn.execute(
            """UPDATE unified_knowledge SET status = 'superseded', updated_at = ?
               WHERE layer IN ('candidate','consolidated') AND status = 'active'
               AND (positive_feedback - negative_feedback * 2) <= -5
               AND (user_id = ? OR user_id = 'default')""",
            (now, user_id)
        ).rowcount
        if superseded:
            actions.append(f"superseded_{superseded}_due_to_negative_feedback")

        # Stage 4: archive consolidated entries not accessed in 180 days
        archived = conn.execute(
            """UPDATE unified_knowledge SET status = 'archived', updated_at = ?
               WHERE layer = 'consolidated' AND status = 'active'
               AND last_accessed < datetime('now', '-180 days')
               AND (user_id = ? OR user_id = 'default')""",
            (now, user_id)
        ).rowcount
        if archived:
            actions.append(f"archived_{archived}_stale_consolidated")

        # Stage 5: backfill active_summary for consolidated entries that lack it
        missing_summary = conn.execute(
            "SELECT id, content FROM unified_knowledge "
            "WHERE layer = 'consolidated' AND status = 'active' "
            "AND (active_summary IS NULL OR active_summary = '') "
            "AND (user_id = ? OR user_id = 'default')",
            (user_id,)
        ).fetchall()
        if missing_summary:
            for mr in missing_summary:
                s = _generate_summary(mr["content"])
                if s:
                    conn.execute(
                        "UPDATE unified_knowledge SET active_summary = ? WHERE id = ?",
                        (s, mr["id"])
                    )
            actions.append(f"backfilled_{len(missing_summary)}_summaries")

        # Stage 6: Belief update — time decay + archive
        try:
            from .belief import BeliefEngine
            be = BeliefEngine(conn)
            belief_result = be.update_all_beliefs()
            if belief_result["decayed_count"] or belief_result["archived_count"]:
                actions.append(f"belief_decayed_{belief_result['decayed_count']}_archived_{belief_result['archived_count']}")
        except Exception:
            pass

        # Stage 7: run miner
        # Entries with the same entity (策略: XXX) and same metric but close
        # values get averaged, with the oldest kept and newer superseded.
        _merged = 0
        _instant_rows = conn.execute(
            "SELECT id, content FROM unified_knowledge "
            "WHERE layer = 'instant' AND status = 'active' AND sleep_time_processed = 0 "
            "AND (user_id = ? OR user_id = 'default')",
            (user_id,)
        ).fetchall()
        for ir in _instant_rows:
            im = self._extract_metrics(ir["content"])
            if not im:
                continue
            entity = im[0][0]
            # Find other instant entries with the same entity
            _peers = [r for r in _instant_rows if r["id"] != ir["id"]]
            for pr in _peers:
                pm = self._extract_metrics(pr["content"])
                if not pm:
                    continue
                if pm[0][0] != entity:
                    continue
                # Same entity in instant layer — mark as processed
                conn.execute(
                    "UPDATE unified_knowledge SET sleep_time_processed = 1 WHERE id = ?",
                    (pr["id"],)
                )
                _merged += 1
            # Mark current as processed
            conn.execute(
                "UPDATE unified_knowledge SET sleep_time_processed = 1 WHERE id = ?",
                (ir["id"],)
            )
        if _merged:
            # conn.commit()  # batched to final commit
            actions.append(f"merged_{_merged}_duplicate_entities")

        # ── Sleep-time Stage 7: detect repeated entity mentions ──────────────
        # Any entity appearing 3+ times in consolidated layer gets a
        # summary entry promoted to consolidated.
        _entity_counts = {}
        _all_active = conn.execute(
            "SELECT id, content FROM unified_knowledge "
            "WHERE status = 'active' AND (user_id = ? OR user_id = 'default')",
            (user_id,)
        ).fetchall()
        for ar in _all_active:
            em = self._extract_metrics(ar["content"])
            if em:
                e = em[0][0]
                _entity_counts[e] = _entity_counts.get(e, 0) + 1
        for entity, count in _entity_counts.items():
            if count >= 3:
                _existing = conn.execute(
                    "SELECT id FROM unified_knowledge WHERE content LIKE ? "
                    "AND status = 'active' AND layer = 'consolidated'",
                    (f"%重复模式: {entity}%",)
                ).fetchone()
                if not _existing:
                    _summary_content = (
                        f"重复模式: {entity}\n"
                        f"出现次数: {count}\n"
                        f"发现时间: {datetime.now(timezone.utc).isoformat()}\n"
                        f"说明: 该实体在知识库中出现至少 {count} 次，"
                        f"可能值得关注或整理。"
                    )
                    _mhash = hashlib.sha256(_summary_content.encode()).hexdigest()[:16]
                    conn.execute(
                        "INSERT INTO unified_knowledge (content, domain_scores, layer, match_hash, user_id) "
                        "VALUES (?, ?, 'consolidated', ?, ?)",
                        (_summary_content, json.dumps({"pattern": 8, "raw_fact": 3}), _mhash, user_id)
                    )
                    _sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    _seg = _segment_fts(_summary_content)
                    conn.execute("INSERT INTO knowledge_fts(knowledge_fts, rowid, content) VALUES ('delete', ?, '')", (_sid,))
                    conn.execute("INSERT INTO knowledge_fts(rowid, content) VALUES (?, ?)", (_sid, _seg))
                    actions.append(f"pattern_detected_{entity}_x{count}")
        if any(a.startswith("pattern_detected") for a in actions):
            pass  # (commit batched to final)

        # ── Sleep-time Stage 8: emergent skills from repeated workflow ──────
        # Scan instant entries for task-type keywords. When the same combo
        # appears 3+ times, create a workflow-domain entry (emergent skill).
        _wf_keywords = [
            "分析", "查询", "搜索", "计算", "生成", "预测",
            "诊断", "报告", "监控", "对比", "评估", "汇总",
        ]
        _wf_entries = conn.execute(
            "SELECT content FROM unified_knowledge "
            "WHERE layer = 'instant' AND status = 'active' "
            "AND (user_id = ? OR user_id = 'default')",
            (user_id,)
        ).fetchall()
        _wf_patterns = {}
        for we in _wf_entries:
            hits = [kw for kw in _wf_keywords if kw in we["content"]]
            if len(hits) >= 2:
                key = "+".join(sorted(hits))
                _wf_patterns[key] = _wf_patterns.get(key, 0) + 1
        for pattern, count in _wf_patterns.items():
            if count >= 3:
                _existing = conn.execute(
                    "SELECT id FROM unified_knowledge WHERE content LIKE ? AND status = 'active'",
                    (f"%涌现技能: {pattern}%",)
                ).fetchone()
                if not _existing:
                    _skill_content = (
                        f"涌现技能: {pattern}\n"
                        f"触发次数: {count}\n"
                        f"发现时间: {datetime.now(timezone.utc).isoformat()}\n"
                        f"摘要: 检测到重复工作流({pattern})，在执行任务时可考虑调用此技能。\n"
                    )
                    _mhash = hashlib.sha256(_skill_content.encode()).hexdigest()[:16]
                    conn.execute(
                        "INSERT INTO unified_knowledge (content, domain_scores, layer, match_hash, user_id) "
                        "VALUES (?, ?, 'consolidated', ?, ?)",
                        (_skill_content, json.dumps({"workflow": 10, "pattern": 5}), _mhash, user_id)
                    )
                    _sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    _seg = _segment_fts(_skill_content)
                    conn.execute("INSERT INTO knowledge_fts(knowledge_fts, rowid, content) VALUES ('delete', ?, '')", (_sid,))
                    conn.execute("INSERT INTO knowledge_fts(rowid, content) VALUES (?, ?)", (_sid, _seg))
                    actions.append(f"emergent_skill_{pattern}_x{count}")
                    # Also write SKILL.md file to make it a real skill
                    try:
                        _skill_name = f"emergent-{pattern.lower().replace('+', '-')}"
                        _skill_dir = Path.home() / ".hermes" / "skills" / "emergent" / _skill_name
                        _skill_dir.mkdir(parents=True, exist_ok=True)
                        _skill_md = _skill_dir / "SKILL.md"
                        if not _skill_md.exists():
                            _desc_parts = pattern.split("+")
                            _desc = "、".join(_desc_parts)
                            _skill_md.write_text(
                                f"---\n"
                                f"name: {_skill_name}\n"
                                f"description: 自动涌现的工作流技能 - {_desc}\n"
                                f"category: emergent\n"
                                f"---\n"
                                f"\n"
                                f"# {_desc} 工作流\n"
                                f"\n"
                                f"该技能由 Nexus 自动检测到重复工作流后生成。\n"
                                f"\n"
                                f"**触发模式**: {pattern}\n"
                                f"**触发次数**: {count}\n"
                                f"**发现时间**: {datetime.now(timezone.utc).isoformat()}\n"
                                f"\n"
                                f"## 使用说明\n"
                                f"\n"
                                f"此技能包含 {_desc} 相关的工作流步骤。\n"
                                f"使用 skills_list 查看，skill_view 加载。\n",
                                encoding="utf-8"
                            )
                            actions.append(f"skill_file_created_wf_{pattern}")
                    except Exception:
                        pass

        # ── Sleep-time Stage 9: cross-validate consolidated/candidate layers ──
        try:
            val_result = self._validate_by_layer(user_id)
            if val_result["conflicts_found"] > 0:
                actions.append(f"layer_conflicts_{val_result['conflicts_found']}")
        except Exception:
            pass

        # ── Stage 10: Miner — scan interaction patterns ─────────────────────
        try:
            from .miner import NexusMiner
            miner = NexusMiner()
            report = miner.mine_all()
            miner.close()
            risk_count = report.get("summary", {}).get("high_risk", 0)
            pattern_count = report.get("summary", {}).get("total_patterns", 0)
            if risk_count:
                actions.append(f"high_risk_knowledge_{risk_count}")
            if pattern_count:
                actions.append(f"query_patterns_{pattern_count}")
            # Auto-demote high-risk knowledge
            for k in report.get("high_risk_knowledge", []):
                try:
                    self._demote(k["id"], "auto: 纠正率过高", user_id)
                    actions.append(f"auto_demoted_{k['id']}")
                except Exception:
                    pass
        except Exception:
            pass

        conn.commit()
        # WAL checkpoint after consolidation
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        return {"actions": actions if actions else ["no_op"]}


# ═══════════════════════════════════════════════════════════
# Context Compressor (P2-8)
# ═══════════════════════════════════════════════════════════


class ContextCompressor:
    """3-level context compression for long conversations.

    Level 1: Clear old tool results (keep recent N)
    Level 2: Summarize old messages via LLM (keep recent N)
    Level 3: (reserved for future use)
    """

    def __init__(self, keep_tool_results: int = 5, keep_recent: int = 10):
        self.keep_tool_results = keep_tool_results
        self.keep_recent = keep_recent

    def maybe_compress(self, messages: list, token_count: int,
                       max_tokens: int) -> list:
        """Compress messages if token count exceeds threshold (80% of max)."""
        threshold = max_tokens * 0.8
        if token_count < threshold:
            return messages

        # Level 1: Clear old tool results
        messages = self._clear_old_tool_results(messages)
        if self._estimate_tokens(messages) < threshold:
            return messages

        # Level 2: Summarize old messages
        messages = self._summarize_history(messages)
        return messages

    def _clear_old_tool_results(self, messages: list) -> list:
        """Replace old tool results with placeholder (keep recent N)."""
        tool_count = 0
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "tool":
                tool_count += 1
                if tool_count > self.keep_tool_results:
                    messages[i] = {
                        "role": "tool",
                        "content": "[Old tool result cleared]",
                    }
        return messages

    def _summarize_history(self, messages: list) -> list:
        """Replace old messages with LLM summary (keep recent N)."""
        if len(messages) <= self.keep_recent:
            return messages

        old_messages = messages[:-self.keep_recent]
        recent_messages = messages[-self.keep_recent:]

        # Try LLM summary
        try:
            from .local import get_client as _get_llm
            client = _get_llm()
            if client and client.ping():
                resp = client.chat([
                    {"role": "system", "content":
                     "将以下对话压缩为简短摘要，保留关键事实、决策和用户偏好。"
                     "用 bullet points，不超过 200 字。"},
                    {"role": "user", "content": json.dumps(old_messages[-10:])},
                ], max_tokens=256)
                summary = resp.get("response", "") or resp.get("message", {}).get("content", "")
                if summary:
                    return [{"role": "system", "content": f"[对话摘要] {summary}"}] + recent_messages
        except Exception:
            pass

        # Fallback: just keep recent
        return recent_messages

    @staticmethod
    def _estimate_tokens(messages: list) -> int:
        """Rough token estimate: ~4 chars per token."""
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        return total_chars // 4
