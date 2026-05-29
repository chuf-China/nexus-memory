"""nexus_drive.py — Nexus 自主驱动引擎

两个功能:
  1. 自主巩固: 不依赖定时，由事件触发
  2. 主动检索: 不等用户问，预判知识需求

事件模型:
  WRITE     — 写入新知识
  CORRECT   — 用户纠正
  SEARCH    — 主动检索触发
  TICK      — 心跳（可选降级）
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_NEXUS_DB = Path.home() / ".hermes" / "data" / "nexus.db"


class NexusDrive:
    """自主驱动引擎。绑定到 Agent 生命周期，由事件驱动。"""

    def __init__(self, agent=None):
        self.agent = agent  # AIAgent 实例，用于预检索
        self._event_log: deque = deque(maxlen=100)  # 最近100个事件
        self._nc = None  # 复用 NexusCore 实例

    def _get_nexus_core(self):
        """获取或创建 NexusCore 实例（连接复用）。"""
        if self._nc is None:
            try:
                from agent.nexus_core import NexusCore
                self._nc = NexusCore(str(_NEXUS_DB))
            except Exception as e:
                logger.debug("NexusDrive: failed to create NexusCore: %s", e)
                return None
        return self._nc

    # ── 1. 事件驱动巩固 ────────────────────────────────

    def on_write(self, knowledge_id: int):
        """写入新知识后: 检查是否需要立即巩固。

        触发条件:
          - instant 层超过 50 条 → 立即 consolidate
          - 新知识有冲突 → 立即检测
        """
        self._log_event("write", {"id": knowledge_id})

        try:
            nc = self._get_nexus_core()
            if nc is None:
                return

            # 检查 instant 层数量
            count = nc._conn().execute(
                "SELECT COUNT(*) FROM unified_knowledge "
                "WHERE layer='instant' AND status='active'"
            ).fetchone()[0]

            if count >= 50:
                logger.info("NexusDrive: instant pileup (%d), auto-consolidating", count)
                nc.consolidate()
        except Exception as e:
            logger.debug("NexusDrive on_write: %s", e)

    def on_correct(self, knowledge_id: int):
        """用户纠正后: 立即降级+巩固。

        触发条件:
          - 被纠正的知识立即降级
          - 检查同域其他知识是否需要联动调整
        """
        self._log_event("correct", {"id": knowledge_id})

        try:
            nc = self._get_nexus_core()
            if nc is None:
                return

            # 1. 降级被纠正的知识
            nc._demote(knowledge_id, "user_correction", "default")

            # 2. 运行 miner 检查同域风险
            from agent.nexus_miner import NexusMiner
            miner = NexusMiner()
            report = miner.mine_all()
            miner.close()

            for k in report.get("high_risk_knowledge", []):
                nc._demote(k["id"], "auto: 纠正链风险", "default")
        except Exception as e:
            logger.debug("NexusDrive on_correct: %s", e)

    def on_tick(self):
        """心跳（可选）: 检查是否有积压事件需要处理。

        不依赖定时器，由 agent 在每次对话间隙调用。
        """
        if len(self._event_log) < 5:
            return  # 事件太少，不用管

        recent_writes = sum(1 for e in self._event_log if e["type"] == "write")
        recent_corrects = sum(1 for e in self._event_log if e["type"] == "correct")

        if recent_writes > 20:
            try:
                nc = self._get_nexus_core()
                if nc:
                    nc.consolidate()
                    self._event_log.clear()
                    logger.info("NexusDrive: tick-triggered consolidate (%d recent writes)", recent_writes)
            except Exception as e:
                logger.debug("NexusDrive on_tick: %s", e)

    # ── 2. 主动检索 ─────────────────────────────────────

    def prefetch(self, context: str = "") -> List[Dict[str, Any]]:
        """根据当前上下文预判知识需求。

        策略:
          1. 时间感知: 交易时段优先返回 A 股知识
          2. 上下文关键词: 从 context 中提取关键词搜索
          3. Miner 关联规则: 用户问过 A 后通常会问 B

        返回: 预加载的知识列表（供 agent 缓存）
        """
        results = []
        now = datetime.now(timezone.utc)
        hour = now.hour + 8  # UTC → 北京时间

        try:
            nc = self._get_nexus_core()
            if nc is None:
                return results

            # 策略 1: 交易时段预加载
            if 9 <= hour <= 15:
                market_knowledge = nc.search("A股", mode="hybrid", limit=3)
                results.extend(market_knowledge)

            # 策略 2: 上下文关键词
            if context and len(context) > 4:
                # 提取核心词（简单截取前 20 个字）
                kw = context.strip()[:20]
                ctx_results = nc.search(kw, mode="hybrid", limit=3)
                results.extend(ctx_results)
        except Exception as e:
            logger.debug("NexusDrive prefetch: %s", e)

        # 去重
        seen = set()
        deduped = []
        for r in results:
            rid = r.get("id") or r.get("entry_id")
            if rid and rid not in seen:
                seen.add(rid)
                deduped.append(r)

        return deduped[:5]

    def on_user_message(self, message: str):
        """用户发了新消息: 预判知识需求，预热 agent 记忆。

        这是主动检索的入口。在 agent 处理用户消息之前调用。
        """
        if not message or len(message) < 4:
            return

        prefetched = self.prefetch(context=message)
        if prefetched and self.agent:
            # 把预检索的知识注入 agent 的 _nexus_knowledge_used
            existing = getattr(self.agent, "_nexus_knowledge_used", None) or []
            merged = {r.get("id") or r.get("entry_id"): r for r in (existing + prefetched)}
            self.agent._nexus_knowledge_used = list(merged.values())
            logger.debug("NexusDrive: prefetched %d knowledge items", len(prefetched))

    # ── 事件日志 ────────────────────────────────────────

    def _log_event(self, event_type: str, data: dict):
        self._event_log.append({
            "type": event_type,
            "data": data,
            "ts": time.time(),
        })

    def status(self) -> Dict[str, Any]:
        return {
            "event_queue_size": len(self._event_log),
            "recent_events": list(self._event_log)[-10:],
            "has_agent": self.agent is not None,
        }
