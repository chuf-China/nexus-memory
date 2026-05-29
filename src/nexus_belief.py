"""nexus_belief.py — Belief 网络（Observation → Belief → Fact 三级认知架构）

Nexus 默认将所有知识视为"事实"。Belief Network 引入三级认知状态，
模拟人类记忆系统：观察 → 信念 → 事实。

三级状态:
  Observation:  原始输入，低置信度 (0.3-0.5)。一次看见不一定是真的。
  Belief:       多次印证，中等置信度 (0.5-0.85)。有价值但可被推翻。
  Fact:         长期验证，高置信度 (0.85+)。通常只被纠正替换，不降级。

置信度更新规则:
  - re-encounter (再次遇到):  +0.15
  - positive_feedback:         +0.10
  - correction (用户纠正):     max(0, conf - 0.30)
  - time decay (48h 不访问):   -0.05
  - contradiction (检测到冲突): -0.15

晋升:
  confidence >= 0.70 → belief (如果当前是 observation)
  confidence >= 0.90 → fact (如果当前是 belief)
  
降级:
  confidence < 0.70 → observation (如果当前是 belief)
  confidence < 0.30 → archived (标记为低可信)

用法:
  from agent.nexus_belief import BeliefEngine
  be = BeliefEngine(conn)
  be.on_encounter(knowledge_id)     # 再次遇到
  be.on_feedback(knowledge_id, +1)  # 正反馈
  be.on_correction(knowledge_id)    # 纠正
  be.update_all_beliefs()           # 批量更新（含时间衰退）
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 阈值常量 ────────────────────────────────────────────────

CONFIDENCE_OBSERVATION_MAX = 0.50
CONFIDENCE_BELIEF_MIN = 0.70
CONFIDENCE_BELIEF_MAX = 0.89
CONFIDENCE_FACT_MIN = 0.90
CONFIDENCE_ARCHIVE_THRESHOLD = 0.30

DECAY_INTERVAL_HOURS = 48       # 超过此时间未访问 → 衰退
DECAY_PER_INTERVAL = 0.05       # 每个间隔衰退量
REINFORCE_INCREMENT = 0.15      # re-encounter 增幅
FEEDBACK_INCREMENT = 0.10       # 显式正反馈增幅
CORRECTION_PENALTY = 0.30       # 纠正惩罚
CONTRADICTION_PENALTY = 0.15    # 冲突惩罚

# ── 核心信念（自律反馈） ───────────────────────────────────
# 系统级强约束信念，不同于 knowledge_beliefs 的动态置信度。
# 这些信念不可删除，仅强度可调。
CORE_BELIEFS = [
    {
        "id": "self_discipline_check",
        "text": "每次完成任务必须经过 quality 和 compliance 检查才可提交",
        "domain": "自律",
        "strength": 1.0,
        "enforcement": "如果检测到连续跳过检查，belief 强度下降时触发告警",
    },
    {
        "id": "no_premature_end",
        "text": "任务未满足完成条件时不得提前结束，必须执行所有步骤",
        "domain": "自律",
        "strength": 1.0,
        "enforcement": "lazy_detector 检测到 premature_end 时强化该 belief",
    },
]

# ── 表定义 ──────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS knowledge_beliefs (
    knowledge_id        INTEGER PRIMARY KEY REFERENCES unified_knowledge(id) ON DELETE CASCADE,
    belief_type         TEXT NOT NULL DEFAULT 'observation'
                        CHECK(belief_type IN ('observation', 'belief', 'fact')),
    confidence          REAL NOT NULL DEFAULT 0.40,
    evidence_positive   INTEGER DEFAULT 0,
    evidence_negative   INTEGER DEFAULT 0,
    encounter_count     INTEGER DEFAULT 1,
    last_reinforced_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_corrected_at   TIMESTAMP,
    promoted_at         TIMESTAMP,
    demoted_at          TIMESTAMP,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_kb_type ON knowledge_beliefs(belief_type);
CREATE INDEX IF NOT EXISTS idx_kb_confidence ON knowledge_beliefs(confidence);
CREATE INDEX IF NOT EXISTS idx_kb_last_reinforced ON knowledge_beliefs(last_reinforced_at);
"""


