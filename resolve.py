"""nexus_resolve.py — Entity Resolution (实体消歧)

Resolves entity aliases and deduplicates entities in the knowledge graph.

Flow:
  1. New entity → FTS5 fuzzy match against existing entities
  2. Candidates found → LLM precision check (optional)
  3. Merge or create new entity

Handles: "张三" = "三哥" = "Zhang San"

Usage:
  from .resolve import EntityResolver
  er = EntityResolver(conn)
  resolved = er.resolve("三哥")
  er.merge(source="三哥", target="张三")
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Common alias patterns
_NICKNAME_PATTERNS = [
    # Chinese nicknames: 小X, 老X, X哥, X姐
    re.compile(r'^(.{1,3})(哥|姐|弟|妹|总|工|老师|教授)$'),
    # Abbreviations: "PG" = "PostgreSQL"
    re.compile(r'^[A-Z]{2,6}$'),
]


# ── LLM Resolution Prompts ────────────────────────────────────

_LLM_RESOLUTION_SYSTEM = """You are an entity resolution specialist.
Determine if a new entity refers to the same real-world entity as any entity in an existing list."""

_LLM_RESOLUTION_PROMPT = """NEW ENTITY: {new_name} (type: {new_type})

EXISTING CANDIDATES:
{candidates}

# MATCHING RULES
1. Name variants: "张三" = "三哥" = "Zhang San" = "Z. San"
2. Abbreviations: "React" = "React.js" = "ReactJS", "Postgres" = "PostgreSQL"
3. Same person with title: "CEO" = "张总" (only if context confirms same person)
4. Case differences: "python" = "Python"
5. DO NOT match: Different people with same surname ("张三" ≠ "张四")
6. DO NOT match: Different versions ("Python 3.11" ≠ "Python 3.12")
7. When uncertain: Do NOT match. Precision > recall.

