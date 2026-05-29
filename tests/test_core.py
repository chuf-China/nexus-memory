#!/usr/bin/env python3
"""test_nexus_core.py — NexusCore单元测试"""

import json
import os
import sys
import tempfile
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from nexus.core import NexusCore
from nexus.utils import content_hash, segment_fts, empty_scores


def test_content_hash():
    """测试内容哈希生成。"""
    h1 = content_hash("测试内容")
    h2 = content_hash("测试内容")
    h3 = content_hash("不同内容")

    assert h1 == h2, "相同内容应产生相同哈希"
    assert h1 != h3, "不同内容应产生不同哈希"
    assert len(h1) == 16, "哈希长度应为16"
    print("✓ test_content_hash")


def test_segment_fts():
    """测试FTS5分词。"""
    # 中文分词
    result = segment_fts("你好世界")
    assert "你" in result, "应包含单字分词"
    assert "你好" in result, "应包含双字分词"

    # 英文直接通过
    result = segment_fts("hello world")
    assert "hello" in result, "英文应直接通过"
    assert "world" in result

    # 混合文本
    result = segment_fts("Python是好的")
    assert "Python" in result
    assert "是" in result

    print("✓ test_segment_fts")


def test_empty_scores():
    """测试空分数生成。"""
    scores = empty_scores()
    assert isinstance(scores, dict)
    assert "identity" in scores
    assert "workflow" in scores
    assert all(v == 0 for v in scores.values())
    print("✓ test_empty_scores")


def test_nexus_core_crud():
    """测试NexusCore的CRUD操作。"""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    try:
        nc = NexusCore(db_path)

        # Write
        result = nc.write(
            content="测试知识条目",
            user_id="test_user",
        )
        assert result["success"], "写入应成功"
        kid = result["id"]
        assert kid > 0, "写入应返回有效ID"

        # Read back via search
        results = nc.search("测试", user_id="test_user", mode="fts")
        assert len(results) > 0, "搜索应返回结果"
        assert any("测试知识条目" in r["content"] for r in results)

        # Feedback
        nc.feedback(kid, "positive", user_id="test_user")

        # Close
        nc.close()
        print("✓ test_nexus_core_crud")
    finally:
        os.unlink(db_path)
        # Cleanup WAL/SHM files
        for suffix in ["-wal", "-shm"]:
            try:
                os.unlink(db_path + suffix)
            except FileNotFoundError:
                pass


def test_nexus_core_search_modes():
    """测试不同搜索模式。"""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    try:
        nc = NexusCore(db_path)

        # 写入测试数据
        nc.write("Python编程技巧", user_id="test")
        nc.write("JavaScript异步处理", user_id="test")

        # FTS搜索
        results = nc.search("Python", user_id="test", mode="fts")
        assert len(results) > 0, "FTS搜索应返回结果"

        # LIKE搜索 (fallback)
        results = nc.search("Python", user_id="test", mode="fts")
        assert len(results) > 0

        nc.close()
        print("✓ test_nexus_core_search_modes")
    finally:
        os.unlink(db_path)
        for suffix in ["-wal", "-shm"]:
            try:
                os.unlink(db_path + suffix)
            except FileNotFoundError:
                pass


def test_nexus_core_feedback():
    """测试反馈机制。"""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    try:
        nc = NexusCore(db_path)

        # 写入
        result = nc.write("反馈测试知识", user_id="test")
        kid = result["id"]

        # Positive feedback
        nc.feedback(kid, "positive")
        # Negative feedback
        nc.feedback(kid, "negative")

        nc.close()
        print("✓ test_nexus_core_feedback")
    finally:
        os.unlink(db_path)
        for suffix in ["-wal", "-shm"]:
            try:
                os.unlink(db_path + suffix)
            except FileNotFoundError:
                pass


def run_all_tests():
    """运行所有测试。"""
    print("\n" + "=" * 50)
    print("  Nexus 单元测试")
    print("=" * 50 + "\n")

    tests = [
        test_content_hash,
        test_segment_fts,
        test_empty_scores,
        test_nexus_core_crud,
        test_nexus_core_search_modes,
        test_nexus_core_feedback,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__}: {e}")
            failed += 1

    print("\n" + "-" * 50)
    print(f"  通过: {passed}/{passed + failed}")
    print("=" * 50 + "\n")

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
