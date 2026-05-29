#!/usr/bin/env python3
"""test_nexus_utils.py — NexusUtils单元测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nexus.utils import (
    normalize, content_hash, segment_fts, empty_scores,
    generate_summary, incr_score, max_domain
)


def test_normalize():
    assert normalize("  Hello   World  ") == "hello world"
    assert normalize("测试\n\n\n内容") == "测试 内容"
    assert normalize("") == ""


def test_generate_summary():
    short = generate_summary("短内容")
    assert short == "短内容"

    long = generate_summary("A" * 500, max_len=50)
    assert len(long) <= 53  # + "..."

    empty = generate_summary("")
    assert empty == ""


def test_incr_score():
    scores = empty_scores()
    scores = incr_score(scores, "coding")
    assert scores["coding"] == 1
    scores = incr_score(scores, "coding")
    assert scores["coding"] == 2
    scores = incr_score(scores, "design")
    assert scores["design"] == 1


def test_max_domain():
    scores = empty_scores()
    assert max_domain(scores) == (None, 0)

    scores["coding"] = 5
    scores["design"] = 3
    domain, val = max_domain(scores)
    assert domain == "coding"
    assert val == 5