OUTPUT (JSON):
{{"match": true/false, "matched_name": "name or null", "confidence": 0.0-1.0, "reasoning": "brief explanation"}}"""


class EntityResolver:
    """Entity deduplication and alias resolution."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._init_tables()

    def _init_tables(self):
        """Add aliases column to entity_relations if missing."""
        try:
            self.conn.execute(
                "ALTER TABLE entity_relations ADD COLUMN aliases TEXT DEFAULT '[]'"
            )
            self.conn.commit()
        except Exception:
            pass

    def resolve(self, entity_name: str) -> Dict[str, Any]:
        """Resolve an entity name to its canonical form.

        Returns:
            {"canonical": str, "aliases": [str], "confidence": float, "action": "merged"|"new"}
        """
        name = entity_name.strip()
        if not name or len(name) < 2:
            return {"canonical": name, "aliases": [], "confidence": 0, "action": "new"}

        # 1. Exact match
        existing = self._find_exact(name)
        if existing:
            return {"canonical": existing, "aliases": [], "confidence": 1.0, "action": "exact"}

        # 2. Fuzzy match via FTS5
        candidates = self._fuzzy_search(name, limit=5)
        if not candidates:
            return {"canonical": name, "aliases": [], "confidence": 0, "action": "new"}

        # 3. Pick best candidate
        best = candidates[0]
        if best["score"] > 0.8:
            return {
                "canonical": best["canonical"],
                "aliases": [name],
                "confidence": best["score"],
                "action": "merged",
            }

        return {"canonical": name, "aliases": [], "confidence": 0, "action": "new"}

    def resolve_with_llm(self, entity_name: str, entity_type: str = "Unknown",
                         llm_call=None) -> Dict[str, Any]:
        """Resolve using LLM for ambiguous cases.

        Args:
            entity_name: Name to resolve
            entity_type: Entity type hint
            llm_call: Callable(system, prompt) -> str (JSON response)

        Returns:
            {"canonical": str, "matched": bool, "confidence": float, "reasoning": str}
        """
        if llm_call is None:
            # Fallback to fuzzy-only resolution
            result = self.resolve(entity_name)
            return {
                "canonical": result["canonical"],
                "matched": result["action"] in ("exact", "merged"),
                "confidence": result["confidence"],
                "reasoning": f"fuzzy match, action={result['action']}",
            }

        # 1. Get fuzzy candidates first (fast filter)
        candidates = self._fuzzy_search(entity_name, limit=5)
        if not candidates:
            return {
                "canonical": entity_name,
                "matched": False,
                "confidence": 0,
                "reasoning": "no candidates found",
            }

        # 2. LLM precision check
        candidate_list = "\n".join(
            f"  - name: {c['canonical']}, similarity: {c['score']:.2f}"
            for c in candidates
        )

        try:
            response = llm_call(
                _LLM_RESOLUTION_SYSTEM,
                _LLM_RESOLUTION_PROMPT.format(
                    new_name=entity_name,
                    new_type=entity_type,
                    candidates=candidate_list,
                )
            )

            # Parse response
            if isinstance(response, str):
                result = json.loads(response)
            else:
                result = response

            if result.get("match") and result.get("matched_name"):
                return {
                    "canonical": result["matched_name"],
                    "matched": True,
                    "confidence": result.get("confidence", 0.8),
                    "reasoning": result.get("reasoning", "LLM match"),
                }
            else:
                return {
                    "canonical": entity_name,
                    "matched": False,
                    "confidence": result.get("confidence", 0),
                    "reasoning": result.get("reasoning", "LLM no match"),
                }

        except Exception as e:
            logger.warning(f"LLM resolution failed for '{entity_name}': {e}")
            # Fallback to fuzzy
            result = self.resolve(entity_name)
            return {
                "canonical": result["canonical"],
                "matched": result["action"] in ("exact", "merged"),
                "confidence": result["confidence"],
                "reasoning": f"LLM failed, fuzzy fallback: {e}",
            }

    def _find_exact(self, name: str) -> Optional[str]:
        """Check if entity exists exactly."""
        row = self.conn.execute(
            "SELECT entity_a FROM entity_relations "
            "WHERE LOWER(entity_a) = LOWER(?) LIMIT 1",
            (name,)
        ).fetchone()
        return row["entity_a"] if row else None

    def _fuzzy_search(self, name: str, limit: int = 5) -> List[Dict]:
        """Find similar entities via FTS5 + string similarity."""
        # FTS5 search
        candidates = []
        try:
            rows = self.conn.execute(
                "SELECT DISTINCT entity_a AS name FROM entity_relations "
                "WHERE entity_a LIKE ? OR entity_a LIKE ? OR entity_a LIKE ? "
                "LIMIT ?",
                (f"%{name}%", f"{name}%", f"%{name}", limit * 3)
            ).fetchall()
            for r in rows:
                score = self._similarity(name, r["name"])
                if score > 0.4:
                    candidates.append({"canonical": r["name"], "score": score})
        except Exception:
            pass

        # Also check entity_b
        try:
            rows = self.conn.execute(
                "SELECT DISTINCT entity_b AS name FROM entity_relations "
                "WHERE entity_b LIKE ? OR entity_b LIKE ? LIMIT ?",
                (f"%{name}%", f"%{name}", limit * 2)
            ).fetchall()
            for r in rows:
                score = self._similarity(name, r["name"])
                if score > 0.4:
                    candidates.append({"canonical": r["name"], "score": score})
        except Exception:
            pass

        # Dedup and sort
        seen = set()
        unique = []
        for c in candidates:
            if c["canonical"] not in seen:
                seen.add(c["canonical"])
                unique.append(c)
        unique.sort(key=lambda x: -x["score"])
        return unique[:limit]

    def merge(self, source: str, target: str) -> Dict[str, Any]:
        """Merge source entity into target (source becomes alias of target).

        Updates entity_relations: replace source with target.
        """
        if source.lower() == target.lower():
            return {"status": "same_entity"}

        # Update entity_a
        self.conn.execute(
            "UPDATE entity_relations SET entity_a = ? WHERE entity_a = ?",
            (target, source)
        )
        # Update entity_b
        self.conn.execute(
            "UPDATE entity_relations SET entity_b = ? WHERE entity_b = ?",
            (target, source)
        )

        # Update adjacency cache
        try:
            self.conn.execute(
                "UPDATE adjacency_cache SET entity_name = ? WHERE entity_name = ?",
                (target, source)
            )
            self.conn.execute(
                "UPDATE adjacency_cache SET neighbor_name = ? WHERE neighbor_name = ?",
                (target, source)
            )
        except Exception:
            pass

        # Merge hit counts (dedup same edges)
        self.conn.execute("""
            DELETE FROM entity_relations
            WHERE entity_a = entity_b
              AND id NOT IN (
                  SELECT MIN(id) FROM entity_relations
                  WHERE entity_a = entity_b GROUP BY entity_a, entity_b, relation_type
              )
        """)

        self.conn.commit()
        logger.info("EntityResolver: merged '%s' → '%s'", source, target)
        return {"status": "merged", "source": source, "target": target}

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """String similarity (Jaccard on character bigrams)."""
        a_lower, b_lower = a.lower(), b.lower()
        if a_lower == b_lower:
            return 1.0
        if a_lower in b_lower or b_lower in a_lower:
            return 0.85

        # Character bigram Jaccard
        def bigrams(s):
            return set(s[i:i+2] for i in range(len(s)-1)) if len(s) > 1 else {s}

        bg_a = bigrams(a_lower)
        bg_b = bigrams(b_lower)
        if not bg_a or not bg_b:
            return 0.0
        return len(bg_a & bg_b) / len(bg_a | bg_b)

    def list_aliases(self, entity: str) -> List[str]:
        """Get all known aliases for an entity."""
        row = self.conn.execute(
            "SELECT aliases FROM entity_relations "
            "WHERE LOWER(entity_a) = LOWER(?) LIMIT 1",
            (entity,)
        ).fetchone()
        if row and row["aliases"]:
            try:
                return json.loads(row["aliases"])
            except Exception:
                pass
        return []

    def add_alias(self, entity: str, alias: str):
        """Add an alias to an entity."""
        aliases = self.list_aliases(entity)
        if alias not in aliases:
            aliases.append(alias)
            self.conn.execute(
                "UPDATE entity_relations SET aliases = ? "
                "WHERE LOWER(entity_a) = LOWER(?)",
                (json.dumps(aliases), entity)
            )
            self.conn.commit()

    def resolution_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Recent resolution decisions (audit trail)."""
        # Store in a simple log table
        try:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS resolve_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT, target TEXT, action TEXT,
                    confidence REAL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            rows = self.conn.execute(
                "SELECT * FROM resolve_log ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _log_resolution(self, source: str, target: str, action: str, confidence: float):
        """Record a resolution decision."""
        try:
            self.conn.execute(
                "INSERT INTO resolve_log (source, target, action, confidence) "
                "VALUES (?, ?, ?, ?)",
                (source, target, action, confidence)
            )
        except Exception:
            pass
