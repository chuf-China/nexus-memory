"""Tests for nexus.episodes — Episode Store."""

import os
import sqlite3
import tempfile

import pytest

from nexus.episodes import EpisodeStore


@pytest.fixture
def conn():
    db = tempfile.mktemp(suffix=".db")
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    yield c
    c.close()
    os.unlink(db)


@pytest.fixture
def es(conn):
    return EpisodeStore(conn)


class TestEpisodeStore:
    def test_record_and_get_session(self, es):
        sid = "test-session-1"
        es.record(sid, "user", "Hello world")
        es.record(sid, "assistant", "Hi there!")

        turns = es.get_session(sid)
        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert turns[1]["role"] == "assistant"

    def test_record_batch(self, es):
        turns = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
        ]
        count = es.record_batch("batch-session", turns)
        assert count == 3

    def test_search(self, es):
        es.record("s1", "user", "PostgreSQL is a database")
        es.record("s1", "assistant", "Yes it is")

        results = es.search("PostgreSQL")
        assert len(results) >= 1

    def test_list_sessions(self, es):
        es.record("s1", "user", "hello")
        es.record("s2", "user", "world")

        sessions = es.list_sessions()
        assert len(sessions) == 2

    def test_delete_session(self, es):
        es.record("del-me", "user", "delete me")
        count = es.delete_session("del-me")
        assert count == 1
        assert es.get_session("del-me") == []

    def test_stats(self, es):
        es.record("s1", "user", "a")
        es.record("s1", "assistant", "b")
        es.record("s2", "user", "c")

        stats = es.stats()
        assert stats["total_episodes"] == 3
        assert stats["total_sessions"] == 2

    def test_get_by_topic(self, es):
        es.record("s1", "user", "topic msg", topic="python")
        es.record("s1", "user", "other msg", topic="java")

        results = es.get_by_topic("python")
        assert len(results) == 1

    def test_empty_session(self, es):
        assert es.get_session("nonexistent") == []
