"""nexus_extract.py — 自动知识提取引擎

双通道提取: 正则通道 (同步) + LLM 通道 (异步, 可选回退)

正则通道: 显式信号 "我喜欢X" "我不能Y" "应该做Z" — 零延迟
LLM 通道: 隐式信号 "Django用着太痛苦" → 推理用户偏好 — 低置信度

LLM 提取的知识初始 confidence=0.3 (observation 层最低档),
走 Belief 引擎的验证环: 只有后续对话自然确认后才晋升。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 正则提取规则 ────────────────────────────────────────────

_IDENTITY_PATTERNS = [
    (re.compile(r'(?:我|我们|本人)(?:喜欢|偏好|倾向于|习惯|常用|使用|用|做|想要|需要|关注|看好|做)\s*(.{4,60})'), "identity"),
    (re.compile(r'(?:我是|我是一个|我是位|我是个)\s*(.{4,60})'), "identity"),
    (re.compile(r'我(?:住在|工作在|毕业于|来自)\s*(.{4,60})'), "identity"),
]

_RULE_PATTERNS = [
    (re.compile(r'(?:应该|必须|一定|得|要)\s*(.{4,80})'), "rule"),
    (re.compile(r'(?:不能|不可以|不要|禁止|避免|别)\s*(.{4,80})'), "rule"),
    (re.compile(r'(?:每次|总是|永远|从不)\s*(.{4,60})'), "rule"),
]

_WORKFLOW_PATTERNS = [
    (re.compile(r'(?:先用|再|然后|接着|最后)(?:\s*用|使用|调|执行)?\s*(.{4,60})'), "workflow"),
    (re.compile(r'(?:用|使用|通过|借助)\s*(.{4,40})(?:来|做|实现|完成|分析|处理)'), "workflow"),
]

_ASSET_PATTERNS = [
    (re.compile(r'(?:成本|成本价|买入|建仓)(?:价|价格|成本)?[是为：:\\s]*(\\d+\\.?\\d*)'), "raw_fact"),
    (re.compile(r'(?:目前|当前|现|现在)(?:持仓|持有|仓位|持股)\\s*(\\d+)'), "raw_fact"),
    (re.compile(r'(?:止损|止盈|目标价)[是为：:\\s]*(\\d+\\.?\\d*)'), "strategy"),
]

_CORRECTION_PATTERNS = [
    re.compile(r'(?:不对|错了|不是|不对的|搞错了|说错了|错了错了)'),
    re.compile(r'(?:其实|实际上|真实情况是|正确是|应该说)'),
    re.compile(r'不(?:是|该|能)\\s*.{2,10}(?:而是|应该)'),
]

_QUANT_PATTERNS = [
    (re.compile(r'(?:RSI|MACD|KDJ|MA[0-9]+|BOLL|量比|换手).{2,30}'), "strategy"),
    (re.compile(r'(?:主力|北向|资金|净流入|净流出).{2,30}'), "strategy"),
]

# ── LLM 提取 ───────────────────────────────────────────────

_LLM_EXTRACT_PROMPT = """从以下用户消息中提取实体和事实。

## 实体提取规则
提取明确提到的实体，分类为:
- Person (人名)
- Organization (组织/公司/团队)
- Technology (工具/框架/语言/服务)
- Project (项目/产品)
- Location (地点/路径)
- Concept (具体领域概念，非泛泛概念)
- Document (具体文档)
- Event (具体事件)

不要提取:
- 代词 (我/你/他/她/它/他们/我们)
- 抽象概念 (快乐/成长/平衡/动力)
- 泛泛名词 (东西/事情/工作/生活/时间/人)
- 形容词短语 (很好的/不同的)
- 句子片段
- 同一实体重复提取

## 事实提取规则
提取 subject-predicate-object 三元组:
- predicate 用英文 SCREAMING_SNAKE_CASE (如 USES, DEPENDS_ON, VERSION_IS)
- 只提取明确陈述的事实，不提取观点和疑问
- 如果有时间信息，标注 valid_at / invalid_at (ISO 8601)

输出格式 JSON:
{
  "entities": [{"name": "...", "type": 1, "summary": "..."}],
  "facts": [
    {"source_entity": "...", "target_entity": "...", "relation_type": "...", "fact": "...", "valid_at": null, "invalid_at": null, "confidence": 0.0}
  ]
}

