"""nexus_constitution.py — 宪法治理层

Animesis 四层治理的实用简化版，聚焦 Nexus 实际场景：

1. 宪法层 (Constitution)  — 不可变规则：
   - identity 域知识只能读取不能由 agent 自动修改
   - portfolio 数据写入前校验格式
   - 某些域的知识只能事实化不能降级

2. 完整性层 (Integrity) — 换模型保护：
   - 模型切换时记忆一致性检查
   - domain_accuracy 漂移检测
   - 模型版本标注

3. 所有权层 (Ownership) — 记忆归属：
   - 谁的知识谁控制
   - 数据生命周期管理
   - 合规删除（Right to be forgotten）

用法:
  from agent.nexus_constitution import Constitution
  c = Constitution(conn)
  c.check_rule('write', domain='identity')  # 检查是否允许写入
  c.on_model_switch('deepseek-v4', 'gemini-3')  # 模型切换保护
  c.forget_user('default')  # 合规删除
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 宪法规则定义 ───────────────────────────────────────────

CONSTITUTION_RULES = {
    # domain → {actions: [allowed/denied ops]}
    "identity": {
        "description": "用户身份/偏好 — 只能由用户显式修改，agent 不能自动覆盖",
        "write": {"allowed": True, "require_confirmation": True},
        "auto_update": {"allowed": False},  # 进化引擎不能自动合并身份信息
        "delete": {"allowed": True, "require_confirmation": True},
        "demote": {"allowed": False},  # 不能自动降级（身份信息不随时间衰退）
    },
    "strategy": {
        "description": "交易策略 — 只能事实化不能降级",
        "write": {"allowed": True},
        "auto_update": {"allowed": True},
        "delete": {"allowed": False},  # 交易策略始终保留审计痕迹
        "demote": {"allowed": False},  # 策略不衰退
    },
    "portfolio": {
        "description": "持仓数据 — 格式校验后写入",
        "write": {"allowed": True, "validate_schema": True},
        "auto_update": {"allowed": False},
        "delete": {"allowed": True},
        "demote": {"allowed": True},
    },
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS constitution_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rule            TEXT NOT NULL,
    domain          TEXT,
    action          TEXT,
    allowed         INTEGER NOT NULL DEFAULT 1,
    reason          TEXT,
    knowledge_id    INTEGER,
    user_id         TEXT DEFAULT 'default',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS model_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name      TEXT NOT NULL,
    provider        TEXT,
    switched_from   TEXT,
    switched_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    domain_accuracy TEXT,  -- JSON snapshot of domain accuracy at switch time
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_cl_domain ON constitution_log(domain);
CREATE INDEX IF NOT EXISTS idx_cl_created ON constitution_log(created_at);
CREATE INDEX IF NOT EXISTS idx_mv_switched ON model_versions(switched_at);
"""


