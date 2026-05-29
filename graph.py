"""nexus_graph.py — Nexus 实体关系图

在 SQLite 上构建轻量级知识图谱：
- 实体自动提取（jieba 词性标注 + 启发式规则）
- 关系存储（共现实体自动链接）
- 图遍历（WITH RECURSIVE CTE）

用法:
  from .graph import EntityGraph
  g = EntityGraph(conn)  # reuse nexus.db connection
  g.extract_and_link(entry_id=1, content="用户偏好简洁回答")
  related = g.traverse(entity="简洁回答", max_depth=2)
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ── 实体提取 ──────────────────────────────────────────────

# 简单规则：提取引号内容、大写短语、数字代号、中英文复合词
_QUOTED_RE = re.compile(r'[""]([^""]{2,60})[""]')
_CAPITALIZED_RE = re.compile(r'\b[A-Z][A-Za-z0-9_/-]{2,40}\b')
_CODE_RE = re.compile(r'\b\d{5,6}\b')  # 股票代码
# 中英混合: 字母+中文组合 (如 APScheduler调度器, fastembed向量)
_MIXED_RE = re.compile(r'(?:[A-Za-z0-9_/-]{2,}(?:[\u4e00-\u9fff]{2,})|(?:[\u4e00-\u9fff]{2,}(?:[A-Za-z0-9_/-]{2,})))')
# 中文词: 有意义的复合词 (4-12字, 排除常见停用词)
_CJK_PHRASE_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]{4,12}')


# 停用词 — 常见中文动词/虚词/泛词 + 技术泛词
_STOP_WORDS = frozenset({
    '一个', '这个', '那个', '什么', '如何', '可以', '需要', '没有',
    '不是', '就是', '还是', '或者', '因为', '所以', '但是', '而且',
    '虽然', '如果', '那么', '已经', '通过', '进行', '使用', '采用',
    '用于', '属于', '作为', '基于', '关于', '对于', '按照', '根据',
    '来自', '位于', '开始', '结束', '包括', '提供', '实现', '支持',
    # 动词/助词
    '具有', '成为', '进入', '看到', '知道', '认为', '表示', '说明',
    '了解', '发现', '得到', '形成', '选择', '设置', '调整', '提升',
    # 方向/数量
    '第一', '第二', '第三', '目前', '当前', '主要', '所有', '全部',
    '各种', '不同', '其他', '一些', '多个', '大量', '部分', '基本',
    # 技术停用词（函数/类/方法/变量等泛称）
    '函数', '方法', '类', '变量', '参数', '返回', '输入', '输出',
    '模块', '文件', '目录', '路径', '配置', '选项', '默认', '系统',
    # 英文停用词
    'the', 'this', 'that', 'what', 'how', 'why', 'when', 'where',
    'which', 'were', 'was', 'have', 'has', 'had', 'from', 'with',
})


def extract_entities(text: str) -> List[str]:
    """从文本中提取候选实体。

    策略(优先级从高到低):
      1. 引号内容（完整保留）
      2. jieba 词性标注: nr(人名)/ns(地名)/nt(机构)/nz(专名)/n(名词+长)
      3. 中英混合词 (APScheduler调度器)
      4. 大写短语 (专有名词)
      5. 股票代码
      6. 中文复合词(4字+, fallback)
    去重 + 过滤停用词 + 短词。
    """
    if not text:
        return []

    entities: List[str] = []
    seen: Set[str] = set()

    def _add(e: str):
        e = e.strip().strip('.,;:!?()[]{}""\' ')
        if len(e) < 2 or len(e) > 60:
            return
        if e.isdigit():
            return
        if e.lower() in _STOP_WORDS:
            return
        key = e.lower()
        if key not in seen:
            seen.add(key)
            entities.append(e)

    # 1. 引号内容（高优先级，完整保留）
    for m in _QUOTED_RE.finditer(text):
        _add(m.group(1))

    # 2. jieba POS tagging — 优先提取专有名词
    _POS实体 = frozenset({'nr', 'ns', 'nt', 'nz'})  # 人名/地名/机构/专名
    _POS排除 = frozenset({'v', 'vn', 'vg', 'vf', 'vd', 'vi', 'vl', 'vu'})  # 动词类
    try:
        import jieba.posseg as pseg
        for word, flag in pseg.cut(text):
            if len(word) < 2:
                continue
            if flag in _POS实体:
                _add(word)
            elif flag == 'n' and len(word) > 3:
                # 长名词（排除动词和泛词）
                if flag not in _POS排除 and word not in _STOP_WORDS:
                    _add(word)
    except ImportError:
        pass  # jieba not available, rely on regex fallback

    # 3. 中英混合词
    for m in _MIXED_RE.finditer(text):
        _add(m.group(0))

    # 4. 大写短语 (专有名词)
    for m in _CAPITALIZED_RE.finditer(text):
        _add(m.group(0))

    # 5. 股票代码
    for m in _CODE_RE.finditer(text):
        _add(m.group(0))

    # 6. 中文复合词 (4-12字, 排除停用词 — fallback for missed entities)
    _动词前缀 = frozenset({'进行', '通过', '使用', '采用', '利用', '基于', '关于'})
    _动词后缀 = frozenset({'进行', '处理', '实现', '完成', '执行', '分析', '推理', '分词'})
    for m in _CJK_PHRASE_RE.finditer(text):
        phrase = m.group(0)
        # 过滤: 以动词开头或以动词结尾的短语
        if any(phrase.startswith(p) for p in _动词前缀):
            continue
        if any(phrase.endswith(s) for s in _动词后缀):
            continue
        _add(phrase)

    return entities


def _normalize_entity(e: str) -> str:
    """提取实体的核心部分，去掉上下文无关的前后缀。

    如 '用户使用APScheduler' → 'APScheduler'
        'APScheduler调度器' → 'APScheduler'
        'interval两种触发方式' → 'interval'
        '股量化交易' → '量化交易'
    """
    # 中英混合: 提取英文部分
    m = re.match(r'^[\u4e00-\u9fff\u3400-\u4dbf]{0,4}([A-Za-z][A-Za-z0-9_/-]{1,})', e)
    if m:
        return m.group(1)
    # 中英混合: 提取中文核心部分 (去掉前后单字动词)
    e = re.sub(r'^(使用|采用|通过|进行|用于|基于|具有|成为|进入)', '', e)
    e = re.sub(r'(支持|实现|提供|开始|结束|方式|方法|系统|工具|机制)$', '', e)
    return e


def extract_entity_pairs(text: str) -> List[Tuple[str, str]]:
    """提取候选实体对（共现实体生成关系）。"""
    entities = extract_entities(text)
    # 归一化
    core_entities = [_normalize_entity(e) for e in entities]
    # 去重保留顺序
    seen: Set[str] = set()
    deduped = []
    for e in core_entities:
        key = e.lower()
        if key not in seen and len(e) >= 2:
            seen.add(key)
            deduped.append(e)
    
    pairs = []
    for i in range(len(deduped)):
        for j in range(i + 1, len(deduped)):
            pairs.append((deduped[i], deduped[j]))
    return pairs


# ── 关系权重词 ──────────────────────────────────────────

_RELATION_KEYWORDS = {
    '正向': ['喜欢', '偏好', '倾向于', '使用', '采用', '推荐', '建议', '利好', '促进'],
    '负向': ['不喜欢', '避免', '反对', '禁止', '警告', '风险', '问题', '利空', '抑制'],
    '因果': ['因为', '导致', '引发', '推动', '抑制', '影响', '关联'],
    '所属': ['属于', '包含', '包括', '旗下', '子公司', '部门'],
    '使用': ['用', '利用', '借助', '依靠', '依赖', '调用', '运行', '加载', '部署'],
    '依赖': ['依赖', '需要', '基于', '底层', '底层依赖', 'require', 'import'],
    '替代': ['替代', '取代', '代替', '优于', '胜过', 'replace', 'instead'],
    '包含': ['包含', '内含', '集成', '内置', '搭载', '内嵌'],
    '时序': ['之前', '之后', '然后', '接着', '随后', '先于', '晚于', 'before', 'after'],
}


def _infer_relation(text: str, entity_a: str, entity_b: str) -> str:
    """启发式推断两实体间的关系类型。"""
    text_lower = text.lower()
    a_lower = entity_a.lower()
    b_lower = entity_b.lower()

    for rel_type, keywords in _RELATION_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                # 确认两个实体都在关键词附近
                before_kw = text_lower[:text_lower.find(kw)]
                after_kw = text_lower[text_lower.find(kw) + len(kw):]
                if (a_lower in before_kw and b_lower in after_kw) or \
                   (b_lower in before_kw and a_lower in after_kw):
                    return rel_type
    return '共现'


# ═══════════════════════════════════════════════════════════
# EntityGraph
# ═══════════════════════════════════════════════════════════


_RELATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS entity_relations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entry    INTEGER REFERENCES unified_knowledge(id),
    target_entry    INTEGER REFERENCES unified_knowledge(id),
    entity_a        TEXT NOT NULL,
    entity_b        TEXT NOT NULL,
    relation_type   TEXT DEFAULT '共现',
    weight          REAL DEFAULT 1.0,
    first_seen      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hit_count       INTEGER DEFAULT 1,
    UNIQUE(entity_a, entity_b, relation_type)
);
CREATE INDEX IF NOT EXISTS idx_er_entity_a ON entity_relations(entity_a);
CREATE INDEX IF NOT EXISTS idx_er_entity_b ON entity_relations(entity_b);
CREATE INDEX IF NOT EXISTS idx_er_source ON entity_relations(source_entry);
CREATE INDEX IF NOT EXISTS idx_er_target ON entity_relations(target_entry);
"""

