"""
Nexus utility functions — shared helpers for text processing and scoring.
"""

from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Text processing helpers
# ---------------------------------------------------------------------------

CONTENT_WHITESPACE = re.compile(r'\s+')
TAG_RE = re.compile(r'<[^>]+>')


def normalize(text: str) -> str:
    """Strip tags, collapse whitespace, lowercase — for match_hash."""
    cleaned = TAG_RE.sub('', text)
    return CONTENT_WHITESPACE.sub(' ', cleaned).strip().lower()


def content_hash(content: str) -> str:
    return hashlib.sha256(normalize(content).encode('utf-8')).hexdigest()[:16]


def segment_fts(text: str) -> str:
    """Segment text for FTS5 indexing using CJK bigrams.

    Non-CJK tokens (ASCII alphanumeric) pass through as-is.
    CJK characters are indexed as:
      - unigrams (single characters)
      - bigrams (overlapping 2-char sequences)

    No external dependencies (replaces jieba). Works with FTS5's
    unicode61 tokenizer which splits on whitespace.
    """
    if not text:
        return ''

    cjk_start = 0x4E00
    cjk_end = 0x9FFF
    result_parts = []
    cjk_buf = []
    ascii_buf = []

    def flush_ascii():
        if ascii_buf:
            result_parts.append(''.join(ascii_buf))
            ascii_buf.clear()

    def flush_cjk():
        if len(cjk_buf) == 0:
            return
        s = ''.join(cjk_buf)
        # Unigrams: each character as a token
        result_parts.append(' '.join(s))
        # Bigrams: overlapping 2-char sequences
        if len(s) >= 2:
            bigrams = [s[i:i+2] for i in range(len(s) - 1)]
            if bigrams:
                result_parts.append(' '.join(bigrams))
        cjk_buf.clear()

    for ch in text:
        cp = ord(ch)
        if cp >= cjk_start and cp <= cjk_end:
            flush_ascii()
            cjk_buf.append(ch)
        elif ch.isascii() and (ch.isalnum() or ch in '._-'):
            flush_cjk()
            ascii_buf.append(ch)
        else:
            flush_ascii()
            flush_cjk()
            # Punctuation/whitespace — skip
            pass

    flush_ascii()
    flush_cjk()
    return ' '.join(result_parts)


def fts_or_query(segmented: str) -> str:
    """Turn segmented text into an FTS5 MATCH expression with OR semantics.

    segment_fts() produces unigram+bigram tokens joined by spaces. Feeding
    that string straight into MATCH applies implicit AND — a 6-char Chinese
    query then requires every unigram AND every bigram to coexist in the
    document (e.g. '中国经济发展' needs the nonexistent bigram '国经').
    That makes FTS5 miss almost all queries ≥4-5 chars and silently fall
    back to the slow LIKE path. OR semantics keep recall; bm25 rank orders.

    Single CJK chars (unigrams) are dropped whenever the query also has
    multi-char tokens: '发' matches every document containing '发达',
    '发展' etc. — pure recall noise with bigrams present. They stay for
    genuinely single-char queries ('中') and for non-CJK tokens.
    """
    tokens = [t for t in segmented.split() if t]
    if not tokens:
        return ''
    if any(len(t) >= 2 for t in tokens):
        tokens = [t for t in tokens if len(t) >= 2 or not _is_cjk_char(t)]
        if not tokens:
            return ''
    if len(tokens) == 1:
        return tokens[0]
    return '(' + ' OR '.join(tokens) + ')'


def _is_cjk_char(ch: str) -> bool:
    cp = ord(ch)
    return 0x4E00 <= cp <= 0x9FFF


# 连续 CJK 片段（中文无空格，整段作为一个子串模式）
_CJK_RUN_RE = re.compile(r'[一-鿿]+')


def like_fragments(query: str) -> List[str]:
    """Extract substring fragments for LIKE '%...%' fallback matching.

    Unlike segment_fts tokens (which contain bigrams that never appear
    verbatim in raw text, and unigrams like '中' that match every Chinese
    document), these fragments are actual substrings of the query:
    continuous CJK runs stay whole, ASCII tokens split on whitespace.
    """
    parts = []
    for tok in query.split():
        cjk_runs = _CJK_RUN_RE.findall(tok)
        if cjk_runs:
            parts.extend(cjk_runs)
        else:
            parts.append(tok)
    return [p for p in parts if p]


# ---------------------------------------------------------------------------
# Domain scoring helpers
# ---------------------------------------------------------------------------

DOMAINS = ('identity', 'workflow', 'behavior', 'strategy', 'rule', 'raw_fact')


def empty_scores() -> Dict[str, int]:
    return {d: 0 for d in DOMAINS}


def generate_summary(content: str, max_len: int = 200) -> str:
    """Generate a concise summary string for active_summary.

    Strategy (in order):
    1. Use the first sentence before any '§' delimiter (up to max_len)
    2. Fallback: first line (up to max_len)
    3. Last resort: truncated content
    """
    if not content:
        return ""

    # Try splitting by § delimiter (Hermes memory file separator)
    if '§' in content:
        first = content.split('§', 1)[0].strip()
        if first and len(first) <= max_len:
            return first
        if first:
            return first[:max_len - 3] + "..."

    # Try first line
    first_line = content.split('\n', 1)[0].strip()
    if first_line and len(first_line) <= max_len:
        return first_line
    if first_line:
        return first_line[:max_len - 3] + "..."

    return content[:max_len]


def incr_score(scores: Dict[str, int], domain: str) -> Dict[str, int]:
    s = dict(scores)
    s[domain] = s.get(domain, 0) + 1
    return s


def max_domain(scores: Dict[str, int]) -> Tuple[Optional[str], int]:
    best = None
    best_val = 0
    for k, v in scores.items():
        if v > best_val:
            best_val = v
            best = k
    return best, best_val