class BeliefEngine:
    """Belief 引擎 — 管理知识的认知状态。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._init_tables()

    def _init_tables(self):
        """创建 belief 表（幂等）。"""
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    # ── 初始化 belief 记录 ─────────────────────────────────

    def init_belief(self, knowledge_id: int,
                     initial_confidence: float = 0.40) -> Dict[str, Any]:
        """为新知识创建 belief 记录。默认为 observation 级别。"""
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO knowledge_beliefs
                   (knowledge_id, belief_type, confidence, encounter_count,
                    last_reinforced_at, created_at, updated_at)
                   VALUES (?, 'observation', ?, 1, ?, ?, ?)""",
                (knowledge_id, initial_confidence, now, now, now)
            )
            self.conn.commit()
            return {"belief_type": "observation", "confidence": initial_confidence}
        except Exception as e:
            logger.debug("Belief init failed for %d: %s", knowledge_id, e)
            return {"belief_type": "observation", "confidence": initial_confidence}

    def get_belief(self, knowledge_id: int) -> Optional[Dict[str, Any]]:
        """查询 confidence 和 belief_type。"""
        row = self.conn.execute(
            "SELECT * FROM knowledge_beliefs WHERE knowledge_id = ?",
            (knowledge_id,)
        ).fetchone()
        if row:
            return dict(row)
        return None

    # ── 事件处理 ───────────────────────────────────────────

    def on_encounter(self, knowledge_id: int) -> Dict[str, Any]:
        """再次遇到某条知识 → 加固信念。"""
        belief = self.get_belief(knowledge_id)
        if not belief:
            return self.init_belief(knowledge_id, 0.40)

        new_conf = min(1.0, belief["confidence"] + REINFORCE_INCREMENT)
        new_encounters = (belief["encounter_count"] or 0) + 1
        new_pos = (belief["evidence_positive"] or 0) + 1
        now = datetime.now(timezone.utc).isoformat()

        self.conn.execute(
            """UPDATE knowledge_beliefs
               SET confidence = ?, encounter_count = ?, evidence_positive = ?,
                   last_reinforced_at = ?, updated_at = ?
               WHERE knowledge_id = ?""",
            (new_conf, new_encounters, new_pos, now, now, knowledge_id)
        )
        self.conn.commit()

        # 检查是否达到晋升阈值
        result = self._check_promotion(knowledge_id, belief["belief_type"], new_conf)
        result["confidence"] = new_conf
        result["encounter_count"] = new_encounters
        return result

    def on_feedback(self, knowledge_id: int, feedback: int = 1) -> Dict[str, Any]:
        """显式正/负反馈 → 调整置信度。"""
        belief = self.get_belief(knowledge_id)
        if not belief:
            return self.init_belief(knowledge_id, 0.40)

        now = datetime.now(timezone.utc).isoformat()

        if feedback > 0:
            new_conf = min(1.0, belief["confidence"] + FEEDBACK_INCREMENT)
            new_pos = (belief["evidence_positive"] or 0) + 1
            self.conn.execute(
                """UPDATE knowledge_beliefs
                   SET confidence = ?, evidence_positive = ?,
                       last_reinforced_at = ?, updated_at = ?
                   WHERE knowledge_id = ?""",
                (new_conf, new_pos, now, now, knowledge_id)
            )
        else:
            new_conf = max(0.0, belief["confidence"] - CORRECTION_PENALTY)
            new_neg = (belief["evidence_negative"] or 0) + 1
            self.conn.execute(
                """UPDATE knowledge_beliefs
                   SET confidence = ?, evidence_negative = ?,
                       last_corrected_at = ?, updated_at = ?
                   WHERE knowledge_id = ?""",
                (new_conf, new_neg, now, now, knowledge_id)
            )
        self.conn.commit()

        result = self._check_promotion(knowledge_id, belief["belief_type"], new_conf)
        result["confidence"] = new_conf
        return result

    def on_correction(self, knowledge_id: int) -> Dict[str, Any]:
        """用户纠正 → 大幅降级。"""
        return self.on_feedback(knowledge_id, -1)

    def on_contradiction(self, knowledge_id: int) -> Dict[str, Any]:
        """检测到冲突 → 中度降级。"""
        belief = self.get_belief(knowledge_id)
        if not belief:
            return self.init_belief(knowledge_id, 0.40)

        new_conf = max(0.0, belief["confidence"] - CONTRADICTION_PENALTY)
        new_neg = (belief["evidence_negative"] or 0) + 1
        now = datetime.now(timezone.utc).isoformat()

        self.conn.execute(
            """UPDATE knowledge_beliefs
               SET confidence = ?, evidence_negative = ?,
                   last_corrected_at = ?, updated_at = ?
               WHERE knowledge_id = ?""",
            (new_conf, new_neg, now, now, knowledge_id)
        )
        self.conn.commit()

        result = self._check_promotion(knowledge_id, belief["belief_type"], new_conf)
        result["confidence"] = new_conf
        return result

    # ── 晋升/降级 ─────────────────────────────────────────

    def _check_promotion(self, knowledge_id: int,
                          current_type: str,
                          confidence: float) -> Dict[str, Any]:
        """检查置信度是否达到晋升或触发降级阈值。"""
        now = datetime.now(timezone.utc).isoformat()
        action = "none"
        new_type = current_type

        if current_type == "observation" and confidence >= CONFIDENCE_BELIEF_MIN:
            # 晋升为 belief
            self.conn.execute(
                """UPDATE knowledge_beliefs
                   SET belief_type = 'belief', promoted_at = ?, updated_at = ?
                   WHERE knowledge_id = ?""",
                (now, now, knowledge_id)
            )
            # 同时更新 unified_knowledge 的 layer
            self.conn.execute(
                "UPDATE unified_knowledge SET layer = 'candidate', updated_at = ? WHERE id = ?",
                (now, knowledge_id)
            )
            action = "promoted_to_belief"
            new_type = "belief"
            logger.info("Belief: promoted %d to belief (conf=%.2f)", knowledge_id, confidence)

        elif current_type == "belief" and confidence >= CONFIDENCE_FACT_MIN:
            # 晋升为 fact
            self.conn.execute(
                """UPDATE knowledge_beliefs
                   SET belief_type = 'fact', promoted_at = ?, updated_at = ?
                   WHERE knowledge_id = ?""",
                (now, now, knowledge_id)
            )
            self.conn.execute(
                "UPDATE unified_knowledge SET layer = 'consolidated', updated_at = ? WHERE id = ?",
                (now, knowledge_id)
            )
            action = "promoted_to_fact"
            new_type = "fact"
            logger.info("Belief: promoted %d to fact (conf=%.2f)", knowledge_id, confidence)

        elif current_type == "belief" and confidence < CONFIDENCE_OBSERVATION_MAX:
            # 降级为 observation
            self.conn.execute(
                """UPDATE knowledge_beliefs
                   SET belief_type = 'observation', demoted_at = ?, updated_at = ?
                   WHERE knowledge_id = ?""",
                (now, now, knowledge_id)
            )
            self.conn.execute(
                "UPDATE unified_knowledge SET layer = 'instant', updated_at = ? WHERE id = ?",
                (now, knowledge_id)
            )
            action = "demoted_to_observation"
            new_type = "observation"
            logger.warning("Belief: demoted %d to observation (conf=%.2f)", knowledge_id, confidence)

        elif current_type == "fact" and confidence < CONFIDENCE_BELIEF_MIN:
            # 降级为 belief
            self.conn.execute(
                """UPDATE knowledge_beliefs
                   SET belief_type = 'belief', demoted_at = ?, updated_at = ?
                   WHERE knowledge_id = ?""",
                (now, now, knowledge_id)
            )
            self.conn.execute(
                "UPDATE unified_knowledge SET layer = 'candidate', updated_at = ? WHERE id = ?",
                (now, knowledge_id)
            )
            action = "demoted_to_belief"
            new_type = "belief"
            logger.warning("Belief: demoted %d to belief (conf=%.2f)", knowledge_id, confidence)

        self.conn.commit()
        return {"action": action, "belief_type": new_type, "confidence": confidence}

    # ── 批量更新 ───────────────────────────────────────────

    def update_all_beliefs(self) -> Dict[str, Any]:
        """批量更新所有 belief: 时间衰退 + 再评估。

        在 consolidate() 中定期调用。
        """
        now = datetime.now(timezone.utc)
        decay_cutoff = (now - timedelta(hours=DECAY_INTERVAL_HOURS)).isoformat()

        # 1. 时间衰退: 长时间未访问的知识置信度下降
        stale = self.conn.execute(
            """SELECT kb.knowledge_id, kb.confidence, kb.belief_type,
                      uk.last_accessed
               FROM knowledge_beliefs kb
               JOIN unified_knowledge uk ON kb.knowledge_id = uk.id
               WHERE uk.last_accessed IS NOT NULL
                 AND uk.last_accessed < ?
                 AND kb.belief_type IN ('belief', 'observation')""",
            (decay_cutoff,)
        ).fetchall()

        decayed = 0
        for row in stale:
            new_conf = max(0.0, row["confidence"] - DECAY_PER_INTERVAL)
            self.conn.execute(
                "UPDATE knowledge_beliefs SET confidence = ?, updated_at = ? "
                "WHERE knowledge_id = ?",
                (new_conf, now.isoformat(), row["knowledge_id"])
            )
            self._check_promotion(row["knowledge_id"], row["belief_type"], new_conf)
            decayed += 1

        # 2. 归档低置信度
        archived = self.conn.execute(
            """SELECT kb.knowledge_id, uk.content
               FROM knowledge_beliefs kb
               JOIN unified_knowledge uk ON kb.knowledge_id = uk.id
               WHERE kb.confidence < ? AND kb.belief_type = 'observation'
                 AND uk.status = 'active'""",
            (CONFIDENCE_ARCHIVE_THRESHOLD,)
        ).fetchall()

        for row in archived:
            self.conn.execute(
                "UPDATE unified_knowledge SET status = 'archived', updated_at = ? "
                "WHERE id = ?",
                (now.isoformat(), row["knowledge_id"])
            )
            logger.info("Belief: archived %d (conf < %.2f)",
                         row["knowledge_id"], CONFIDENCE_ARCHIVE_THRESHOLD)

        self.conn.commit()

        return {
            "decayed_count": decayed,
            "archived_count": len(archived),
        }

    def promote_knowledge(self, knowledge_id: int,
                           confidence: float = CONFIDENCE_FACT_MIN) -> Dict[str, Any]:
        """手动晋升一条知识到 fact 级别（用户确认后调用）。"""
        now = datetime.now(timezone.utc).isoformat()
        belief = self.get_belief(knowledge_id)
        if not belief:
            return self.init_belief(knowledge_id, confidence)

        new_type = "fact" if confidence >= CONFIDENCE_FACT_MIN else "belief"
        self.conn.execute(
            """UPDATE knowledge_beliefs
               SET belief_type = ?, confidence = ?, promoted_at = ?, updated_at = ?
               WHERE knowledge_id = ?""",
            (new_type, confidence, now, now, knowledge_id)
        )
        self.conn.execute(
            "UPDATE unified_knowledge SET layer = 'consolidated', updated_at = ? WHERE id = ?",
            (now, knowledge_id)
        )
        self.conn.commit()
        logger.info("Belief: manually promoted %d to %s", knowledge_id, new_type)
        return {"action": f"promoted_to_{new_type}", "belief_type": new_type, "confidence": confidence}

    # ── 统计 ───────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """belief 系统统计。"""
        try:
            obs = self.conn.execute(
                "SELECT COUNT(*) FROM knowledge_beliefs WHERE belief_type = 'observation'"
            ).fetchone()[0]
            bel = self.conn.execute(
                "SELECT COUNT(*) FROM knowledge_beliefs WHERE belief_type = 'belief'"
            ).fetchone()[0]
            fac = self.conn.execute(
                "SELECT COUNT(*) FROM knowledge_beliefs WHERE belief_type = 'fact'"
            ).fetchone()[0]
            avg_conf = self.conn.execute(
                "SELECT AVG(confidence) FROM knowledge_beliefs"
            ).fetchone()[0] or 0.0
            return {
                "observations": obs,
                "beliefs": bel,
                "facts": fac,
                "total": obs + bel + fac,
                "average_confidence": round(avg_conf, 3),
            }
        except Exception:
            return {"observations": 0, "beliefs": 0, "facts": 0, "total": 0, "average_confidence": 0.0}

    # ── 核心信念（自律系统） ───────────────────────────────

    def get_core_beliefs(self, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取核心信念列表，可选按 domain 过滤。

        Args:
            domain: 过滤域名，如 "自律"。None 返回全部。

        Returns:
            核心信念列表，每个包含 id/text/strength/domain/enforcement
        """
        if domain:
            return [b for b in CORE_BELIEFS if b.get("domain") == domain]
        return list(CORE_BELIEFS)

    def weaken_core_belief(self, belief_id: str, amount: float = 0.15,
                           reason: str = "") -> Dict[str, Any]:
        """降低核心信念强度，触发告警逻辑。

        Args:
            belief_id: 信念 ID
            amount: 降低量 (0-1)
            reason: 降低原因

        Returns:
            {"id": str, "old_strength": float, "new_strength": float,
             "alert": bool, "reminder": Optional[str]}
        """
        for belief in CORE_BELIEFS:
            if belief["id"] == belief_id:
                old = belief["strength"]
                belief["strength"] = max(0.0, old - amount)
                new_s = belief["strength"]
                logger.warning(
                    "Core belief '%s' weakened: %.2f → %.2f (reason: %s)",
                    belief_id, old, new_s, reason
                )

                result = {
                    "id": belief_id,
                    "old_strength": round(old, 2),
                    "new_strength": round(new_s, 2),
                    "alert": new_s < 0.3,
                }

                if new_s < 0.3:
                    result["alert_level"] = "critical"
                    result["reminder"] = (
                        f"⚠️ 核心信念「{belief['text']}」强度已降至 {new_s:.1f}，"
                        f"低于警戒线 0.3。请立即纠正行为。"
                    )
                elif new_s < 0.5:
                    result["alert_level"] = "warning"
                    result["reminder"] = (
                        f"⚠️ 核心信念「{belief['text']}」强度降至 {new_s:.1f}，建议关注。"
                    )
                else:
                    result["alert_level"] = "info"
                    result["reminder"] = None

                return result

        return {"error": f"Unknown belief_id: {belief_id}"}

    def strengthen_core_belief(self, belief_id: str, amount: float = 0.10) -> Dict[str, Any]:
        """提升核心信念强度。

        Args:
            belief_id: 信念 ID
            amount: 提升量 (0-1)

        Returns:
            {"id": str, "old_strength": float, "new_strength": float}
        """
        for belief in CORE_BELIEFS:
            if belief["id"] == belief_id:
                old = belief["strength"]
                belief["strength"] = min(1.0, old + amount)
                new_s = belief["strength"]
                if new_s > old:
                    logger.info(
                        "Core belief '%s' strengthened: %.2f → %.2f",
                        belief_id, old, new_s
                    )
                return {
                    "id": belief_id,
                    "old_strength": round(old, 2),
                    "new_strength": round(new_s, 2),
                }

        return {"error": f"Unknown belief_id: {belief_id}"}

    def get_alerting_beliefs(self, threshold: float = 0.3) -> List[Dict[str, Any]]:
        """获取强度低于阈值的核心信念。

        Args:
            threshold: 强度阈值 (默认 0.3)

        Returns:
            低于阈值的信念列表
        """
        return [b for b in CORE_BELIEFS if b["strength"] < threshold]

    def generate_reminder_text(self) -> Optional[str]:
        """生成针对低强度核心信念的上下文注入提醒。"""
        alerting = self.get_alerting_beliefs()
        if not alerting:
            return None

        parts = ["⚠️ 系统自律告警:"]
        for b in alerting:
            parts.append(f"  - {b['text']} (强度 {b['strength']:.1f})")
            # 如果强度接近 0，给出补救建议
            if b["strength"] < 0.1:
                parts.append(f"    → 建议立即执行 {b['text'].split('必须')[0]}必须的操作")
        return "\n".join(parts)