_ADJACENCY_CACHE_SQL = """
CREATE TABLE IF NOT EXISTS adjacency_cache (
    entity_name     TEXT NOT NULL,
    neighbor_name   TEXT NOT NULL,
    relation_type   TEXT NOT NULL DEFAULT '共现',
    direction       TEXT NOT NULL DEFAULT 'both',
    depth           INTEGER NOT NULL DEFAULT 1,
    weight          REAL DEFAULT 1.0,
    hop_path        TEXT,
    PRIMARY KEY (entity_name, neighbor_name, relation_type, direction, depth)
);
CREATE INDEX IF NOT EXISTS idx_adj_entity ON adjacency_cache(entity_name, depth);
CREATE INDEX IF NOT EXISTS idx_adj_neighbor ON adjacency_cache(neighbor_name, depth);
"""


class EntityGraph:
    """轻量级知识图谱，构建在 SQLite 上。"""

    def __init__(self, conn, max_depth: int = 3):
        self.conn = conn
        self.max_depth = max_depth
        self._init_table()

    def _init_table(self):
        self.conn.executescript(_RELATION_TABLE_SQL)
        self.conn.executescript(_ADJACENCY_CACHE_SQL)
        self.conn.commit()

    # ── Adjacency Cache ─────────────────────────────────────

    def update_adjacency_cache(self, entity_name: str, max_depth: int = 3):
        """Update adjacency cache for an entity up to max_depth."""
        el = entity_name.lower()
        # Clear old entries
        self.conn.execute(
            "DELETE FROM adjacency_cache WHERE entity_name = ? OR neighbor_name = ?",
            (el, el)
        )

        # Depth 1: Direct neighbors from entity_relations
        visited = {el}
        frontier = {el}
        for depth in range(1, max_depth + 1):
            next_frontier = set()
            for node in frontier:
                rows = self.conn.execute("""
                    SELECT DISTINCT
                        CASE WHEN LOWER(entity_a) = ? THEN entity_b ELSE entity_a END AS neighbor,
                        relation_type,
                        weight
                    FROM entity_relations
                    WHERE (LOWER(entity_a) = ? OR LOWER(entity_b) = ?)
                """, (node, node, node)).fetchall()
                for r in rows:
                    neighbor = r["neighbor"].lower()
                    if neighbor not in visited:
                        self.conn.execute("""
                            INSERT OR IGNORE INTO adjacency_cache
                            (entity_name, neighbor_name, relation_type, direction, depth, weight, hop_path)
                            VALUES (?, ?, ?, 'both', ?, ?, ?)
                        """, (el, neighbor, r["relation_type"], depth, r["weight"],
                              json.dumps([el, neighbor])))
                        visited.add(neighbor)
                        next_frontier.add(neighbor)
            frontier = next_frontier
            if not frontier:
                break

        self.conn.commit()

    def rebuild_adjacency_cache(self, max_depth: int = 3):
        """Full rebuild of adjacency cache up to max_depth."""
        entities = self.conn.execute(
            "SELECT DISTINCT entity_a FROM entity_relations "
            "UNION SELECT DISTINCT entity_b FROM entity_relations"
        ).fetchall()
        for r in entities:
            self.update_adjacency_cache(r[0], max_depth=max_depth)
        logger.info("Adjacency cache rebuilt for %d entities (depth=%d)", len(entities), max_depth)

    # ── Write ─────────────────────────────────────────────

    def extract_and_link(self, entry_id: int, content: str):
        """从 content 提取实体，建立跨条目的关联边。

        对于 content 中出现的每对实体:
          1. 查已有条目中哪些也提到了这些实体
          2. 建立或更新 entity_relations
        """
        entities = extract_entities(content)
        if len(entities) < 2:
            return  # 单实体无法建关系

        pairs = extract_entity_pairs(content)
        now = time.time()

        for a, b in pairs:
            rel = _infer_relation(content, a, b)

            # 查已有条目中谁提到了 a 和 b
            related_entries = self._find_entries_with_both(a, b, exclude=entry_id)

            # 写入关系（即使没有关联条目也记录实体对）
            self.conn.execute("""
                INSERT INTO entity_relations
                    (source_entry, target_entry, entity_a, entity_b,
                     relation_type, weight, first_seen, last_seen, hit_count)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), 1)
                ON CONFLICT(entity_a, entity_b, relation_type) DO UPDATE SET
                    hit_count = hit_count + 1,
                    last_seen = datetime('now'),
                    weight = MIN(5.0, weight + 0.5)
            """, (entry_id, None, a, b, rel, 1.0))

            # 如果有其他条目也包含这对实体，建立条目间边
            for tid in related_entries:
                self.conn.execute("""
                    INSERT INTO entity_relations
                        (source_entry, target_entry, entity_a, entity_b,
                         relation_type, weight, first_seen, last_seen, hit_count)
                    VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), 1)
                    ON CONFLICT(entity_a, entity_b, relation_type) DO UPDATE SET
                        hit_count = hit_count + 1,
                        last_seen = datetime('now'),
                        weight = MIN(5.0, weight + 0.5)
                """, (entry_id, tid, a, b, rel, 1.0))

        self.conn.commit()

        # Update adjacency cache for affected entities
        try:
            for entity in entities[:5]:
                self.update_adjacency_cache(entity)
        except Exception:
            pass  # cache update is best-effort

    def _find_entries_with_both(self, entity_a: str, entity_b: str,
                                 exclude: Optional[int] = None) -> List[int]:
        """查找同时提到 entity_a 和 entity_b 的其他条目。"""
        a_lower = entity_a.lower()
        b_lower = entity_b.lower()

        rows = self.conn.execute(
            "SELECT id, content FROM unified_knowledge "
            "WHERE status = 'active' AND id != ?",
            (exclude or 0,)
        ).fetchall()

        matches = []
        for r in rows:
            c = r["content"].lower()
            if a_lower in c and b_lower in c:
                matches.append(r["id"])
        return matches

    # ── Read ──────────────────────────────────────────────

    def traverse(self, entity: str, max_depth: int = 2,
                 min_weight: float = 0.5) -> List[Dict[str, Any]]:
        """BFS 图遍历，从 entity 出发。

        优先使用邻接表缓存 (O(1) lookup)，缓存未命中时回退到 CTE 遍历。

        返回 [{entity, relation_type, depth, weight, entries}, ...]
        """
        entity_lower = entity.lower()

        # ── 优先: 邻接表缓存查询 (O(1) per depth) ──────────
        try:
            cached = self.conn.execute("""
                SELECT neighbor_name, relation_type, depth, weight, hop_path
                FROM adjacency_cache
                WHERE entity_name = ? AND depth <= ?
                ORDER BY depth, weight DESC
                LIMIT 50
            """, (entity_lower, max_depth)).fetchall()

            if cached:
                return [
                    {
                        "entity": r["neighbor_name"],
                        "relation_type": r["relation_type"],
                        "depth": r["depth"],
                        "weight": r["weight"],
                    }
                    for r in cached
                ]
        except Exception:
            pass  # adjacency_cache table might not exist yet

        # ── 回退: CTE 递归遍历 ─────────────────────────────
        query = """
        WITH RECURSIVE graph_walk AS (
            SELECT
                CASE WHEN LOWER(entity_a) = ? THEN entity_b ELSE entity_a END AS node,
                relation_type,
                1 AS depth,
                weight,
                source_entry,
                target_entry
            FROM entity_relations
            WHERE (LOWER(entity_a) = ? OR LOWER(entity_b) = ?)
              AND weight >= ?

            UNION

            SELECT
                CASE WHEN LOWER(er.entity_a) = LOWER(gw.node)
                     THEN er.entity_b ELSE er.entity_a END,
                er.relation_type,
                gw.depth + 1,
                er.weight,
                er.source_entry,
                er.target_entry
            FROM entity_relations er
            JOIN graph_walk gw ON (
                LOWER(er.entity_a) = LOWER(gw.node)
                OR LOWER(er.entity_b) = LOWER(gw.node)
            )
            WHERE gw.depth < ?
              AND er.weight >= ?
              AND LOWER(CASE WHEN LOWER(er.entity_a) = LOWER(gw.node)
                             THEN er.entity_b ELSE er.entity_a END) != ?
        )
        SELECT DISTINCT node, relation_type, MIN(depth) AS depth,
                        MAX(weight) AS weight
        FROM graph_walk
        WHERE LOWER(node) != ?
        GROUP BY node, relation_type
        ORDER BY depth, weight DESC
        LIMIT 50
        """
        rows = self.conn.execute(
            query, (entity_lower, entity_lower, entity_lower, min_weight,
                    max_depth, min_weight, entity_lower, entity_lower)
        ).fetchall()

        return [
            {
                "entity": r["node"],
                "relation_type": r["relation_type"],
                "depth": r["depth"],
                "weight": r["weight"],
            }
            for r in rows
        ]

    def find_path(self, source: str, target: str,
                  max_depth: int = 4) -> Optional[List[str]]:
        """最短路径查询。优先邻接表，回退 BFS。

        Returns: [source, ..., target] or None
        """
        sl, tl = source.lower(), target.lower()
        if sl == tl:
            return [sl]

        # Try adjacency cache first
        try:
            row = self.conn.execute("""
                SELECT hop_path FROM adjacency_cache
                WHERE entity_name = ? AND neighbor_name = ? AND depth <= ?
                ORDER BY depth LIMIT 1
            """, (sl, tl, max_depth)).fetchone()
            if row and row["hop_path"]:
                return json.loads(row["hop_path"])
        except Exception:
            pass

        # BFS fallback
        from collections import deque
        visited = {sl}
        queue = deque([(sl, [sl])])
        while queue:
            current, path = queue.popleft()
            if len(path) > max_depth:
                break
            neighbors = self.traverse(current, max_depth=1, min_weight=0.1)
            for n in neighbors:
                nl = n["entity"].lower()
                if nl == tl:
                    return path + [nl]
                if nl not in visited:
                    visited.add(nl)
                    queue.append((nl, path + [nl]))
        return None

    def find_related_by_type(self, entity: str, target_type: str,
                             depth: int = 2) -> List[Dict[str, Any]]:
        """查找指定类型的相关实体（基于实体名中的关键词匹配）。"""
        el = entity.lower()
        try:
            rows = self.conn.execute("""
                SELECT ac.neighbor_name, ac.relation_type, ac.depth, ac.weight
                FROM adjacency_cache ac
                WHERE ac.entity_name = ? AND ac.depth <= ?
                  AND LOWER(ac.neighbor_name) LIKE ?
                ORDER BY ac.depth, ac.weight DESC
            """, (el, depth, f"%{target_type.lower()}%")).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            pass
        # Fallback: filter traverse results
        results = self.traverse(entity, max_depth=depth, min_weight=0.1)
        return [r for r in results if target_type.lower() in r["entity"].lower()]

    def centrality_approx(self, entity: str) -> float:
        """近似中心度: 直接邻居数 / 总实体数。"""
        el = entity.lower()
        try:
            neighbor_count = self.conn.execute(
                "SELECT COUNT(DISTINCT neighbor_name) FROM adjacency_cache "
                "WHERE entity_name = ? AND depth = 1",
                (el,)
            ).fetchone()[0]
            total = self.conn.execute(
                "SELECT COUNT(DISTINCT entity_name) FROM adjacency_cache"
            ).fetchone()[0]
            return neighbor_count / max(total, 1)
        except Exception:
            return 0.0

    def cluster_by_relation(self, relation: str,
                            min_size: int = 3) -> List[Dict[str, Any]]:
        """按关系类型聚类。返回每个源实体及其连接的目标。"""
        return [
            dict(r) for r in self.conn.execute("""
                SELECT entity_a AS source,
                       GROUP_CONCAT(entity_b) AS connected,
                       COUNT(entity_b) AS size
                FROM entity_relations
                WHERE relation_type = ?
                  AND (last_seen IS NULL OR last_seen > datetime('now', '-90 days'))
                GROUP BY entity_a
                HAVING COUNT(entity_b) >= ?
                ORDER BY COUNT(entity_b) DESC
            """, (relation, min_size)).fetchall()
        ]

    def search_by_graph(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """基于图的检索：提取查询实体，图遍历找到关联条目。

        返回 [{entry_id, content, score, path}, ...]
        """
        query_entities = extract_entities(query)
        if not query_entities:
            return []

        # 对每个查询实体做图遍历
        seen_entries: Dict[int, Dict] = {}
        for qe in query_entities[:3]:  # 最多取前3个实体
            # 归一化查询实体
            normalized_qe = _normalize_entity(qe)
            if len(normalized_qe) < 2:
                continue
            related = self.traverse(normalized_qe, max_depth=2, min_weight=0.3)
            for r in related:
                # 找提到该关联实体的条目
                ent = r["entity"]
                # 也尝试用归一化名称搜索
                search_terms = [ent, _normalize_entity(ent)]
                rows = []
                for term in set(search_terms):
                    if len(term) < 2:
                        continue
                    term_rows = self.conn.execute(
                        "SELECT id, content, domain_scores, layer, active_summary "
                        "FROM unified_knowledge WHERE status = 'active' "
                        "AND LOWER(content) LIKE ? "
                        "ORDER BY positive_feedback DESC LIMIT 3",
                        (f"%{term.lower()}%",)
                    ).fetchall()
                    rows.extend(term_rows)

                for row in rows:
                    eid = row["id"]
                    score = r["weight"] / (r["depth"] or 1)
                    if eid in seen_entries:
                        seen_entries[eid]["score"] = max(
                            seen_entries[eid]["score"], score
                        )
                        seen_entries[eid]["paths"].append(
                            f"{qe}→{r['entity']}"
                        )
                    else:
                        try:
                            ds = json.loads(row["domain_scores"])
                        except Exception:
                            ds = {}
                        seen_entries[eid] = {
                            "entry_id": eid,
                            "content": row["content"],
                            "domain_scores": ds,
                            "layer": row["layer"],
                            "summary": row["active_summary"],
                            "score": score,
                            "paths": [f"{qe}→{r['entity']}"],
                        }

        results = sorted(seen_entries.values(), key=lambda x: -x["score"])
        return results[:limit]
