"""Tests for NexusCore - basic functionality."""

import os
import sqlite3
import pytest
from src.nexus_core import NexusCore


@pytest.fixture
def nexus(tmp_path):
    """Create a fresh NexusCore instance for each test."""
    db_path = str(tmp_path / "test.db")
    n = NexusCore(db_path)
    yield n
    n.close()


class TestNexusCoreInit:
    """Test NexusCore initialization."""

    def test_init_creates_db(self, tmp_path):
        db_path = str(tmp_path / "new.db")
        nexus = NexusCore(db_path)
        assert os.path.exists(db_path)
        nexus.close()

    def test_init_with_valid_path(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        nexus = NexusCore(db_path)
        assert nexus.db_path == db_path
        nexus.close()


class TestNexusCoreWrite:
    """Test knowledge writing."""

    def test_write_returns_dict(self, nexus):
        result = nexus.write("Test content")
        assert isinstance(result, dict)

    def test_write_with_user_id(self, nexus):
        result = nexus.write("Test content", user_id="user_1")
        assert isinstance(result, dict)

    def test_write_with_confidence(self, nexus):
        result = nexus.write("Test content", initial_confidence=0.9)
        assert isinstance(result, dict)

    def test_write_stores_in_db(self, nexus):
        nexus.write("Test content")
        conn = sqlite3.connect(nexus.db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM unified_knowledge")
        count = cursor.fetchone()[0]
        conn.close()
        assert count >= 1

    def test_write_multiple(self, nexus):
        for i in range(5):
            nexus.write(f"Content {i}")
        conn = sqlite3.connect(nexus.db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM unified_knowledge")
        count = cursor.fetchone()[0]
        conn.close()
        assert count == 5


class TestNexusCoreSearch:
    """Test knowledge search."""

    def test_search_returns_list(self, nexus):
        nexus.write("Python is great")
        results = nexus.search("Python")
        assert isinstance(results, list)

    def test_search_with_limit(self, nexus):
        for i in range(10):
            nexus.write(f"Test content {i}")
        results = nexus.search("test", limit=3)
        assert len(results) <= 3

    def test_search_empty_db(self, nexus):
        results = nexus.search("test")
        assert isinstance(results, list)


class TestNexusCoreFeedback:
    """Test feedback system."""

    def test_feedback_with_valid_type(self, nexus):
        result = nexus.write("Test")
        if "id" in result:
            feedback_result = nexus.feedback(result["id"], "confirm")
            assert isinstance(feedback_result, dict)


class TestNexusCoreStats:
    """Test statistics."""

    def test_stats_returns_dict(self, nexus):
        result = nexus.stats()
        assert isinstance(result, dict)


class TestNexusCoreSystemPrompt:
    """Test system prompt generation."""

    def test_system_prompt_block(self, nexus):
        prompt = nexus.system_prompt_block()
        assert isinstance(prompt, str)


class TestNexusCoreConsolidate:
    """Test consolidation."""

    def test_consolidate_returns_dict(self, nexus):
        result = nexus.consolidate()
        assert isinstance(result, dict)


class TestNexusCoreAlerts:
    """Test alerts."""

    def test_get_alerts_returns_list(self, nexus):
        result = nexus.get_alerts()
        assert isinstance(result, list)
