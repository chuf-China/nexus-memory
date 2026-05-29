"""nexus_miner.py — Nexus 隐式模式挖掘引擎

从 interaction_log 中提取用户行为模式:
  1. 知识质量分: 用得多 + 纠正少 = 高质量 → 加速晋升
  2. 知识风险分: 纠正率高 = 高风险 → 标记审查
  3. 查询→知识关联: "问A时总用B" → 优化预加载
  4. 知识衰退检测: 长期不用但有效 → 归档建议

用法:
  from .miner import NexusMiner
  miner = NexusMiner()
  report = miner.mine_all()
  print(report)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_NEXUS_DB = Path.home() / ".hermes" / "data" / "nexus" / "nexus.db"


def _conn() -> Optional[sqlite3.Connection]:
    if not _NEXUS_DB.exists():
        return None
    c = sqlite3.connect(str(_NEXUS_DB))
    c.row_factory = sqlite3.Row
    return c


class NexusMiner:

    def __init__(self):
        self.conn = _conn()
        self.report: Dict[str, Any] = {}

    # ── 1. 知识质量分 ──────────────────────────────────────

    def score_knowledge_quality(self) -> List[Dict[str, Any]]:
        """为每条 active 知识计算质量分。

        分数公式:
          usage = 被用于交互的次数
          correction_rate = 涉及该知识的交互中被纠正的比例
          score = usage * (1 - correction_rate) + positive_feedback - negative_feedback * 2

        返回按 score 降序排列的知识列表。
        """
        if not self.conn:
            return []

        # 获取所有 interaction_log 中的 knowledge_used
        rows = self.conn.execute(
            "SELECT knowledge_used, correction_of FROM interaction_log "
            "ORDER BY created_at DESC LIMIT 1000"
        ).fetchall()

        # 统计每条知识: (usage_count, correction_count)
        usage: Dict[str, int] = Counter()
        corrections: Dict[str, int] = Counter()

        for r in rows:
            is_correction = r["correction_of"] is not None
            try:
                knowledge = json.loads(r["knowledge_used"]) if r["knowledge_used"] else []
            except Exception:
                continue
            for k in knowledge:
                kid = str(k.get("id"))
                if not kid:
                    continue
                usage[kid] += 1
                if is_correction:
                    corrections[kid] += 1

        # 获取每条知识的反馈数据
        knowledge_rows = self.conn.execute(
            "SELECT id, content, domain_scores, layer, positive_feedback, "
            "  negative_feedback, active_summary "
            "FROM unified_knowledge WHERE status = 'active'"
        ).fetchall()

        scored = []
        for r in knowledge_rows:
            kid = str(r["id"])
            u = usage.get(kid, 0)
            c = corrections.get(kid, 0)
            correction_rate = c / u if u > 0 else 0
            pf = r["positive_feedback"] or 0
            nf = r["negative_feedback"] or 0
            score = u * (1 - correction_rate) + pf - nf * 2

            try:
                ds = json.loads(r["domain_scores"])
            except Exception:
                ds = {}

            scored.append({
                "id": r["id"],
                "content": r["content"][:120],
                "layer": r["layer"],
                "domain": max(ds, key=ds.get) if ds else "unknown",
                "usage_count": u,
                "correction_count": c,
                "correction_rate": round(correction_rate, 3),
                "positive_feedback": pf,
                "negative_feedback": nf,
                "quality_score": round(score, 2),
                "summary": r["active_summary"] or "",
            })

        scored.sort(key=lambda x: -x["quality_score"])

        # 标记高风险知识
        for item in scored:
            if item["correction_rate"] > 0.3 and item["usage_count"] >= 3:
                item["flag"] = "🔴 高风险 — 纠正率过高"
            elif item["correction_rate"] > 0.1 and item["usage_count"] >= 5:
                item["flag"] = "🟡 中风险 — 需要审查"
            elif item["quality_score"] > 5:
                item["flag"] = "🟢 高质量 — 建议固化"
            else:
                item["flag"] = "⚪ 正常"

        return scored

    # ── 2. 查询→知识关联 ──────────────────────────────────

    def mine_query_patterns(self, min_occurrences: int = 3) -> List[Dict[str, Any]]:
        """挖掘查询与知识之间的关联规则。

        模式: "用户问 X 类问题时，总是用了 Y 类知识"
        用于: 优化预加载策略 (看到 X 关键词就预加载 Y)
        """
        if not self.conn:
            return []

        rows = self.conn.execute(
            "SELECT user_query, knowledge_used FROM interaction_log "
            "WHERE correction_of IS NULL "
            "ORDER BY created_at DESC LIMIT 1000"
        ).fetchall()

        # 提取关键词→知识ID 共现
        # 用简单的词频统计，不做复杂关联规则挖掘
        from collections import defaultdict
        # 关键词→{知识ID→count}
        kw_knowledge: Dict[str, Counter] = defaultdict(Counter)
        # 关键词出现总次数
        kw_total: Counter = Counter()

        # 中文停用词
        stopwords = {'一个', '这个', '那个', '什么', '怎么', '如何', '可以',
                     '需要', '没有', '不是', '就是', '还是', '因为', '所以',
                     '但是', '而且', '的', '了', '在', '是', '有', '和', '与'}

        for r in rows:
            query = (r["user_query"] or "").strip()
            if not query:
                continue
            try:
                knowledge = json.loads(r["knowledge_used"]) if r["knowledge_used"] else []
            except Exception:
                continue

            # 简单分词: 按空格/标点切分，取 2-6 字词
            import re
            tokens = set()
            for t in re.findall(r'[\u4e00-\u9fff\w]{2,}', query.lower()):
                if t not in stopwords and len(t) <= 6:
                    tokens.add(t)

            for t in tokens:
                kw_total[t] += 1
                for k in knowledge:
                    kid = str(k.get("id"))
                    if kid:
                        kw_knowledge[t][kid] += 1

        # 过滤低频，计算关联强度
        patterns = []
        for kw, kc in kw_knowledge.items():
            total = kw_total[kw]
            if total < min_occurrences:
                continue
            for kid, count in kc.most_common(3):
                confidence = count / total
                if confidence >= 0.5:  # 50%+ 的查询都用了这个知识
                    # 获取知识内容
                    krow = self.conn.execute(
                        "SELECT content FROM unified_knowledge WHERE id = ?",
                        (kid,)
                    ).fetchone()
                    if krow:
                        patterns.append({
                            "keyword": kw,
                            "knowledge_id": int(kid),
                            "knowledge_content": krow["content"][:80],
                            "occurrences": count,
                            "total_queries": total,
                            "confidence": round(confidence, 2),
                        })

        patterns.sort(key=lambda x: -x["confidence"])
        return patterns[:20]

    # ── 3. 知识衰退检测 ────────────────────────────────────

    def detect_stale_knowledge(self, days_threshold: int = 60) -> List[Dict[str, Any]]:
        """检测知识衰退: 长期未被使用但仍在 active 状态。

        标准:
          - consolidated: 60天未用 → 标记为可能需要审查
          - candidate: 30天未用 → 建议降级回 instant
          - instant: 自动清理由 curator 负责
        """
        if not self.conn:
            return []

        now = datetime.now(timezone.utc).isoformat()
        threshold = (datetime.now(timezone.utc) - timedelta(days=days_threshold)).isoformat()

        rows = self.conn.execute(
            "SELECT id, content, layer, last_accessed, positive_feedback, "
            "  active_summary "
            "FROM unified_knowledge WHERE status = 'active' "
            "  AND layer != 'instant' "
            "  AND last_accessed < ?",
            (threshold,)
        ).fetchall()

        stale = []
        for r in rows:
            last_acc = r["last_accessed"] or ""
            if not last_acc:
                continue

            last_dt = datetime.fromisoformat(last_acc)
            days_ago = (datetime.now(timezone.utc) - last_dt).days

            suggestion = ""
            if r["layer"] == "consolidated" and days_ago > 90:
                suggestion = "建议审查: 90+ 天未使用，可能已过时"
            elif r["layer"] == "consolidated" and days_ago > 60:
                suggestion = "建议关注: 60+ 天未使用"
            elif r["layer"] == "candidate" and days_ago > 30:
                suggestion = "建议降级: candidate 30+ 天未使用"

            if suggestion:
                stale.append({
                    "id": r["id"],
                    "content": r["content"][:100],
                    "layer": r["layer"],
                    "last_accessed": last_acc,
                    "days_since_access": days_ago,
                    "positive_feedback": r["positive_feedback"],
                    "suggestion": suggestion,
                })

        stale.sort(key=lambda x: -x["days_since_access"])
        return stale

    # ── 4. 自我建模 — 从 interaction_log 提取 agent 自己的能力边界 ──

    def mine_self_model(self) -> Dict[str, Any]:
        """分析 interaction_log 构建 agent 的自我认知。

        产出:
          domain_accuracy: 各领域的纠正率 → 知道"我在哪容易犯错"
          query_coverage:  哪些查询有足够知识 → 知道"这我懂"
          capability_boundary: 能力边界声明
        """
        if not self.conn:
            return {"domain_accuracy": {}, "query_coverage": [], "capability_boundary": []}

        rows = self.conn.execute(
            "SELECT user_query, knowledge_used, correction_of FROM interaction_log "
            "ORDER BY created_at DESC LIMIT 2000"
        ).fetchall()

        # 按领域统计准确率
        # 用 knowledge 的 domain_scores 推断"这是什么领域的问题"
        domain_stats: Dict[str, Dict] = {}
        total_queries = 0
        corrected_queries = 0
        domain_queries: Dict[str, int] = {}
        domain_errors: Dict[str, int] = {}

        for r in rows:
            total_queries += 1
            if r["correction_of"] is not None:
                corrected_queries += 1

            try:
                knowledge = json.loads(r["knowledge_used"]) if r["knowledge_used"] else []
            except Exception:
                continue

            # 从使用的知识推断涉及的领域
            domains_in_query = set()
            for k in knowledge:
                try:
                    ds = json.loads(k.get("domain_scores", "{}"))
                    for d, score in ds.items():
                        if score > 0:
                            domains_in_query.add(d)
                except Exception:
                    pass

            for d in domains_in_query:
                domain_queries[d] = domain_queries.get(d, 0) + 1
                if r["correction_of"] is not None:
                    domain_errors[d] = domain_errors.get(d, 0) + 1

        # 计算各领域准确率
        domain_accuracy = {}
        for d in set(list(domain_queries.keys()) + list(domain_errors.keys())):
            total = domain_queries.get(d, 0)
            errors = domain_errors.get(d, 0)
            accuracy = 1 - (errors / total) if total > 0 else 0
            domain_accuracy[d] = {
                "total_queries": total,
                "errors": errors,
                "accuracy": round(accuracy, 3),
                "confidence": "high" if accuracy > 0.9 and total >= 5 else (
                    "medium" if accuracy > 0.7 else "low"
                ),
            }

        # 生成能力边界声明
        capability_boundary = []
        for d, stats in sorted(domain_accuracy.items(), key=lambda x: -x[1]["total_queries"]):
            if stats["confidence"] == "high":
                capability_boundary.append(
                    f"我在 {d} 领域有高可靠性 (准确率 {stats['accuracy']:.0%}, {stats['total_queries']} 次交互)"
                )
            elif stats["confidence"] == "low":
                capability_boundary.append(
                    f"我在 {d} 领域可靠性较低 (准确率 {stats['accuracy']:.0%}, {stats['total_queries']} 次交互，{stats['errors']} 次纠正)"
                )

        # 查询覆盖率: 哪些查询有足够知识储备
        query_coverage = []
        kw_count = {}
        for r in rows:
            query = (r["user_query"] or "").strip()[:20]
            if query and len(query) >= 4:
                kw_count[query] = kw_count.get(query, 0) + 1

        for query, count in sorted(kw_count.items(), key=lambda x: -x[1])[:20]:
            query_coverage.append({"keyword": query, "count": count})

        return {
            "domain_accuracy": domain_accuracy,
            "query_coverage": query_coverage,
            "capability_boundary": capability_boundary,
            "total_queries": total_queries,
            "overall_accuracy": round(1 - (corrected_queries / total_queries), 3) if total_queries > 0 else 0,
        }

    def sync_self_model(self):
        """将自我模型写回 Nexus DB 作为知识条目。"""
        model = self.mine_self_model()
        if not model.get("capability_boundary"):
            return

        from .core import NexusCore
        from pathlib import Path
        nc = NexusCore(str(Path.home() / ".hermes" / "data" / "nexus" / "nexus.db"))

        for statement in model["capability_boundary"]:
            nc.write(
                content=statement,
                user_id="agent_self",
                source_session_id="self_model_miner",
            )

        nc.close()

    # ── 5. 全量报告 ────────────────────────────────────────

    def mine_all(self) -> Dict[str, Any]:
        """全量挖掘，输出完整报告。"""
        quality = self.score_knowledge_quality()
        patterns = self.mine_query_patterns()
        stale = self.detect_stale_knowledge()

        # 汇总统计
        total_active = len(quality)
        high_risk = len([k for k in quality if k.get("flag", "").startswith("🔴")])
        medium_risk = len([k for k in quality if k.get("flag", "").startswith("🟡")])
        high_quality = len([k for k in quality if k.get("flag", "").startswith("🟢")])

        self.report = {
            "summary": {
                "total_active_knowledge": total_active,
                "high_quality": high_quality,
                "medium_risk": medium_risk,
                "high_risk": high_risk,
                "total_patterns": len(patterns),
                "total_stale": len(stale),
            },
            "top_knowledge": quality[:10],
            "high_risk_knowledge": [k for k in quality if k.get("flag", "").startswith("🔴")],
            "query_patterns": patterns,
            "stale_knowledge": stale,
            "mined_at": datetime.now(timezone.utc).isoformat(),
        }
        return self.report

    def close(self):
        if self.conn:
            self.conn.close()


# ── Graph Auto-Build (LLM-enhanced) ───────────────────────

_EXTRACT_ENTITIES_PROMPT = """从以下文本中提取实体和关系。
只提取明确提到的实体，不要推测。

