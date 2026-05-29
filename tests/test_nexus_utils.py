"""Tests for nexus_utils module."""

import pytest
from src.nexus_utils import (
    normalize,
    content_hash,
    segment_fts,
    empty_scores,
    generate_summary,
    incr_score,
    max_domain,
)


class TestNormalize:
    """Test text normalization."""

    def test_normalize_whitespace(self):
        result = normalize("  hello   world  ")
        assert result == "hello world"

    def test_normalize_newlines(self):
        result = normalize("hello\n\nworld")
        assert "hello" in result
        assert "world" in result

    def test_normalize_tabs(self):
        result = normalize("hello\tworld")
        assert "\t" not in result


class TestContentHash:
    """Test content hashing."""

    def test_hash_returns_string(self):
        result = content_hash("test content")
        assert isinstance(result, str)

    def test_hash_deterministic(self):
        hash1 = content_hash("test content")
        hash2 = content_hash("test content")
        assert hash1 == hash2

    def test_hash_different_content(self):
        hash1 = content_hash("content 1")
        hash2 = content_hash("content 2")
        assert hash1 != hash2


class TestSegmentFts:
    """Test FTS segmentation."""

    def test_segment_basic(self):
        result = segment_fts("hello world")
        assert isinstance(result, str)

    def test_segment_chinese(self):
        result = segment_fts("用户喜欢Python")
        assert isinstance(result, str)


class TestEmptyScores:
    """Test empty scores creation."""

    def test_empty_scores_returns_dict(self):
        result = empty_scores()
        assert isinstance(result, dict)

    def test_empty_scores_has_domains(self):
        result = empty_scores()
        assert len(result) > 0


class TestGenerateSummary:
    """Test summary generation."""

    def test_short_text(self):
        result = generate_summary("hello world")
        assert result == "hello world"

    def test_long_text_truncated(self):
        long_text = "a" * 300
        result = generate_summary(long_text, max_len=100)
        assert len(result) <= 100


class TestIncrScore:
    """Test score increment."""

    def test_incr_score(self):
        scores = empty_scores()
        result = incr_score(scores, "test_domain")
        assert isinstance(result, dict)


class TestMaxDomain:
    """Test max domain extraction."""

    def test_max_domain_empty(self):
        scores = empty_scores()
        result = max_domain(scores)
        assert isinstance(result, tuple)
