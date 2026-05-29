"""
Nexus utility functions — shared helpers for text processing and scoring.
"""

from __future__ import annotations

import hashlib
import re
from typing import Dict, Optional, Tuple

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
