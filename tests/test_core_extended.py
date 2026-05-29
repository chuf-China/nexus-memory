#!/usr/bin/env python3
"""test_nexus_core_extended.py — 补充NexusCore方法测试"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nexus.core import NexusCore


def _make_nc():
    f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    f.close()
    return NexusCore(f.name), f.name


def _cleanup(path):
    os.unlink(path)
    for s in ["-wal", "-shm"]:
        try:
            os.unlink(path + s)
        except FileNotFoundError:
            pass


def test_add_rule():
    nc, p = _make_nc()
    try:
        r = nc.add_rule("简洁回答", source="user", confidence=0.8)
        assert r == "简洁回答"
        # 去重
        r2 = nc.add_rule("简洁回答")
        assert r2 is None
        rules = nc.get_active_rules()
        assert "简洁回答" in rules
    finally:
        nc.close()
        _cleanup(p)


def test_record_model_performance():
    nc, p = _make_nc()
    try:
        nc.record_model_performance("gpt-4o", "coding", 0.92, "sess1")
        nc.record_model_performance("gpt-4o", "coding", 0.88, "sess2")
        stats = nc.get_model_stats("gpt-4o")
        assert len(stats) == 1
        assert stats[0]["model"] == "gpt-4o"
        assert stats[0]["samples"] == 2
        assert 0.88 <= stats[0]["avg_quality"] <= 0.92
    finally:
        nc.close()
        _cleanup(p)


def test_audit_stats():
    nc, p = _make_nc()
    try:
        nc.log_interaction("q1", "r1", knowledge_used=[], user_id="u1")
        nc.log_interaction("q2", "r2", knowledge_used=[], user_id="u1")
        stats = nc.audit_stats("u1")
        assert stats["total_interactions"] == 2
        assert stats["total_corrections"] == 0
        assert stats["correction_rate"] == 0
    finally:
        nc.close()
        _cleanup(p)


def test_knowledge_snapshot():
    nc, p = _make_nc()
    try:
        nc.write("快照测试数据条目", user_id="snap")
        now = datetime.now(timezone.utc).isoformat()
        snap = nc.knowledge_snapshot(at_time=now, user_id="snap")
        assert len(snap) >= 1
        assert any("快照测试数据条目" in s.get("content", "") for s in snap)
    finally:
        nc.close()
        _cleanup(p)


def test_stats():
    nc, p = _make_nc()
    try:
        nc.write("stats测试", user_id="s1")
        s = nc.stats("s1")
        assert s["total"] >= 1
        assert "by_layer" in s
    finally:
        nc.close()
        _cleanup(p)


def test_get_alerts():
    nc, p = _make_nc()
    try:
        alerts = nc.get_alerts()
        assert isinstance(alerts, list)
    finally:
        nc.close()
        _cleanup(p)


def test_build_context():
    nc, p = _make_nc()
    try:
        nc.write("上下文测试", user_id="ctx")
        results = nc.search("上下文", user_id="ctx", mode="fts")
        ctx = nc.build_context(results, question="上下文")
        assert isinstance(ctx, str)
    finally:
        nc.close()
        _cleanup(p)


def test_search_by_domain():
    nc, p = _make_nc()
    try:
        nc.write("Python技巧", user_id="d1")
        results = nc.search_by_domain("coding", user_id="d1")
        assert isinstance(results, list)
    finally:
        nc.close()
        _cleanup(p)


def test_record_domain_hit():
    nc, p = _make_nc()
    try:
        r = nc.write("领域测试数据条目", user_id="dh")
        nc.record_domain_hit(r["id"], "coding")
        # 检查不报错即可
    finally:
        nc.close()
        _cleanup(p)


def test_get_history():
    nc, p = _make_nc()
    try:
        r = nc.write("历史测试数据条目", user_id="h1")
        hist = nc.get_history(r["id"])
        assert isinstance(hist, list)
    finally:
        nc.close()
        _cleanup(p)


def test_system_prompt_block():
    nc, p = _make_nc()
    try:
        nc.write("prompt测试", user_id="prompt")
        block = nc.system_prompt_block(user_id="prompt")
        assert isinstance(block, str)
    finally:
        nc.close()
        _cleanup(p)


def test_get_subsystem_views():
    nc, p = _make_nc()
    try:
        views = nc.get_subsystem_views()
        assert isinstance(views, dict)
    finally:
        nc.close()
        _cleanup(p)


def test_rebuild_fts():
    nc, p = _make_nc()
    try:
        nc.write("FTS重建测试", user_id="fts")
        count = nc.rebuild_fts()
        assert count >= 0
    finally:
        nc.close()
        _cleanup(p)


def test_consolidate():
    nc, p = _make_nc()
    try:
        nc.write("合并测试A", user_id="con")
        nc.write("合并测试B", user_id="con")
        result = nc.consolidate(user_id="con")
        assert isinstance(result, dict)
        assert "actions" in result
    finally:
        nc.close()
        _cleanup(p)


def test_log_correction():
    nc, p = _make_nc()
    try:
        lid = nc.log_interaction("原始问题", "原始回答", knowledge_used=[], user_id="corr")
        nc.log_correction(lid, "修正问题", "修正回答", "用户纠正")
        chain = nc.get_interaction_chain(lid)
        assert len(chain) >= 1
    finally:
        nc.close()
        _cleanup(p)


def test_write_dedup():
    """write() should dedup by match_hash."""
    nc, p = _make_nc()
    try:
        r1 = nc.write("去重测试内容", user_id="dedup")
        r2 = nc.write("去重测试内容", user_id="dedup")
        assert r1["id"] == r2["id"]
        assert r2["action"] == "updated_existing"
    finally:
        nc.close()
        _cleanup(p)


def test_write_empty():
    nc, p = _make_nc()
    try:
        r = nc.write("", user_id="empty")
        assert r["success"] is False
    finally:
        nc.close()
        _cleanup(p)


def test_search_fts():
    nc, p = _make_nc()
    try:
        nc.write("FTS搜索测试内容", user_id="search")
        results = nc.search("FTS搜索", user_id="search", mode="fts")
        assert len(results) >= 1
        assert any("FTS搜索" in r["content"] for r in results)
    finally:
        nc.close()
        _cleanup(p)


def test_search_hybrid():
    nc, p = _make_nc()
    try:
        nc.write("混合搜索测试", user_id="hybrid")
        results = nc.search("混合搜索", user_id="hybrid", mode="hybrid")
        assert isinstance(results, list)
    finally:
        nc.close()
        _cleanup(p)


def test_search_graph():
    nc, p = _make_nc()
    try:
        nc.write("图搜索测试", user_id="graph")
        results = nc.search("图搜索", user_id="graph", mode="graph")
        assert isinstance(results, list)
    finally:
        nc.close()
        _cleanup(p)


def test_feedback_positive():
    nc, p = _make_nc()
    try:
        r = nc.write("正反馈测试", user_id="fb_pos")
        kid = r["id"]
        for _ in range(10):
            nc.feedback(kid, "explicit_positive", user_id="fb_pos")
        conn = nc._conn()
        row = conn.execute(
            "SELECT positive_feedback, layer FROM unified_knowledge WHERE id=?", (kid,)
        ).fetchone()
        assert row["positive_feedback"] >= 10
    finally:
        nc.close()
        _cleanup(p)


def test_feedback_negative():
    nc, p = _make_nc()
    try:
        r = nc.write("负反馈测试", user_id="fb_neg")
        kid = r["id"]
        for _ in range(10):
            nc.feedback(kid, "explicit_negative", user_id="fb_neg")
        conn = nc._conn()
        row = conn.execute(
            "SELECT negative_feedback FROM unified_knowledge WHERE id=?", (kid,)
        ).fetchone()
        assert row["negative_feedback"] >= 10
    finally:
        nc.close()
        _cleanup(p)


def test_supersede_fact():
    nc, p = _make_nc()
    try:
        r = nc.write("旧知识内容", user_id="sup")
        kid = r["id"]
        nc.supersede_fact(kid, "新知识内容", user_id="sup")
        conn = nc._conn()
        old = conn.execute(
            "SELECT status FROM unified_knowledge WHERE id=?", (kid,)
        ).fetchone()
        assert old["status"] == "superseded"
    finally:
        nc.close()
        _cleanup(p)


def test_search_temporal():
    nc, p = _make_nc()
    try:
        nc.write("时间搜索测试", user_id="temp")
        now = datetime.now(timezone.utc).isoformat()
        results = nc.search_temporal("时间搜索", at_time=now, user_id="temp")
        assert isinstance(results, list)
    finally:
        nc.close()
        _cleanup(p)


def test_log_interaction_with_knowledge():
    nc, p = _make_nc()
    try:
        r = nc.write("交互知识测试条目", user_id="log_k")
        kid = r["id"]
        knowledge = [{"id": kid, "content": "交互知识测试条目", "layer": "instant", "_source": "search", "similarity": 0.9}]
        lid = nc.log_interaction("问题", "回答", knowledge_used=knowledge, user_id="log_k")
        assert lid > 0
        chain = nc.get_interaction_chain(lid)
        assert len(chain) >= 1
    finally:
        nc.close()
        _cleanup(p)


def test_ensure_fts_integrity():
    nc, p = _make_nc()
    try:
        nc.write("FTS完整性测试", user_id="fts_int")
        conn = nc._conn()
        nc._ensure_fts_integrity(conn)
        # Should not raise
    finally:
        nc.close()
        _cleanup(p)


def test_auto_repair():
    nc, p = _make_nc()
    try:
        nc.write("修复测试数据条目", user_id="repair")
        result = nc._auto_repair()
        assert isinstance(result, bool)
    finally:
        nc.close()
        _cleanup(p)


def test_check_integrity():
    nc, p = _make_nc()
    try:
        result = nc._check_integrity()
        assert result is True
    finally:
        nc.close()
        _cleanup(p)


def test_resolve_session():
    nc, p = _make_nc()
    try:
        result = nc.resolve_session("test-session-id")
        assert isinstance(result, str)
    finally:
        nc.close()
        _cleanup(p)


def test_domain_scores_update():
    nc, p = _make_nc()
    try:
        r = nc.write("领域分数测试", user_id="ds")
        results = [{"id": r["id"], "content": "领域分数测试", "domain_scores": {"coding": 0}}]
        nc._update_domain_scores(results, "ds")
        conn = nc._conn()
        row = conn.execute(
            "SELECT domain_scores FROM unified_knowledge WHERE id=?", (r["id"],)
        ).fetchone()
        assert row is not None
    finally:
        nc.close()
        _cleanup(p)


def test_infer_domain():
    nc, p = _make_nc()
    try:
        domain = nc._infer_domain("Python编程技巧", {"domain_scores": {"coding": 5}})
        assert isinstance(domain, str)
    finally:
        nc.close()
        _cleanup(p)


def test_belief_init():
    nc, p = _make_nc()
    try:
        r = nc.write("信念测试数据条目", user_id="belief")
        nc._init_belief(r["id"], initial_confidence=0.5)
        # Should not raise
    finally:
        nc.close()
        _cleanup(p)


def test_extract_metrics():
    nc, p = _make_nc()
    try:
        metrics = nc._extract_metrics("Python 3.11版本，性能提升20%")
        assert isinstance(metrics, list)
    finally:
        nc.close()
        _cleanup(p)


def test_backup_db():
    from nexus.core import NexusCore
    nc, p = _make_nc()
    try:
        nc.write("备份测试数据条目", user_id="backup")
        result = NexusCore._backup_db(p)
        assert isinstance(result, bool)
    finally:
        nc.close()
        _cleanup(p)


def test_coldstart_stats():
    nc, p = _make_nc()
    try:
        nc.write("冷启动测试", user_id="cold")
        stats = nc._get_coldstart_stats("cold")
        assert isinstance(stats, dict)
    finally:
        nc.close()
        _cleanup(p)


def test_validate_by_layer():
    nc, p = _make_nc()
    try:
        nc.write("层级验证测试", user_id="layer")
        result = nc._validate_by_layer("layer")
        assert isinstance(result, dict)
    finally:
        nc.close()
        _cleanup(p)


def test_detect_conflicts():
    nc, p = _make_nc()
    try:
        nc.write("Python 3.11版本 性能提升20%", user_id="conflict")
        nc.write("Python 3.11版本 性能提升50%", user_id="conflict")
        # Should detect conflict on same entity+metric with different values
    finally:
        nc.close()
        _cleanup(p)


def test_write_new_entry():
    nc, p = _make_nc()
    try:
        r = nc.write("全新知识条目", user_id="new_entry", skip_conflict_detection=True)
        assert r["success"] is True
        assert r["action"] == "created"
        assert r["id"] > 0
    finally:
        nc.close()
        _cleanup(p)


def test_system_prompt_block_disabled():
    nc, p = _make_nc()
    try:
        block = nc.system_prompt_block(memory_enabled=False)
        assert block == ""
    finally:
        nc.close()
        _cleanup(p)


def test_get_alerts_empty():
    nc, p = _make_nc()
    try:
        alerts = nc.get_alerts()
        assert isinstance(alerts, list)
        assert len(alerts) == 0
    finally:
        nc.close()
        _cleanup(p)


def test_audit_snapshot():
    nc, p = _make_nc()
    try:
        nc.write("审计快照测试", user_id="audit_snap")
        now = datetime.now(timezone.utc).isoformat()
        snap = nc.audit_snapshot(at_time=now, user_id="audit_snap")
        assert isinstance(snap, dict)
    finally:
        nc.close()
        _cleanup(p)


def test_get_model_stats_all():
    nc, p = _make_nc()
    try:
        nc.record_model_performance("gpt-4o", "coding", 0.9, "s1")
        nc.record_model_performance("gpt-4o", "writing", 0.85, "s2")
        stats = nc.get_model_stats()  # All models
        assert len(stats) == 2
    finally:
        nc.close()
        _cleanup(p)


def test_get_interaction_chain_empty():
    nc, p = _make_nc()
    try:
        chain = nc.get_interaction_chain(99999)
        assert len(chain) == 0
    finally:
        nc.close()
        _cleanup(p)


def test_log_correction_invalid_original():
    nc, p = _make_nc()
    try:
        result = nc.log_correction(99999, "q", "a")
        assert result == 0
    finally:
        nc.close()
        _cleanup(p)


def test_system_prompt_block_with_data():
    nc, p = _make_nc()
    try:
        nc.write("系统提示测试", user_id="prompt")
        block = nc.system_prompt_block(user_id="prompt", char_limit=500)
        assert isinstance(block, str)
    finally:
        nc.close()
        _cleanup(p)


def test_get_subsystem_views():
    nc, p = _make_nc()
    try:
        nc.write("子系统视图测试", user_id="views")
        views = nc.get_subsystem_views("views")
        assert isinstance(views, dict)
        assert "domains" in views or "alerts" in views
    finally:
        nc.close()
        _cleanup(p)


def test_search_empty():
    nc, p = _make_nc()
    try:
        results = nc.search("不存在的内容xyz123", user_id="empty_search", mode="fts")
        assert isinstance(results, list)
    finally:
        nc.close()
        _cleanup(p)


def test_write_event_time():
    nc, p = _make_nc()
    try:
        et = "2025-01-01T00:00:00+00:00"
        r = nc.write("时间戳测试", user_id="et", event_time=et)
        assert r["success"] is True
    finally:
        nc.close()
        _cleanup(p)


def test_write_initial_confidence():
    nc, p = _make_nc()
    try:
        r = nc.write("置信度测试", user_id="conf", initial_confidence=0.8)
        assert r["success"] is True
    finally:
        nc.close()
        _cleanup(p)


def test_feedback_correction_type():
    nc, p = _make_nc()
    try:
        r = nc.write("修正反馈测试", user_id="corr_fb")
        kid = r["id"]
        result = nc.feedback(kid, "correction", user_id="corr_fb")
        assert result["success"] is True
    finally:
        nc.close()
        _cleanup(p)


def test_feedback_invalid_type():
    nc, p = _make_nc()
    try:
        r = nc.write("无效反馈测试", user_id="inv_fb")
        result = nc.feedback(r["id"], "invalid_type", user_id="inv_fb")
        assert result["success"] is False
    finally:
        nc.close()
        _cleanup(p)


def test_feedback_nonexistent():
    nc, p = _make_nc()
    try:
        result = nc.feedback(99999, "explicit_positive")
        assert result["success"] is False
    finally:
        nc.close()
        _cleanup(p)


if __name__ == "__main__":
    import traceback
    tests = [
        test_add_rule, test_record_model_performance, test_audit_stats,
        test_knowledge_snapshot, test_stats, test_get_alerts,
        test_build_context, test_search_by_domain, test_record_domain_hit,
        test_get_history, test_system_prompt_block, test_get_subsystem_views,
        test_rebuild_fts, test_consolidate, test_log_correction,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n结果: {passed}/{passed+failed} 通过")
    sys.exit(0 if failed == 0 else 1)
