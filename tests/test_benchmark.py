"""test_nexus_benchmark.py — Nexus 记忆系统基准测试套件

测试内容:
  1. 基本写入/检索 (10轮)
  2. 写入合并 (写时进化) (5轮)
  3. Belief 晋升 (encounter → belief → fact) (5轮)
  4. 时间旅行查询 (知识快照) (3轮)
  5. 宪法治理 (身份域保护) (3轮)
  6. 多轮对话综合召回率 (~30轮)

用法:
  python3 tests/test_nexus_benchmark.py [--verbose]

每次运行使用独立的测试 DB，不影响生产数据。
"""

import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nexus.core import NexusCore
from nexus.belief import BeliefEngine
from nexus.evolve import evolve_on_write, find_merge_target
from nexus.constitution import Constitution


# ── 测试数据 ───────────────────────────────────────────────

TEST_USER = "benchmark_user"
TEST_IDENTITY = [
    "用户偏好Python，动态类型语言，用于数据分析",
    "用户习惯用VS Code写代码，Docker跑环境",
    "用户每天开盘前看一遍持仓，收盘后复盘",
    "用户对技术精度极度敏感，不接受模糊表述",
    "用户风格是不断推进直到全部完成，不喜中断",
]

TEST_WORKFLOW = [
    "先用MCP工具获取实时行情，再用pipeline做深度分析",
    "盘后运行pipeline.py trading做全链路分析",
    "每日9:25前加载preflight预加载模块",
    "每完成一个阶段要跑通全链路验证",
    "地基问题就改地基，不绕路",
]

TEST_CORRECTIONS = [
    ("用户喜欢Java", "用户偏好Python，不是Java"),
    ("用户用PyCharm写代码", "用户用VS Code写代码，不是PyCharm"),
]