用户消息:
"""


def _is_correction(text: str) -> bool:
    return any(p.search(text) for p in _CORRECTION_PATTERNS)


# ── 正则提取 ────────────────────────────────────────────────

def extract_knowledge(user_message: str) -> List[Dict[str, str]]:
    """正则通道: 从用户消息中提取知识。零延迟。"""
    if not user_message or len(user_message) < 6:
        return []

    extracted = []
    seen = set()

    def _add(content: str, domain: str):
        if not content or len(content) < 4:
            return
        content = content.strip().strip('，。,．')
        key = content.lower()[:40]
        if key not in seen:
            seen.add(key)
            extracted.append({"content": content, "domain": domain, "source": "regex", "level": "A"})

    for pattern, domain in _IDENTITY_PATTERNS:
        for m in pattern.finditer(user_message):
            _add(m.group(1).strip(), domain)
    for pattern, domain in _RULE_PATTERNS:
        for m in pattern.finditer(user_message):
            _add(m.group(1).strip(), domain)
    for pattern, domain in _WORKFLOW_PATTERNS:
        for m in pattern.finditer(user_message):
            _add(m.group(1).strip(), domain)
    for pattern, domain in _ASSET_PATTERNS:
        for m in pattern.finditer(user_message):
            _add(m.group(0).strip(), domain)
    for pattern, domain in _QUANT_PATTERNS:
        for m in pattern.finditer(user_message):
            _add(m.group(0).strip(), domain)

    return extracted


# ── LLM 提取 ────────────────────────────────────────────────

def llm_extract_knowledge(user_message: str,
                           llm_client=None) -> List[Dict[str, Any]]:
    """LLM 通道: 从用户消息中提取隐式知识。

    返回提取结果，每条包含:
      - content, domain, level (A/B/C), source="llm"

    调用方负责:
      - 无需额外校验，调用方自行处理
      - 系统会自动为 llm 提取结果设置较低置信度
    """
    if not user_message or len(user_message) < 10:
        return []

    if llm_client is None:
        try:
            from .local import get_client as _get_llm
            llm_client = _get_llm()
        except Exception:
            return []

    if llm_client is None:
        return []

    try:
        resp = llm_client.chat([
            {"role": "system", "content": _LLM_EXTRACT_PROMPT},
            {"role": "user", "content": user_message},
        ], max_tokens=1024)

        content = resp.get("response", "") or resp.get("message", {}).get("content", "")
        if not content:
            return []

        # Parse JSON from response
        # Find first [ and last ]
        start = content.find("[")
        end = content.rfind("]")
        if start == -1 or end == -1:
            return []

        parsed = json.loads(content[start:end + 1])
        if not isinstance(parsed, list):
            return []

        results = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            content_text = (item.get("content") or "").strip()
            if len(content_text) < 4:
                continue
            domain = item.get("domain", "raw_fact")
            if domain not in ("identity", "workflow", "rule", "strategy", "raw_fact"):
                domain = "raw_fact"
            level = item.get("level", "C")
            if level not in ("A", "B", "C"):
                level = "C"

            results.append({
                "content": content_text,
                "domain": domain,
                "level": level,
                "source": "llm",
            })

        return results

    except Exception as e:
        logger.debug("LLM extract failed: %s", e)
        return []


# ── 集成入口 ────────────────────────────────────────────────

def extract_on_turn(user_message: str, model_response: str,
                     use_llm: bool = True) -> Tuple[List[Dict], bool]:
    """对话 turn 完成后调用。

    返回 (提取的知识列表, 是否包含纠正信号)。

    提取策略:
      1. 正则通道始终运行 — 高精度低延迟
      2. LLM 通道可选 — 捕获隐式信息，低置信度
      3. 合并结果，LLM 提取的标记 source="llm"
    """
    # 正则通道 (同步)
    regex_knowledge = extract_knowledge(user_message)

    # LLM 通道 (异步，降级)
    llm_knowledge = []
    if use_llm:
        try:
            from .local import get_client as _get_llm
            client = _get_llm()
            if client:
                llm_knowledge = llm_extract_knowledge(user_message, client)
        except Exception:
            pass

    # 合并: 去重 (content 相同的不重复加)
    known_contents = {k["content"] for k in regex_knowledge}
    for lk in llm_knowledge:
        if lk["content"] not in known_contents:
            known_contents.add(lk["content"])
            regex_knowledge.append(lk)

    is_correction = _is_correction(user_message)

    if is_correction:
        regex_knowledge.append({
            "content": f"用户纠正了之前的回答: {user_message[:100]}",
            "domain": "raw_fact",
            "source": "regex",
            "level": "A",
        })

    return regex_knowledge, is_correction


# ── MemoryExtractor (interval-based with dedup) ───────────

_EXTRACT_INTERVAL = 3       # 每 N 轮提取一次
_EXTRACT_MAX_MEMORIES = 5   # 每次最多保存条数


class MemoryExtractor:
    """Turn-aware memory extractor.

    Usage:
        extractor = MemoryExtractor()
        # At end of each turn:
        new_memories = extractor.maybe_extract(user_msg, model_msg, conn, user_id)
    """

    def __init__(self, interval: int = _EXTRACT_INTERVAL):
        self.interval = interval
        self._turn_counter = 0
        self._last_extract_turn = 0

    def maybe_extract(self, user_message: str, model_response: str,
                      conn=None, user_id: str = "default") -> List[Dict]:
        """Extract memories every N turns, dedup against existing DB entries.

        Returns: list of saved memory dicts (empty if skipped or no new memories).
        """
        self._turn_counter += 1
        if self._turn_counter - self._last_extract_turn < self.interval:
            return []

        self._last_extract_turn = self._turn_counter

        # Run extraction
        memories, _ = extract_on_turn(user_message, model_response, use_llm=True)
        if not memories:
            return []

        # Dedup against existing DB
        if conn:
            memories = self._dedup_against_db(memories, conn, user_id)

        # Cap
        return memories[:_EXTRACT_MAX_MEMORIES]

    def _dedup_against_db(self, memories: List[Dict],
                          conn, user_id: str) -> List[Dict]:
        """Remove memories whose content is very similar to existing DB entries."""
        new_memories = []
        for mem in memories:
            content = mem.get("content", "")
            if not content or len(content) < 4:
                continue
            # FTS5 lookup for similar content
            try:
                from .utils import segment_fts
                seg = segment_fts(content)
                if seg:
                    existing = conn.execute(
                        "SELECT id FROM unified_knowledge "
                        "WHERE content MATCH ? AND status = 'active' "
                        "AND (user_id = ? OR user_id = 'default') LIMIT 1",
                        (seg, user_id)
                    ).fetchone()
                    if existing:
                        continue  # skip — too similar to existing
            except Exception:
                pass
            new_memories.append(mem)
        return new_memories


# ── Pronoun Resolution ───────────────────────────────────────

_PRONOUNS_CN = re.compile(r'(他|她|它|他们|她们|它们|其)')
_PRONOUNS_EN = re.compile(r'\b(he|she|it|they|him|her|them|his|its|their)\b', re.IGNORECASE)
_PRONOUNS_CN_SET = {"他", "她", "它", "他们", "她们", "它们", "其"}
# Common Chinese verbs/particles that indicate word boundary
_CN_VERBS = {"来", "去", "说", "是", "有", "在", "做", "为", "能", "会", "要", "给", "把", "被",
             "从", "到", "和", "与", "或", "但", "而", "了", "过", "着", "得", "地", "的",
             "写", "读", "看", "听", "吃", "喝", "走", "跑", "飞", "打", "买", "卖", "教",
             "学", "问", "答", "叫", "让", "请", "帮", "用", "住", "开", "关", "找", "想"}
_ENTITY_PATTERN = re.compile(r'[一-鿿]{2,4}|[A-Z][a-zA-Z]{2,30}')


def _extract_cn_entities(text: str) -> List[str]:
    """Extract Chinese entity names (2 chars, before verb/particle)."""
    entities = []
    segments = re.split(r'[。！？，；、\s]+', text)
    for seg in segments:
        for m in re.finditer(r'[一-鿿]{2,3}', seg):
            word = m.group()
            # Skip if starts with or is a pronoun
            if any(word.startswith(p) for p in _PRONOUNS_CN_SET):
                continue
            # Trim trailing verb/particle
            if word[-1] in _CN_VERBS:
                word = word[:-1]
            # Must be 2+ chars, first char not a verb/pronoun
            if (len(word) >= 2
                    and word[0] not in _CN_VERBS
                    and word not in _PRONOUNS_CN_SET):
                entities.append(word)
    return entities


def resolve_pronouns(text: str, context: str = "") -> str:
    """Replace pronouns with the most recently mentioned entity.

    Uses rule-based resolution: finds the last entity before each pronoun.
    If context is provided, uses it as additional reference.
    """
    full_text = f"{context} {text}" if context else text
    cn_entities = _extract_cn_entities(full_text)
    en_entities = _ENTITY_PATTERN.findall(full_text)
    en_entities = [e for e in en_entities if e.isascii()]

    result = text

    # Resolve Chinese pronouns (process in reverse to preserve positions)
    cn_matches = list(_PRONOUNS_CN.finditer(text))
    for m in reversed(cn_matches):
        pronoun = m.group(1)
        pos = m.start()
        preceding = full_text[:pos]
        preceding_cn = _extract_cn_entities(preceding)
        if preceding_cn:
            antecedent = preceding_cn[-1]
            result = result[:pos] + antecedent + result[pos+len(pronoun):]

    # Resolve English pronouns (process in reverse to preserve positions)
    en_matches = list(_PRONOUNS_EN.finditer(text))
    for m in reversed(en_matches):
        pronoun = m.group(1)
        pos = m.start()
        preceding = full_text[:pos]
        preceding_en = _ENTITY_PATTERN.findall(preceding)
        preceding_en = [e for e in preceding_en if e.isascii()
                        and e.lower() not in ("he", "she", "it", "they", "him", "her", "them")]
        if preceding_en:
            antecedent = preceding_en[-1]
            result = result[:pos] + antecedent + result[pos+len(pronoun):]

    return result
