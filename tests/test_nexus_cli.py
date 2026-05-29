"""Tests for nexus_cli module."""

import json
import os
import tempfile

import pytest

from src.nexus_cli import health, list_knowledge, stats


class TestHealth:
    """Test health check."""

    def test_health_returns_dict(self):
        result = health()
        assert isinstance(result, dict)

    def test_health_has_backup_info(self):
        result = health()
        assert "backup_exists" in result or "active_count" in result


class TestListKnowledge:
    """Test knowledge listing."""

    def test_list_returns_list(self):
        result = list_knowledge()
        assert isinstance(result, list)

    def test_list_with_limit(self):
        result = list_knowledge(limit=5)
        assert len(result) <= 5


class TestStats:
    """Test statistics."""

    def test_stats_returns_dict(self):
        result = stats()
        assert isinstance(result, dict)
