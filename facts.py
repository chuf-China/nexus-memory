"""nexus_facts.py — Fact Store (结构化事实)

Stores and queries structured facts as subject-predicate-object triples.

Features:
  - Subject-predicate-object storage with confidence and source
  - Automatic conflict detection: new facts supersede old ones on same S-P
  - LLM-based fact extraction from unstructured text
  - Hybrid search: exact SPO lookup + fuzzy + semantic

Usage:
  from .facts import FactStore, FactExtractor
  fs = FactStore(conn)
  fs.add("PostgreSQL", "version", "16", confidence=0.95, source="docs")
  results = fs.query(subject="PostgreSQL", predicate="version")
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subject         TEXT NOT NULL,
    predicate       TEXT NOT NULL,
    object          TEXT NOT NULL,
    confidence      REAL DEFAULT 1.0,
    source          TEXT DEFAULT 'unknown',
    superseded_by   INTEGER DEFAULT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    user_id         TEXT DEFAULT 'default'
);

CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);
CREATE INDEX IF NOT EXISTS idx_facts_predicate ON facts(predicate);
CREATE INDEX IF NOT EXISTS idx_facts_sp ON facts(subject, predicate);
CREATE INDEX IF NOT EXISTS idx_facts_active ON facts(superseded_by) WHERE superseded_by IS NULL;
"""