class Constitution:

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    # ── 规则检查 ───────────────────────────────────────────

    def check_rule(self, action: str, domain: str = "",
                   knowledge_id: Optional[int] = None,
                   user_id: str = "default",
                   auto_confirm: bool = False) -> Dict[str, Any]:
        """检查某操作是否被宪法允许。

        Returns:
            {"allowed": True/False, "reason": "...", "require_confirmation": True/False}
        """
        rule = CONSTITUTION_RULES.get(domain, {})
        action_rule = rule.get(action, {})

        allowed = action_rule.get("allowed", True)
        require_confirm = action_rule.get("require_confirmation", False)
        reason = action_rule.get("description", "")

        if not allowed:
            reason = f"宪法禁止: {domain} 域不允许 {action} 操作"
            logger.warning("Constitution blocked: %s.%s", domain, action)

        if require_confirm and not auto_confirm:
            reason = f"需要确认: {domain} 域的 {action} 操作需要用户确认"
            allowed = False  # Require confirmation = block until confirmed

        # 记录审计日志
        self._log(rule=action_rule.get("description", action),
                  domain=domain, action=action,
                  allowed=allowed, reason=reason,
                  knowledge_id=knowledge_id, user_id=user_id)

        return {
            "allowed": allowed,
            "require_confirmation": require_confirm,
            "reason": reason,
        }

    def require_confirmation(self, domain: str, action: str) -> bool:
        """快速检查某操作是否需要用户确认。"""
        rule = CONSTITUTION_RULES.get(domain, {})
        return rule.get(action, {}).get("require_confirmation", False)

    def can_auto_update(self, domain: str) -> bool:
        """进化引擎是否可以自动修改此域的知识。"""
        rule = CONSTITUTION_RULES.get(domain, {})
        return rule.get("auto_update", {}).get("allowed", True)

    def can_demote(self, domain: str) -> bool:
        """此域的知识是否可以因时间衰退而降级。"""
        rule = CONSTITUTION_RULES.get(domain, {})
        return rule.get("demote", {}).get("allowed", True)

    # ── 换模型保护 ─────────────────────────────────────────

    def on_model_switch(self, new_model: str, provider: str = "",
                         old_model: str = "",
                         user_id: str = "default") -> Dict[str, Any]:
        """模型切换时调用：快照 domain_accuracy + 标注所有知识。

        这是宪法层的核心保护机制。
        当底层模型改变时，已写入的知识可能被新模型"重新解读"。
        通过记录切换时间和各域准确率，可以追踪解读漂移。
        """
        now = datetime.now(timezone.utc).isoformat()

        # 1. 快照当前 domain_accuracy（从 miner）
        domain_accuracy = {}
        try:
            from agent.nexus_miner import NexusMiner
            miner = NexusMiner()
            report = miner.mine_all()
            if "self_model" in report:
                domain_accuracy = report["self_model"].get("domain_accuracy", {})
            miner.close()
        except Exception:
            pass

        # 2. 记录模型版本
        accuracy_json = json.dumps(domain_accuracy, ensure_ascii=False)
        self.conn.execute(
            """INSERT INTO model_versions
               (model_name, provider, switched_from, domain_accuracy, notes)
               VALUES (?, ?, ?, ?, ?)""",
            (new_model, provider, old_model,
             accuracy_json,
             f"Switched from {old_model} at {now}")
        )
        version_id = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.conn.commit()

        # 3. 标记所有 active 知识为"在旧模型下写入"
        self.conn.execute(
            """UPDATE unified_knowledge
               SET active_summary = CASE
                 WHEN active_summary IS NULL OR active_summary = ''
                   THEN 'model:' || ?
                 ELSE active_summary || ' | model:' || ?
               END
               WHERE status = 'active' AND user_id = ?""",
            (old_model, old_model, user_id)
        )
        self.conn.commit()

        logger.info("Constitution: model switch %s -> %s logged (version=%d)",
                     old_model, new_model, version_id)

        return {
            "success": True,
            "version_id": version_id,
            "new_model": new_model,
            "old_model": old_model,
            "domain_accuracy_snapshot": domain_accuracy,
        }

    def detect_drift(self, user_id: str = "default") -> Dict[str, Any]:
        """检测新模型下的 domain_accuracy 是否与切换时发生漂移。

        需要当前模型至少运行了 50 次交互才能比较。
        """
        # 获取最近一次模型切换
        last_switch = self.conn.execute(
            "SELECT * FROM model_versions ORDER BY switched_at DESC LIMIT 1"
        ).fetchone()
        if not last_switch:
            return {"drift_detected": False,
                    "reason": "no_model_switch_recorded"}

        old_accuracy = json.loads(last_switch["domain_accuracy"]) if last_switch["domain_accuracy"] else {}

        # 获取当前准确率
        current_accuracy = {}
        try:
            from agent.nexus_miner import NexusMiner
            miner = NexusMiner()
            report = miner.mine_all()
            if "self_model" in report:
                current_accuracy = report["self_model"].get("domain_accuracy", {})
            miner.close()
        except Exception:
            pass

        # 比较各域
        drifts = {}
        total_drift = 0.0
        domains = set(list(old_accuracy.keys()) + list(current_accuracy.keys()))
        for d in domains:
            old_val = old_accuracy.get(d, {}).get("accuracy", 0.0) if isinstance(old_accuracy.get(d), dict) else 0.0
            cur_val = current_accuracy.get(d, {}).get("accuracy", 0.0) if isinstance(current_accuracy.get(d), dict) else 0.0
            diff = abs(cur_val - old_val)
            if diff > 0.1:
                drifts[d] = {"old": round(old_val, 3), "current": round(cur_val, 3), "diff": round(diff, 3)}
                total_drift = max(total_drift, diff)

        result = {
            "drift_detected": len(drifts) > 0,
            "drifted_domains": drifts,
            "max_drift": round(total_drift, 3),
        }
        if drifts:
            logger.warning("Constitution: domain accuracy drift detected: %s", drifts)

        return result

    # ── 合规删除 ───────────────────────────────────────────

    def forget_user(self, user_id: str = "default") -> Dict[str, Any]:
        """合规删除用户所有数据（Right to be forgotten）。

        - 不硬删除，标记为 'forgotten' status
        - 保留审计痕迹但不保留内容
        """
        now = datetime.now(timezone.utc).isoformat()

        # 1. 匿名化 unified_knowledge
        anonymized = self.conn.execute(
            """UPDATE unified_knowledge
               SET content = '[REDACTED per user request]',
                   source_snippet = NULL,
                   active_summary = '[REDACTED]',
                   user_id = 'forgotten_' || ?,
                   updated_at = ?
               WHERE user_id = ? AND status = 'active'""",
            (user_id, now, user_id)
        ).rowcount

        # 2. 匿名化 interaction_log
        log_anonymized = self.conn.execute(
            """UPDATE interaction_log
               SET user_query = '[REDACTED]',
                   model_response = '[REDACTED]',
                   knowledge_used = '[]',
                   user_id = 'forgotten_' || ?
               WHERE user_id = ?""",
            (user_id, user_id)
        ).rowcount

        # 3. 删除 user_fingerprints
        self.conn.execute(
            "DELETE FROM user_fingerprints WHERE user_id = ?",
            (user_id,)
        )

        # 4. 删除 belief 记录
        self.conn.execute(
            """DELETE FROM knowledge_beliefs
               WHERE knowledge_id IN (
                 SELECT id FROM unified_knowledge WHERE user_id LIKE 'forgotten_%'
               )"""
        )

        self.conn.commit()

        logger.info("Constitution: forgot user %s (%d knowledge, %d interactions)",
                     user_id, anonymized, log_anonymized)

        return {
            "success": True,
            "anonymized_knowledge": anonymized,
            "anonymized_interactions": log_anonymized,
        }

    # ── 审计日志 ───────────────────────────────────────────

    def _log(self, rule: str, domain: str, action: str,
             allowed: bool, reason: str,
             knowledge_id: Optional[int] = None,
             user_id: str = "default"):
        try:
            self.conn.execute(
                """INSERT INTO constitution_log
                   (rule, domain, action, allowed, reason, knowledge_id, user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (rule[:200], domain, action, 1 if allowed else 0,
                 reason[:500], knowledge_id, user_id)
            )
            self.conn.commit()
        except Exception:
            pass

    def get_log(self, limit: int = 20) -> List[Dict[str, Any]]:
        """最近宪法操作日志。"""
        rows = self.conn.execute(
            "SELECT * FROM constitution_log ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_model_history(self) -> List[Dict[str, Any]]:
        """模型切换历史。"""
        rows = self.conn.execute(
            "SELECT * FROM model_versions ORDER BY switched_at DESC LIMIT 10"
        ).fetchall()
        return [dict(r) for r in rows]
