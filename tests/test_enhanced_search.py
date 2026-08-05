#!/usr/bin/env python3
"""test_enhanced_search.py — 增强检索回归测试

回归覆盖：外部评估报告指出的 expand_query 硬依赖 tests.eval_locomo
（该模块不在主仓库，缺失时必须降级而非崩溃）。
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.nexus_core import NexusCore
from src.nexus_search import expand_query, EnhancedSearch


def _cleanup(path):
    os.unlink(path)
    for s in ["-wal", "-shm"]:
        try:
            os.unlink(path + s)
        except FileNotFoundError:
            pass


def test_expand_query_no_locomo_dependency():
    """expand_query 不应因缺少 tests.eval_locomo 而崩溃。"""
    queries = expand_query("What did Caroline do yesterday?")
    assert isinstance(queries, list)
    assert 1 <= len(queries) <= 4


def test_enhanced_search_runs():
    """EnhancedSearch.search 端到端可运行。"""
    f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    f.close()
    nc = NexusCore(f.name)
    try:
        nc.write("Caroline went to the LGBTQ support group yesterday",
                 source_session_id="test")
        es = EnhancedSearch(nc)
        results = es.search("What did Caroline do yesterday?", limit=5)
        assert isinstance(results, list)
    finally:
        nc.close()
        _cleanup(f.name)