输出 JSON:
{
  "entities": [{"name": "...", "type": "Person|Project|Tool|Concept|Service|Company"}],
  "edges": [{"source": "...", "target": "...", "relation": "USES|CAUSES|DEPENDS_ON|BELONGS_TO|CONTAINS|WORKS_WITH|PART_OF"}]
}

如果没有可提取的实体，返回 {"entities": [], "edges": []}
"""


def auto_build_graph(content: str, conn=None) -> Dict[str, Any]:
    """Extract entities and edges from content using LLM, then upsert to graph.

    Returns: {"entities_added": int, "edges_added": int}
    """
    if not content or len(content) < 20:
        return {"entities_added": 0, "edges_added": 0}

    # Try LLM extraction
    entities, edges = _llm_extract_entities(content)
    if not entities:
        return {"entities_added": 0, "edges_added": 0}

    if conn is None:
        from pathlib import Path
        db_path = Path.home() / ".hermes" / "data" / "nexus" / "nexus.db"
        if not db_path.exists():
            return {"entities_added": 0, "edges_added": 0}
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        should_close = True
    else:
        should_close = False

    entities_added = 0
    edges_added = 0
    try:
        for e in entities:
            if _upsert_entity(conn, e):
                entities_added += 1
        for ed in edges:
            if _upsert_edge(conn, ed):
                edges_added += 1
        conn.commit()
    except Exception as ex:
        logger.debug("auto_build_graph: %s", ex)
    finally:
        if should_close:
            conn.close()

    return {"entities_added": entities_added, "edges_added": edges_added}


def _llm_extract_entities(content: str) -> Tuple[List[Dict], List[Dict]]:
    """Use LLM to extract entities and edges from content."""
    try:
        from .local import get_client as _get_llm
        client = _get_llm()
        if not client or not client.ping():
            return [], []

        resp = client.chat([
            {"role": "system", "content": _EXTRACT_ENTITIES_PROMPT},
            {"role": "user", "content": content[:2000]},
        ], max_tokens=512)

        text = resp.get("response", "") or resp.get("message", {}).get("content", "")
        if not text:
            return [], []

        # Parse JSON
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return [], []

        parsed = json.loads(text[start:end + 1])
        entities = [e for e in parsed.get("entities", []) if e.get("name")]
        edges = [e for e in parsed.get("edges", [])
                 if e.get("source") and e.get("target")]
        return entities, edges

    except Exception as e:
        logger.debug("LLM entity extraction failed: %s", e)
        return [], []


def _upsert_entity(conn, entity: Dict) -> bool:
    """Insert or update entity in entity_relations (as entity_a or entity_b)."""
    name = entity.get("name", "").strip()
    if not name or len(name) < 2:
        return False
    # Check if entity already exists in relations
    existing = conn.execute(
        "SELECT id FROM entity_relations "
        "WHERE LOWER(entity_a) = LOWER(?) OR LOWER(entity_b) = LOWER(?) LIMIT 1",
        (name, name)
    ).fetchone()
    if existing:
        return False
    # Create a seed relation with itself (so entity appears in graph)
    conn.execute("""
        INSERT OR IGNORE INTO entity_relations
        (entity_a, entity_b, relation_type, weight)
        VALUES (?, ?, 'self', 0.1)
    """, (name, name))
    return True


def _upsert_edge(conn, edge: Dict) -> bool:
    """Insert or update edge in entity_relations."""
    src = edge.get("source", "").strip()
    tgt = edge.get("target", "").strip()
    rel = edge.get("relation", "related").strip()
    if not src or not tgt or len(src) < 2 or len(tgt) < 2:
        return False
    existing = conn.execute(
        "SELECT id FROM entity_relations "
        "WHERE LOWER(entity_a) = LOWER(?) AND LOWER(entity_b) = LOWER(?) "
        "AND relation_type = ?",
        (src, tgt, rel)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE entity_relations SET hit_count = hit_count + 1, "
            "last_seen = datetime('now'), weight = MIN(5.0, weight + 0.5) "
            "WHERE id = ?",
            (existing["id"],)
        )
        return False
    conn.execute("""
        INSERT INTO entity_relations
        (entity_a, entity_b, relation_type, weight)
        VALUES (?, ?, ?, 1.0)
    """, (src, tgt, rel))
    return True
