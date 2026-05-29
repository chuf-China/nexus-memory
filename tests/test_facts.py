"""Tests for nexus.facts — Fact Store with conflict detection."""

import os
import sqlite3
import tempfile

import pytest

from nexus.facts import FactExtractor, FactStore


@pytest.fixture
def conn():
    db = tempfile.mktemp(suffix=".db")
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    yield c
    c.close()
    os.unlink(db)


@pytest.fixture
def fs(conn):
    return FactStore(conn)


class TestFactStore:
    def test_add_and_query(self, fs):
        r = fs.add("Python", "version", "3.12", confidence=0.95)
        assert r["status"] == "new"
        assert r["id"] > 0

        results = fs.query(subject="Python")
        assert len(results) == 1
        assert results[0]["object"] == "3.12"

    def test_supersede(self, fs):
        fs.add("Python", "version", "3.11")
        r = fs.add("Python", "version", "3.12")
        assert r["status"] == "superseded"
        assert len(r["superseded"]) == 1

        active = fs.query(subject="Python", predicate="version")
        assert len(active) == 1
        assert active[0]["object"] == "3.12"

    def test_merge_same_value(self, fs):
        fs.add("Redis", "type", "kv-store", confidence=0.8)
        r = fs.add("Redis", "type", "kv-store", confidence=0.9)
        assert r["status"] == "merged"

        results = fs.query(subject="Redis")
        assert len(results) == 1
        assert results[0]["confidence"] == 0.9  # max of 0.8, 0.9

    def test_query_multiple_filters(self, fs):
        fs.add("Python", "version", "3.12")
        fs.add("Python", "creator", "Guido")
        fs.add("Java", "version", "21")

        r = fs.query(subject="Python", predicate="version")
        assert len(r) == 1
        assert r[0]["predicate"] == "version"

    def test_search_text(self, fs):
        fs.add("PostgreSQL", "type", "relational database")
        fs.add("MySQL", "type", "relational database")

        r = fs.search_text("relational")
        assert len(r) == 2

    def test_history(self, fs):
        fs.add("Go", "version", "1.20")
        fs.add("Go", "version", "1.21")
        fs.add("Go", "version", "1.22")

        hist = fs.history("Go", "version")
        assert len(hist) == 3
        objects = [h["object"] for h in hist]
        assert "1.20" in objects
        assert "1.22" in objects

    def test_supersede_method(self, fs):
        r1 = fs.add("Node", "version", "18")
        r2 = fs.supersede(r1["id"], "20")
        assert r2["status"] == "superseded"

    def test_delete(self, fs):
        r = fs.add("Temp", "key", "value")
        assert fs.delete(r["id"]) is True
        active = fs.query(subject="Temp")
        assert len(active) == 0

    def test_stats(self, fs):
        fs.add("A", "x", "1")
        fs.add("A", "x", "2")  # supersedes
        fs.add("B", "y", "3")

        stats = fs.stats()
        assert stats["active_facts"] == 2
        assert stats["superseded_facts"] == 1

    def test_empty_fields_rejected(self, fs):
        r = fs.add("", "predicate", "object")
        assert r["status"] == "error"

    def test_to_graph_edges(self, fs):
        fs.add("X", "USES", "Y")
        fs.add("A", "HAS", "B")
        edges = fs.to_graph_edges()
        assert len(edges) == 2
        assert ("X", "USES", "Y") in edges
        assert ("A", "HAS", "B") in edges


class TestFactExtractor:
    def test_regex_extract(self):
        ext = FactExtractor()
        facts = ext._regex_extract("Python is a programming language. Python uses Django.")
        assert len(facts) >= 1
        subjects = [f["subject"] for f in facts]
        assert any("Python" in s for s in subjects)

    def test_regex_version(self):
        ext = FactExtractor()
        facts = ext._regex_extract("PostgreSQL version is 16")
        assert any(f["predicate"] == "version" for f in facts)
