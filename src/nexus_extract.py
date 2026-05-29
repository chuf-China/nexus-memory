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

_LLM_EXTRACT_PROMPT = """从以下用户消息中提取可以记住的用户信息。

只提取符合以下条件的:
- **A级（直接陈述）**: 用户明确说出的身份、偏好、习惯、事实
  - "我是...", "我喜欢...", "我用...", "我需要..."
- **B级（强烈暗示）**: 用户没有直接说但上下文非常明确的
  - "每次开盘我都..." → 用户习惯
  - "从来不碰XX" → 明确的规则
- **C级（推理）**: 你根据上下文推理出的，标注 "(推测)"

不要提取:
- 一段时间后就会变的临时状态（"今天心情不好"）
- 明显的反话或夸张表达
- 你不太确定的内容

输出格式 JSON:
[
  {"content": "...", "domain": "identity|workflow|rule|strategy|raw_fact", "level": "A|B|C"},
  ...
]

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
            from .nexus_local import get_client as _get_llm
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
            from .nexus_local import get_client as _get_llm
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
