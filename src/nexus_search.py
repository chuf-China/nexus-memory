"""nexus_search.py — 检索增强管线 v2

包装 nexus_core.search()，加入:
  1. Query expansion: 实体提取 + 同义扩展
  2. Multi-hop: 时间推理 + 实体关联的两步检索
  3. Negation-aware: 否定句特殊搜索策略
  4. Context builder: 排序 + 去重 + 截断 + 时间注入 + 引用标注

用法:
  from agent.nexus_search import EnhancedSearch
  es = EnhancedSearch(nc)
  results = es.search("When did Caroline go to the LGBTQ support group?", ...)
  context = es.build_context(results, max_tokens=2000, question=question)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 正则 ──────────────────────────────────────────────────

_RELATIVE_TIME = re.compile(r"\b(yesterday|today|tomorrow|last\s+\w+|next\s+\w+|this\s+\w+|ago)\b", re.IGNORECASE)
_ENTITY = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b")
_DATE = re.compile(r"\b(\d{1,2}\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b", re.IGNORECASE)
_NEGATION_WORDS = re.compile(r"\b(didn't|never|not|without|no\s+longer|wasn't|haven't|hadn't|refused|avoided)\b", re.IGNORECASE)

_SYNONYMS = {
    "what": "what which",
    "when": "when date time",
    "where": "where location place",
    "who": "who person",
    "how long": "how long duration years months",
    "how many": "how many number count",
    "why": "why reason because",
    "relationship": "relationship dating married single",
    "work": "work job career profession",
    "school": "school education college university class",
    "support group": "support group meeting therapy",
    "go to": "go to went to attended visited",
    "planning": "planning planning going to will",
    "identity": "identity identify transgender LGBTQ",
}

# ── Query Expansion ───────────────────────────────────────

def expand_query(query: str) -> List[str]:
    """生成多条搜索查询"""
    queries = [query]
    # 同义替换
    for word, synonyms in _SYNONYMS.items():
        if word in query.lower():
            for syn in synonyms.split():
                alt = re.sub(rf"\b{word}\b", syn, query, flags=re.IGNORECASE)
                if alt != query and alt not in queries:
                    queries.append(alt)
            break
    # 实体关键词
    entities = _ENTITY.findall(query)
    if entities:
        eq = " ".join(entities)
        if eq not in queries:
            queries.append(eq)
    # 关键词
    from tests.eval_locomo import extract_keywords
    keywords = extract_keywords(query, max_kw=8)
    if keywords:
        kw = " ".join(keywords)
        if kw not in queries:
            queries.append(kw)
    return queries[:4]


# ── Multi-hop ─────────────────────────────────────────────

def needs_relative_time(results: List[Dict]) -> Tuple[bool, str]:
    """检查结果中是否有时间相对词"""
    for r in results[:10]:
        content = r.get("content", "") or ""
        m = _RELATIVE_TIME.search(content)
        if m:
            return True, m.group(1)
    return False, ""


def extract_entities(text: str) -> List[str]:
    """从问题中提取人名/地名/专名"""
    return _ENTITY.findall(text)


def is_negation_query(query: str) -> bool:
    """检测是否是否定句问题"""
    return bool(_NEGATION_WORDS.search(query))


# ── Context Builder v2 ────────────────────────────────────

_MONTH_MAP = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}


def _resolve_relative_time(content: str, session_date: str) -> str:
    """将时间相对词解析为绝对日期"""
    m = _RELATIVE_TIME.search(content)
    if not m or not session_date:
        return content

    rel_word = m.group(1).lower()
    try:
        # 解析 session_date 格式: "1:56 pm on 8 May, 2023"
        date_match = _DATE.search(session_date)
        if not date_match:
            return content
        date_str = date_match.group(1)
        import datetime as dt
        # Parse "8 May 2023" -> datetime
        parts = date_str.split()
        day = int(parts[0])
        month = _MONTH_MAP.get(parts[1], 1)
        year = int(parts[2])
        base = dt.date(year, month, day)
        resolved = base
        if rel_word == "yesterday":
            resolved = base - dt.timedelta(days=1)
        elif rel_word == "today":
            resolved = base
        elif rel_word == "tomorrow":
            resolved = base + dt.timedelta(days=1)
        elif rel_word.startswith("last"):
            resolved = base - dt.timedelta(days=7)
        elif rel_word.startswith("next"):
            resolved = base + dt.timedelta(days=7)
        else:
            return content
        # 替换相对词为绝对日期
        resolved_str = resolved.strftime("%d %B %Y")
        content = content.replace(rel_word, resolved_str, 1)
        content += f" [context: session date was {date_str}, so '{rel_word}' = {resolved_str}]"
    except Exception:
        pass
    return content


def build_context(results: List[Dict],
                  max_tokens: int = 2000,
                  question: str = "",
                  session_info: str = "") -> str:
    """构建 LLM 可读的上下文 v2。

    改进:
      1. 时间相对词 → 解析为绝对日期
      2. 否定句 → 标注为 "否定查询"，改变优先级
      3. 按分数降序 + 去重
      4. 标记引用来源编号
    """
    if not results:
        return "[No relevant context found.]"

    # 去重
    seen = set()
    unique = []
    for r in results:
        content = r.get("content", "") or ""
        prefix = content[:60]
        if prefix not in seen:
            seen.add(prefix)
            unique.append(r)

    # 排序
    unique.sort(key=lambda x: -(x.get("rerank_score") or x.get("similarity") or 0))

    # 时间注入
    if session_info:
        for r in unique:
            content = r.get("content", "") or ""
            resolved = _resolve_relative_time(content, session_info)
            if resolved != content:
                r["_resolved_content"] = resolved

    is_neg = is_negation_query(question)
    neg_note = ""
    if is_neg:
        neg_note = ("[NOTE: This is a negative question — the answer should describe "
                    "something that did NOT happen or was NOT done.]\n")

    parts = []
    char_count = 0
    for i, r in enumerate(unique):
        content = r.get("_resolved_content") or r.get("content", "") or ""
        est_tokens = len(content) // 4
        if char_count + est_tokens > max_tokens:
            remaining = max_tokens * 4 - char_count
            if remaining > 40:
                content = content[:remaining] + "..."
            else:
                break
        parts.append(f"[{i+1}] {content}")
        char_count += est_tokens

    ctx = "\n".join(parts)
    if neg_note:
        ctx = neg_note + ctx
    return ctx


# ── 主类 ──────────────────────────────────────────────────

class EnhancedSearch:
    """增强检索: query expansion + multi-hop + negation-aware + context builder v2"""

    def __init__(self, nexus_core):
        self.nc = nexus_core

    def search(self, query: str, user_id: str = "default",
               limit: int = 5, mode: str = "hybrid") -> List[Dict[str, Any]]:
        """增强搜索"""
        expanded = expand_query(query)
        # 主搜索 (query expansion 合并)
        seen_results = {}  # id → result with best score
        for eq in expanded:
            results = self.nc.search(eq, user_id=user_id, limit=limit * 2, mode=mode)
            for r in results:
                rid = r.get("id") or r.get("entry_id")
                if not rid:
                    continue
                score = r.get("rerank_score") or r.get("similarity") or r.get("score") or 0
                if rid not in seen_results or score > seen_results[rid].get("rerank_score", 0):
                    seen_results[rid] = r
                    r["rerank_score"] = score

        all_results = list(seen_results.values())

        # Multi-hop: 时间相对词 → 搜实体名 + 日期
        has_relative, rel_word = needs_relative_time(all_results)
        if has_relative:
            entities = extract_entities(query)
            for ent in entities[:2]:
                hop2 = self.nc.search(f"{ent} date time", user_id=user_id,
                                      limit=5, mode=mode)
                for r in hop2:
                    rid = r.get("id") or r.get("entry_id")
                    if rid and rid not in seen_results:
                        seen_results[rid] = r
                        r["_source"] = "multi_hop"
                        r["rerank_score"] = r.get("similarity") or r.get("score") or 0.3
                        all_results.append(r)

        # 否定句: 加搜被否定的事件的正面描述
        if is_negation_query(query):
            neg_terms = _NEGATION_WORDS.sub("", query).strip()
            if neg_terms:
                hop_neg = self.nc.search(neg_terms, user_id=user_id,
                                         limit=5, mode=mode)
                for r in hop_neg:
                    rid = r.get("id") or r.get("entry_id")
                    if rid and rid not in seen_results:
                        seen_results[rid] = r
                        r["_source"] = "negation_hop"
                        r["rerank_score"] = r.get("similarity") or r.get("score") or 0.3
                        all_results.append(r)

        # 排序: 最后用原 query 的 cross-encoder 重排（如果可用）
        all_results.sort(key=lambda x: -x.get("rerank_score", 0))

        # 如果有 cross-encoder，用原 query 做最终重排
        try:
            from agent.nexus_embedder import Reranker
            reranker = Reranker()
            reranker._load_cross_encoder()
            if reranker._model and reranker._model != "score_only":
                all_results = reranker.rerank(query, all_results, top_k=limit * 3)
        except Exception:
            pass

        return all_results[:limit * 3]

    def build_context(self, results: List[Dict],
                      max_tokens: int = 2000,
                      question: str = "",
                      session_dates: Optional[List[str]] = None) -> str:
        return build_context_v2(results, max_tokens=max_tokens,
                                question=question, session_dates=session_dates)


def _resolve_relative_time_hard(content: str, session_date: str) -> str:
    """规则引擎: 将相对时间词替换为计算出的绝对日期。
    不依赖 LLM 推理，直接用日期算术。

    返回替换后的文本，如果没有相对时间词则返回原文。
    """
    m = _RELATIVE_TIME.search(content)
    if not m or not session_date:
        return content

    rel_word = m.group(1).lower()

    # 解析 session_date: "1:56 pm on 8 May, 2023"
    parts = session_date.split()
    try:
        idx = next(i for i, p in enumerate(parts) if p in _MONTH_MAP)
        day = int(parts[idx - 1].strip(','))
        month = _MONTH_MAP[parts[idx]]
        year = int(parts[idx + 1].strip(','))
    except (StopIteration, ValueError, IndexError):
        return content

    import datetime as dt
    base = dt.date(year, month, day)

    if rel_word == "yesterday":
        resolved = base - dt.timedelta(days=1)
    elif rel_word == "today":
        resolved = base
    elif rel_word == "tomorrow":
        resolved = base + dt.timedelta(days=1)
    elif rel_word.startswith("last") and "week" in rel_word:
        resolved = base - dt.timedelta(days=7)
    elif rel_word.startswith("next") and "week" in rel_word:
        resolved = base + dt.timedelta(days=7)
    elif rel_word.startswith("last") and "month" in rel_word:
        resolved = dt.date(year if month > 1 else year - 1, month - 1 if month > 1 else 12, day)
    elif rel_word.startswith("next") and "month" in rel_word:
        resolved = dt.date(year if month < 12 else year + 1, month + 1 if month < 12 else 1, day)
    elif rel_word.startswith("last") and ("summer" in rel_word or "winter" in rel_word or "spring" in rel_word or "fall" in rel_word or "autumn" in rel_word):
        # "last summer" → approximate: 3 months ago
        resolved = dt.date(year, max(1, month - 3), day)
    elif rel_word.startswith("next"):
        resolved = base + dt.timedelta(days=7)
    else:
        return content

    resolved_str = resolved.strftime("%-d %B %Y")
    # 替换相对词为绝对日期
    content = re.sub(rf"\b{rel_word}\b", resolved_str, content, count=1, flags=re.IGNORECASE)
    return content


def build_context_v2(results: List[Dict],
                      max_tokens: int = 2000,
                      question: str = "",
                      session_dates: Optional[List[str]] = None) -> str:
    """构建 LLM 可读的上下文。

    核心改进:
      1. 硬时间推理 — 规则引擎解析相对时间词，不依赖 LLM
      2. 多 session 日期注入 — 所有会话日期开头展示
      3. 否定句标注
    """
    if not results:
        return "[No relevant context found.]"

    # ── 时间注入: 用规则引擎解析相对时间词 ──────
    if session_dates:
        for r in results:
            content = r.get("content", "") or ""
            for sd in session_dates:
                resolved = _resolve_relative_time_hard(content, sd)
                if resolved != content:
                    r["_resolved_content"] = resolved
                    break

    # ── session 日期头 ───────────────────────
    header = ""
    if session_dates:
        dates_lines = [f"  Session {i+1}: {sd}" for i, sd in enumerate(session_dates)]
        header = "Session dates:\n" + "\n".join(dates_lines) + "\n\n"

    # ── 去重排序 ────────────────────────────
    seen = set()
    unique = []
    for r in results:
        content = r.get("_resolved_content") or r.get("content", "") or ""
        prefix = content[:60]
        if prefix not in seen:
            seen.add(prefix)
            unique.append(r)

    unique.sort(key=lambda x: -(x.get("rerank_score") or x.get("similarity") or 0))

    # ── 否定句标注 ─────────────────────────
    is_neg = is_negation_query(question)
    if is_neg:
        header += "[NOTE: This question asks about something that did NOT happen]\n"

    # ── 拼接 ───────────────────────────────
    parts = []
    char_count = 0
    for r in unique:
        content = r.get("_resolved_content") or r.get("content", "") or ""
        est = len(content) // 4
        if char_count + est > max_tokens:
            remaining = max_tokens * 4 - char_count
            if remaining > 40:
                content = content[:remaining] + "..."
            else:
                break
        parts.append(f"[{len(parts)+1}] {content}")
        char_count += est

    body = "\n".join(parts)
    return header + body