class FactStore:
    """Structured fact storage with conflict detection."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript(_SCHEMA_SQL)
        self.conn.commit()

    def add(self, subject: str, predicate: str, obj: str,
            confidence: float = 1.0, source: str = "unknown",
            user_id: str = "default",
            use_llm_conflict: bool = False) -> Dict[str, Any]:
        """Add a fact. Supersedes existing fact with same subject+predicate.

        When use_llm_conflict=True, uses LLM to classify the conflict type
        (CONTRADICTS/UPDATES/REFINES/DUPLICATE/COMPATIBLE) for nuanced handling.

        Returns: {"id": int, "status": "new"|"superseded"|"merged", "superseded": [int]}
        """
        subject = subject.strip()
        predicate = predicate.strip()
        obj = obj.strip()
        if not all([subject, predicate, obj]):
            return {"status": "error", "reason": "empty fields"}

        # Find existing active facts with same subject+predicate
        existing = self.conn.execute(
            "SELECT id, object, confidence, created_at FROM facts "
            "WHERE subject = ? AND predicate = ? AND superseded_by IS NULL",
            (subject, predicate)
        ).fetchall()

        superseded_ids = []

        for row in existing:
            # If same object, update confidence (merge evidence)
            if row["object"].lower() == obj.lower():
                new_conf = max(row["confidence"], confidence)
                self.conn.execute(
                    "UPDATE facts SET confidence = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    (new_conf, row["id"])
                )
                self.conn.commit()
                return {"id": row["id"], "status": "merged", "superseded": []}

            # Different object: LLM conflict detection (optional)
            if use_llm_conflict:
                action = self._llm_classify_conflict(
                    {"subject": subject, "predicate": predicate,
                     "object": row["object"], "confidence": row["confidence"],
                     "created_at": row.get("created_at", "")},
                    {"subject": subject, "predicate": predicate,
                     "object": obj, "confidence": confidence},
                )
                if action == "skip":
                    return {"id": row["id"], "status": "skip", "superseded": []}
                elif action == "merge":
                    # Keep both facts (different aspects of same S-P)
                    continue
                # Default: supersede (CONTRADICTS or UPDATES)
            superseded_ids.append(row["id"])

        # Insert new fact
        cur = self.conn.execute(
            "INSERT INTO facts (subject, predicate, object, confidence, source, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (subject, predicate, obj, confidence, source, user_id)
        )
        new_id = cur.lastrowid

        # Mark old facts as superseded
        for old_id in superseded_ids:
            self.conn.execute(
                "UPDATE facts SET superseded_by = ? WHERE id = ?",
                (new_id, old_id)
            )

        self.conn.commit()
        return {
            "id": new_id,
            "status": "superseded" if superseded_ids else "new",
            "superseded": superseded_ids,
        }

    def query(self, subject: Optional[str] = None,
              predicate: Optional[str] = None,
              obj: Optional[str] = None,
              include_superseded: bool = False,
              limit: int = 20) -> List[Dict[str, Any]]:
        """Query facts by subject, predicate, or object."""
        conditions = []
        params: list = []

        if not include_superseded:
            conditions.append("superseded_by IS NULL")

        if subject:
            conditions.append("LOWER(subject) = LOWER(?)")
            params.append(subject)
        if predicate:
            conditions.append("LOWER(predicate) = LOWER(?)")
            params.append(predicate)
        if obj:
            conditions.append("LOWER(object) LIKE LOWER(?)")
            params.append(f"%{obj}%")

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM facts WHERE {where} ORDER BY confidence DESC, created_at DESC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def search_text(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search facts by text across all fields."""
        pattern = f"%{query}%"
        rows = self.conn.execute(
            "SELECT * FROM facts "
            "WHERE superseded_by IS NULL "
            "AND (subject LIKE ? OR predicate LIKE ? OR object LIKE ?) "
            "ORDER BY confidence DESC LIMIT ?",
            (pattern, pattern, pattern, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_by_id(self, fact_id: int) -> Optional[Dict[str, Any]]:
        """Get a fact by ID."""
        row = self.conn.execute(
            "SELECT * FROM facts WHERE id = ?", (fact_id,)
        ).fetchone()
        return dict(row) if row else None

    def supersede(self, fact_id: int, new_object: str,
                  confidence: float = 1.0, source: str = "manual") -> Dict[str, Any]:
        """Manually supersede a fact with a new value."""
        old = self.get_by_id(fact_id)
        if not old:
            return {"status": "error", "reason": "fact not found"}

        return self.add(
            old["subject"], old["predicate"], new_object,
            confidence=confidence, source=source
        )

    def delete(self, fact_id: int) -> bool:
        """Soft-delete a fact (supersede with NULL)."""
        cur = self.conn.execute(
            "UPDATE facts SET superseded_by = -1 WHERE id = ? AND superseded_by IS NULL",
            (fact_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def _llm_classify_conflict(self, existing: Dict, new: Dict) -> str:
        """Use LLM to classify conflict type between existing and new fact.

        Returns: "supersede"|"merge"|"add"|"skip"
        Falls back to "supersede" if LLM unavailable.
        """
        try:
            from .local import get_client as _get_llm
            from .prompts import (
                build_conflict_prompt, validate_conflict_detection,
            )
            client = _get_llm()
            if client is None:
                return "supersede"

            sys_prompt, user_prompt = build_conflict_prompt(existing, new)
            resp = client.chat([
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ])
            result = validate_conflict_detection(resp)
            if result:
                return result.action
        except Exception as e:
            logger.debug("LLM conflict classification failed: %s", e)
        return "supersede"

    def history(self, subject: str, predicate: str) -> List[Dict[str, Any]]:
        """Get all historical values for a subject-predicate pair."""
        rows = self.conn.execute(
            "SELECT * FROM facts WHERE subject = ? AND predicate = ? "
            "ORDER BY created_at DESC",
            (subject, predicate)
        ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> Dict[str, Any]:
        """Fact store statistics."""
        total = self.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        active = self.conn.execute(
            "SELECT COUNT(*) FROM facts WHERE superseded_by IS NULL"
        ).fetchone()[0]
        superseded = total - active
        subjects = self.conn.execute(
            "SELECT COUNT(DISTINCT subject) FROM facts WHERE superseded_by IS NULL"
        ).fetchone()[0]
        return {
            "total_facts": total,
            "active_facts": active,
            "superseded_facts": superseded,
            "unique_subjects": subjects,
        }

    def to_graph_edges(self) -> List[Tuple[str, str, str]]:
        """Export active facts as graph edges (subject, predicate, object)."""
        rows = self.conn.execute(
            "SELECT subject, predicate, object FROM facts "
            "WHERE superseded_by IS NULL"
        ).fetchall()
        return [(r["subject"], r["predicate"], r["object"]) for r in rows]


class FactExtractor:
    """LLM-based structured fact extraction from unstructured text."""

    _EXTRACT_PROMPT = """You are an expert fact extractor that extracts fact triples from text.

EXTRACTION RULES:
1. Extract subject-predicate-object triples where subject and object are entity names, not full sentences
2. Use SCREAMING_SNAKE_CASE for predicates (e.g., USES, DEPENDS_ON, VERSION_IS, LOCATED_IN, WORKS_AT, CREATED_BY, CONFIGURED_AS, REPLACED_BY, CAUSES, SOLVES, DEPLOYED_ON)
3. Be specific with predicates: "VERSION_IS" not "IS", "USES" not "HAS"
4. Include only factual claims, not opinions or questions
5. Extract facts even when phrased negatively ("X is not Y" → the negation is part of the fact)
6. When time information is available, include valid_at/invalid_at in ISO 8601 format
7. Each fact must involve two DISTINCT entities (source ≠ target)
8. Include confidence score (0.0-1.0): 1.0 for explicit statements, 0.7 for strong implications, 0.5 for inferences

EXCLUSION LIST (do NOT extract facts about):
- Temporary states ("today is raining")
- Hypothetical scenarios ("if X then Y" — unless stated as a rule)
- Opinions ("I think X is better")
- Questions ("is X Y?")
- Generic/abstract statements ("life is good")

CONTEXT:
{context}

Text:
{text}

Output JSON array:
[
  {{"subject": "entity", "predicate": "SCREAMING_SNAKE_CASE", "object": "value", "confidence": 0.9, "valid_at": null, "invalid_at": null}},
  ...
]

If no facts found, return [].

Facts (JSON):"""

    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self.conn = conn

    def extract(self, text: str, source: str = "conversation") -> List[Dict[str, Any]]:
        """Extract facts from text using LLM.

        Returns list of extracted facts (not yet stored).
        """
        # Use a simple regex-based fallback if no LLM available
        facts = self._regex_extract(text)

        if self.conn and facts:
            # Store extracted facts
            fs = FactStore(self.conn)
            results = []
            for f in facts:
                result = fs.add(
                    f["subject"], f["predicate"], f["object"],
                    confidence=0.8, source=source
                )
                results.append(result)
            return results
        return facts

    def extract_from_conversation(self, user_msg: str, assistant_msg: str,
                                   source: str = "conversation") -> List[Dict[str, Any]]:
        """Extract facts from a conversation turn pair."""
        combined = f"User: {user_msg}\nAssistant: {assistant_msg}"
        return self.extract(combined, source=source)

    @staticmethod
    def _regex_extract(text: str) -> List[Dict[str, str]]:
        """Simple regex-based fact extraction (no LLM needed)."""
        facts = []

        # Pattern: "X is Y" or "X are Y"
        for m in re.finditer(r'([A-Z][a-zA-Z\s]{1,30})\s+(?:is|are)\s+([^.!?]{3,50})', text):
            facts.append({
                "subject": m.group(1).strip(),
                "predicate": "is",
                "object": m.group(2).strip(),
            })

        # Pattern: "X uses Y" / "X has Y" / "X supports Y"
        for m in re.finditer(r'([A-Z][a-zA-Z\s]{1,30})\s+(uses?|has|supports?|runs?|needs?)\s+([^.!?]{3,50})', text):
            facts.append({
                "subject": m.group(1).strip(),
                "predicate": m.group(2).strip().lower(),
                "object": m.group(3).strip(),
            })

        # Pattern: "X version Y" or "X version is Y"
        for m in re.finditer(r'([A-Z][a-zA-Z\s]{1,30})\s+version\s+(?:is\s+)?([0-9][^.!?]{1,20})', text):
            facts.append({
                "subject": m.group(1).strip(),
                "predicate": "version",
                "object": m.group(2).strip(),
            })

        # Dedup
        seen = set()
        unique = []
        for f in facts:
            key = (f["subject"].lower(), f["predicate"].lower(), f["object"].lower())
            if key not in seen:
                seen.add(key)
                unique.append(f)
        return unique