class BenchmarkSuite:
    """Nexus 基准测试"""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.results = {"tests": [], "total_score": 0.0}
        self._setup_db()

    def _setup_db(self):
        """创建独立测试 DB"""
        self.db_dir = tempfile.mkdtemp(prefix="nexus_benchmark_")
        self.db_path = os.path.join(self.db_dir, "nexus_test.db")
        # 复制 schema.sql
        schema_src = Path.home() / ".hermes" / "hermes-agent" / "plugins" / "memory" / "nexus" / "schema.sql"
        if schema_src.exists():
            import shutil
            os.makedirs(os.path.join(self.db_dir, "plugins", "memory", "nexus"), exist_ok=True)
            shutil.copy(schema_src, os.path.join(self.db_dir, "plugins", "memory", "nexus", "schema.sql"))
        self.nc = NexusCore(self.db_path)
        self.conn = self.nc._conn()

    def _log(self, msg: str):
        if self.verbose:
            print(f"  {msg}")
        elif not self.verbose:
            pass

    def run_all(self) -> dict:
        """运行所有测试，返回结果"""
        self._test_basic_write_retrieve()
        self._test_write_merge()
        self._test_belief_evolution()
        self._test_temporal_snapshot()
        self._test_constitution()
        self._test_comprehensive_recall()
        self._print_summary()
        self.nc.close()

        # Cleanup temp DB
        import shutil
        try:
            shutil.rmtree(self.db_dir)
        except Exception:
            pass

        return self.results

    # ── Test 1: 基本写入/检索 ──────────────────────────────

    def _test_basic_write_retrieve(self):
        """写入 5 条身份知识，检索验证"""
        t0 = time.time()
        ids = []
        for content in TEST_IDENTITY:
            r = self.nc.write(content, user_id=TEST_USER,
                              source_session_id="benchmark_1")
            ids.append(r.get("id"))
            self._log(f"  write: {content[:40]} -> {r['action']}")

        # 检索
        found = 0
        for content in TEST_IDENTITY:
            kw = content[:10]  # 用前10个字检索
            results = self.nc.search(kw, user_id=TEST_USER, limit=5)
            if any(content[:20] in (r.get("content", "") or "") for r in results):
                found += 1

        score = found / len(TEST_IDENTITY)
        elapsed = time.time() - t0
        self.results["tests"].append({
            "name": "basic_write_retrieve",
            "score": round(score, 3),
            "found": found,
            "total": len(TEST_IDENTITY),
            "elapsed": round(elapsed, 2),
        })
        self._log(f"  Score: {found}/{len(TEST_IDENTITY)} = {score:.1%} in {elapsed:.1f}s")

    # ── Test 2: 写时合并 ──────────────────────────────────

    def _test_write_merge(self):
        t0 = time.time()
        # 写入原始版
        base = "用户在交易时段专注看盘，不处理其他事务"
        r1 = self.nc.write(base, user_id=TEST_USER, source_session_id="benchmark_2")
        self._log(f"  Write original: {r1['action']} id={r1.get('id')}")

        # 写入扩展版（被包含关系）
        extended = "用户在交易时段专注看盘，不处理其他事务 — 包括不看消息不回邮件"
        r2 = self.nc.write(extended, user_id=TEST_USER, source_session_id="benchmark_2")
        self._log(f"  Write extended: {r2['action']} id={r2.get('id')}")

        merged = r2["action"] in ("fuzzy_dup", "updated_existing", "complement")

        # 写入互补版
        comp = "另外用户在非交易时段做复盘和研究，不参与社交"
        r3 = self.nc.write(comp, user_id=TEST_USER, source_session_id="benchmark_2")
        self._log(f"  Write complement: {r3['action']} id={r3.get('id')}")
        complemented = r3["action"] in ("complement", "fuzzy_dup", "updated_existing")

        score = (1.0 if merged else 0.0) + (1.0 if complemented else 0.0)
        score /= 2
        elapsed = time.time() - t0
        self.results["tests"].append({
            "name": "write_merge",
            "score": round(score, 3),
            "merged": merged,
            "complemented": complemented,
            "elapsed": round(elapsed, 2),
        })
        self._log(f"  Score: merged={merged}, complemented={complemented} = {score:.1%}")

    # ── Test 3: Belief 晋升 ───────────────────────────────

    def _test_belief_evolution(self):
        t0 = time.time()
        be = BeliefEngine(self.conn)

        # 写入一条新知识
        r = self.nc.write("用户偏好短线交易，持仓不超过3天",
                          user_id=TEST_USER, source_session_id="benchmark_3")
        kid = r.get("id")
        self._log(f"  Created id={kid}")

        # 检查初始 belief
        b = be.get_belief(kid)
        initial_type = b["belief_type"] if b else "none"
        initial_conf = b["confidence"] if b else 0.0
        self._log(f"  Initial: {initial_type} (conf={initial_conf:.2f})")

        # 重复 encounter 5 次（模拟多次遇到同一知识）
        for i in range(5):
            be.on_encounter(kid)

        b = be.get_belief(kid)
        after_type = b["belief_type"] if b else "none"
        after_conf = b["confidence"] if b else 0.0
        self._log(f"  After 5 encounters: {after_type} (conf={after_conf:.2f})")

        # 施加纠正
        be.on_correction(kid)
        b = be.get_belief(kid)
        corrected_conf = b["confidence"] if b else 0.0
        self._log(f"  After correction: conf={corrected_conf:.2f}")

        # 检查层同步
        row = self.conn.execute(
            "SELECT layer FROM unified_knowledge WHERE id = ?", (kid,)
        ).fetchone()
        layer = row["layer"] if row else "unknown"

        # 评分
        promoted = after_type in ("belief", "fact")  # 从 observation 晋升
        demoted_after_correct = corrected_conf < after_conf  # 纠正确实降级

        score = (1.0 if promoted else 0.0) + (1.0 if demoted_after_correct else 0.0)
        score /= 2
        elapsed = time.time() - t0
        self.results["tests"].append({
            "name": "belief_evolution",
            "score": round(score, 3),
            "initial": {"type": initial_type, "conf": round(initial_conf, 2)},
            "after_encounters": {"type": after_type, "conf": round(after_conf, 2)},
            "after_correction": {"conf": round(corrected_conf, 2)},
            "layer": layer,
            "elapsed": round(elapsed, 2),
        })
        self._log(f"  Score: promoted={promoted}, demoted_on_correction={demoted_after_correct} = {score:.1%}")

    # ── Test 4: 时间旅行 ──────────────────────────────────

    def _test_temporal_snapshot(self):
        t0 = time.time()
        # 写入上午的知识，用过去的时间戳 (UTC 时间，确保在测试执行之前)
        r = self.nc.write("测试时间旅行: 这个是上午记的",
                          user_id=TEST_USER, source_session_id="benchmark_4",
                          event_time="2026-05-23T09:00:00")
        kid_am = r.get("id")

        # 稍后纠正为下午版本
        self.nc.supersede_fact(kid_am, "测试时间旅行: 这个是下午改的",
                                user_id=TEST_USER, reason="correction",
                                source_session_id="benchmark_4")
        # 获取新 id
        row = self.conn.execute(
            "SELECT id FROM unified_knowledge WHERE replaces = ?",
            (kid_am,)
        ).fetchone()
        kid_pm = row["id"] if row else None

        # 时间旅行回昨天中午 — AM 版本应该存在，PM 版本不应该
        snap_am = self.nc.knowledge_snapshot("2026-05-23T12:00:00",
                                              user_id=TEST_USER, limit=20)
        has_am = any(r.get("id") == kid_am for r in snap_am)
        has_pm = any(r.get("id") == kid_pm for r in snap_am) if kid_pm else False
        correct_snapshot = has_am and not has_pm

        # 当前状态 = PM 版本存在
        import datetime as dt
        now_str = dt.datetime.now(dt.timezone.utc).isoformat()
        audit = self.nc.audit_snapshot(now_str, user_id=TEST_USER)
        current_has_pm = any(
            k["id"] == kid_pm for k in audit.get("active_knowledge", [])
        ) if kid_pm else False

        score = (1.0 if correct_snapshot else 0.0) + (1.0 if current_has_pm else 0.0)
        score /= 2
        elapsed = time.time() - t0
        self.results["tests"].append({
            "name": "temporal_snapshot",
            "score": round(score, 3),
            "am_visible_at_10am": has_am,
            "pm_not_visible_at_10am": not has_pm,
            "pm_visible_at_6pm": current_has_pm,
            "elapsed": round(elapsed, 2),
        })
        self._log(f"  Score: am_only_at_10am={correct_snapshot}, pm_at_6pm={current_has_pm} = {score:.1%}")

    # ── Test 5: 宪法治理 ──────────────────────────────────

    def _test_constitution(self):
        t0 = time.time()
        c = Constitution(self.conn)

        # identity 域: auto_update 应该被禁止
        blocked = not c.can_auto_update("identity")
        # strategy 域: 应该允许
        allowed = c.can_auto_update("strategy")

        # identity 写入需要确认
        req_confirm = c.require_confirmation("identity", "write")
        # strategy 不需要
        no_confirm = not c.require_confirmation("strategy", "write")

        score = (1.0 if blocked else 0.0) + (1.0 if allowed else 0.0) + \
                (1.0 if req_confirm else 0.0) + (1.0 if no_confirm else 0.0)
        score /= 4
        elapsed = time.time() - t0
        self.results["tests"].append({
            "name": "constitution",
            "score": round(score, 3),
            "identity_auto_update_blocked": blocked,
            "strategy_auto_update_allowed": allowed,
            "identity_write_requires_confirmation": req_confirm,
            "strategy_write_no_confirmation": no_confirm,
            "elapsed": round(elapsed, 2),
        })
        self._log(f"  Score: {score:.1%}")

    # ── Test 6: 综合召回率 ────────────────────────────────

    def _test_comprehensive_recall(self):
        """写入 15 条知识 + 3 次纠正，然后全员检索"""
        t0 = time.time()

        all_knowledge = TEST_IDENTITY + TEST_WORKFLOW
        written = {}
        for content in all_knowledge:
            r = self.nc.write(content, user_id=TEST_USER,
                              source_session_id="benchmark_6")
            written[content[:20]] = r.get("id")

        # 纠正
        for wrong, correct in TEST_CORRECTIONS:
            # Find the wrong entry
            row = self.conn.execute(
                "SELECT id FROM unified_knowledge WHERE content LIKE ? AND user_id = ?",
                (f"%{wrong[:20]}%", TEST_USER)
            ).fetchone()
            if row:
                self.nc.supersede_fact(row["id"], correct, user_id=TEST_USER,
                                        reason="user_correction",
                                        source_session_id="benchmark_6")

        # 检索所有
        found = 0
        for content in all_knowledge:
            kw = content[:10]
            results = self.nc.search(kw, user_id=TEST_USER, mode="hybrid", limit=5)
            matched = any(
                content[:20] in (r.get("content", "") or "")
                for r in results
            )
            if matched:
                found += 1

        score = found / len(all_knowledge)
        elapsed = time.time() - t0
        self.results["tests"].append({
            "name": "comprehensive_recall",
            "score": round(score, 3),
            "found": found,
            "total": len(all_knowledge),
            "elapsed": round(elapsed, 2),
        })
        self._log(f"  Score: {found}/{len(all_knowledge)} = {score:.1%} in {elapsed:.1f}s")

    # ── 汇总 ──────────────────────────────────────────────

    def _print_summary(self):
        print("\n" + "=" * 60)
        print("  NEXUS BENCHMARK RESULTS")
        print("=" * 60)
        total = 0.0
        count = 0
        for t in self.results["tests"]:
            total += t["score"]
            count += 1
            bar = "█" * int(t["score"] * 20) + "░" * (20 - int(t["score"] * 20))
            name = t["name"].ljust(28)
            print(f"  {name} {bar} {t['score']:.0%}  ({t.get('elapsed', 0):.1f}s)")

        avg = total / count if count > 0 else 0
        print("=" * 60)
        bar = "█" * int(avg * 20) + "░" * (20 - int(avg * 20))
        print(f"  OVERALL{' ' * 22} {bar} {avg:.0%}")
        print("=" * 60)

        self.results["overall_score"] = round(avg, 3)
        self.results["total_time"] = round(sum(t.get("elapsed", 0) for t in self.results["tests"]), 2)


if __name__ == "__main__":
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    suite = BenchmarkSuite(verbose=verbose)
    suite.run_all()
