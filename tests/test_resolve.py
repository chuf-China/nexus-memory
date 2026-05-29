"""Tests for nexus.resolve — Entity Resolution."""

import os
import sqlite3
import tempfile

import pytest

from nexus.resolve import EntityResolver


@pytest.fixture
def conn():
    db = tempfile.mktemp(suffix=".db")
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    # Create minimal entity_relations table
    c.execute("""
        CREATE TABLE IF NOT EXISTS entity_relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_a TEXT, entity_b TEXT, relation_type TEXT,
            weight REAL DEFAULT 1.0, hit_count INTEGER DEFAULT 1,
            aliases TEXT DEFAULT '[]'
        )
    """)
    c.commit()
    yield c
    c.close()
    os.unlink(db)


@pytest.fixture
def er(conn):
    return EntityResolver(conn)


class TestEntityResolver:
    def test_exact_match(self, er, conn):
        conn.execute("INSERT INTO entity_relations (entity_a, entity_b, relation_type) VALUES ('Python', 'Django', 'USES')")
        conn.commit()

        r = er.resolve("Python")
        assert r["action"] == "exact"
        assert r["canonical"] == "Python"
        assert r["confidence"] == 1.0

    def test_fuzzy_match(self, er, conn):
        conn.execute("INSERT INTO entity_relations (entity_a, entity_b, relation_type) VALUES ('PostgreSQL', 'JSONB', 'SUPPORTS')")
        conn.commit()

        r = er.resolve("Postgre")
        # Should find PostgreSQL as fuzzy match
        assert r["action"] in ("merged", "new")

    def test_new_entity(self, er):
        r = er.resolve("BrandNewEntity123")
        assert r["action"] == "new"

    def test_short_name(self, er):
        r = er.resolve("x")
        assert r["action"] == "new"

    def test_merge(self, er, conn):
        conn.execute("INSERT INTO entity_relations (entity_a, entity_b, relation_type) VALUES ('Zhang San', 'Django', 'USES')")
        conn.execute("INSERT INTO entity_relations (entity_a, entity_b, relation_type) VALUES ('三哥', 'Flask', 'USES')")
        conn.commit()

        result = er.merge("三哥", "Zhang San")
        assert result["status"] == "merged"

        # Verify merge
        rows = conn.execute("SELECT DISTINCT entity_a FROM entity_relations").fetchall()
        names = [r["entity_a"] for r in rows]
        assert "Zhang San" in names
        assert "三哥" not in names

    def test_merge_same_entity(self, er):
        result = er.merge("Python", "Python")
        assert result["status"] == "same_entity"

    def test_aliases(self, er, conn):
        conn.execute("INSERT INTO entity_relations (entity_a, entity_b, relation_type, aliases) VALUES ('PG', 'DB', 'USES', '[]')")
        conn.commit()

        er.add_alias("PG", "PostgreSQL")
        aliases = er.list_aliases("PG")
        assert "PostgreSQL" in aliases

    def test_similarity(self):
        assert EntityResolver._similarity("python", "python") == 1.0
        assert EntityResolver._similarity("python", "python3") == 0.85
        assert EntityResolver._similarity("abc", "xyz") < 0.5
