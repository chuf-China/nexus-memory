"""nexus_evolve.py — 写时合并引擎（知识进化）

在 write() 之前调用，决定是把新知识合并到已有的条目，
还是创建新条目。规则驱动，零 LLM 依赖。

合并策略:
  exact_dup    → match_hash 完全匹配，不新建
  fuzzy_dup    → 文本相似度 > 0.85，更新置信度不新建
  complement   → 同 domain 不同方面，关联边+升级 merge_count
  contradict   → 同 domain 冲突，标记冲突不合并
  new          → 无合适目标，正常创建

用法:
  from .evolve import find_merge_target
  target = find_merge_target(content, user_id, conn)
  if target["action"] == "fuzzy_dup":
      # 更新现有条目
  elif target["action"] == "new":
      # 正常 write()
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 相似度计算 ─────────────────────────────────────────────

_NEGATION_WORDS = {
    "不", "不是", "不要", "不能", "没有", "不会", "不对",
    "not", "no", "don", "doesn", "isn", "aren", "won",
    "避免", "禁止", "拒绝", "反对", "否定",
}

_COMPLEMENT_MARKERS = {
    "另外", "还有", "此外", "同时", "另一方面",
    "also", "besides", "furthermore", "moreover",
    "不过", "但是", "然而", "虽然",
}


def _containment_ratio(a: str, b: str) -> float:
    """What fraction of a's tokens appear in b? (CJK-aware)."""
    a_tokens = set(_tokenize(a))
    b_tokens = set(_tokenize(b))
    if not a_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens)


def _jaccard_similarity(a: str, b: str) -> float:
    """Token overlap ratio — fast, no external deps."""
    a_tokens = set(_tokenize(a))
    b_tokens = set(_tokenize(b))
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)


def _tokenize(text: str) -> List[str]:
    """Split into lowercased alphanumeric tokens (CJK chars as unigrams)."""
    text = text.lower()
    tokens = []
    buf = []
    for ch in text:
        if ch.isalnum():
            buf.append(ch)
        else:
            if buf:
                word = "".join(buf)
                # Split CJK into unigrams
                if any("\u4e00" <= c <= "\u9fff" for c in word):
                    tokens.extend(word)
                else:
                    tokens.append(word)
                buf = []
    if buf:
        word = "".join(buf)
        if any("\u4e00" <= c <= "\u9fff" for c in word):
            tokens.extend(word)
        else:
            tokens.append(word)
    return tokens


def _has_negation(text: str) -> bool:
    """Check if text contains negation words."""
    tokens = set(_tokenize(text))
    return bool(tokens & _NEGATION_WORDS)


def _has_complement(text: str) -> bool:
    """Detect if text is introducing complementary info."""
    return any(m in text.lower() for m in _COMPLEMENT_MARKERS)


def _infer_domain(content: str) -> str:
    """Quick domain inference from content."""
    content_lower = content.lower()
    if any(w in content_lower for w in ("我", "偏好", "喜欢", "习惯", "用")):
        return "identity" if len(content) < 40 else "behavior"
    if any(w in content_lower for w in ("应该", "必须", "不能", "可以", "要")):
        return "rule" if len(content) < 60 else "workflow"
    if any(w in content_lower for w in ("股票", "仓位", "止损", "买入", "卖出", "策略")):
        return "strategy"
    return "raw_fact"


# ── Embedding helpers ──────────────────────────────────────

def _get_embedding(knowledge_id: int, conn: sqlite3.Connection) -> Optional[List[float]]:
    """Read stored embedding from knowledge_embeddings table."""
    import struct
    try:
        row = conn.execute(
            "SELECT embedding, embed_dim FROM knowledge_embeddings WHERE entry_id = ?",
            (knowledge_id,)
        ).fetchone()
        if row and row["embedding"]:
            dim = row["embed_dim"]
            blob = row["embedding"]
            if not dim:
                # Fallback: infer dim from blob length / 4
                dim = len(blob) // 4
            vec = list(struct.unpack(f"{dim}f", blob[:dim * 4]))
            return vec
    except Exception:
        pass
    return None


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors."""
    if not a or not b:
        return 0.0
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ── 查找合并目标 ───────────────────────────────────────────

def find_merge_target(content: str, user_id: str,
                      conn: sqlite3.Connection,
                      embedder=None) -> Dict[str, Any]:
    """Find best merge target for new content.

    Returns:
        {"action": "exact_dup"|"fuzzy_dup"|"complement"|"contradict"|"new",
         "target_id": int or None,
         "target_content": str or None,
         "similarity": float,
         "reason": str}
    """
    content = content.strip()
    if not content or len(content) < 8:
        return {"action": "new", "target_id": None,
                "target_content": None, "similarity": 0.0, "reason": "too_short"}

    # Step 1: exact match via FTS5 — fast path
    row = conn.execute(
        "SELECT id, content FROM unified_knowledge "
        "WHERE match_hash = ? AND status = 'active' AND user_id = ?",
        (_content_hash(content), user_id)
    ).fetchone()
    if row:
        return {"action": "exact_dup", "target_id": row["id"],
                "target_content": row["content"], "similarity": 1.0, "reason": "match_hash_dedup"}

    # Step 2: find candidates via keyword search (FTS5 or LIKE)
    candidates = []
    keywords = _extract_keywords(content)

    # Try FTS5 with keyword subset (not full seg — too strict for AND semantics)
    for kw in keywords[:3]:
        try:
            rows = conn.execute(
                """SELECT uk.id, uk.content, uk.domain_scores
                   FROM unified_knowledge uk
                   JOIN knowledge_fts kfts ON uk.id = kfts.rowid
                   WHERE kfts.content MATCH ?
                     AND uk.status = 'active'
                     AND (uk.user_id = ? OR uk.user_id = 'default')
                   ORDER BY rank
                   LIMIT 10""",
                (kw, user_id)
            ).fetchall()
            candidates.extend(rows)
        except Exception:
            pass
        if len(candidates) >= 5:
            break

    # If FTS5 returned nothing, fall back to LIKE for each keyword
    if not candidates:
        for kw in keywords[:3]:
            if len(kw) < 2:
                continue
            try:
                rows = conn.execute(
                    """SELECT id, content, domain_scores FROM unified_knowledge
                       WHERE content LIKE ? AND status = 'active'
                       AND (user_id = ? OR user_id = 'default')
                       LIMIT 10""",
                    (f"%{kw}%", user_id)
                ).fetchall()
                candidates.extend(rows)
            except Exception:
                pass
            if len(candidates) >= 5:
                break

    if not candidates:
        return {"action": "new", "target_id": None,
                "target_content": None, "similarity": 0.0, "reason": "no_candidates"}

    # Step 3: evaluate each candidate
    new_domain = _infer_domain(content)
    new_neg = _has_negation(content)
    new_comp = _has_complement(content)

    best = None
    best_action = "new"
    best_score = 0.0

    for row in candidates:
        old = row["content"]
        sim = _jaccard_similarity(content, old)

        try:
            ds = json.loads(row["domain_scores"]) if row["domain_scores"] != "{}" else {}
        except Exception:
            ds = {}
        old_domain = _infer_domain(old)

        # Same domain?
        same_domain = new_domain == old_domain

        # Containment: new content is a subset of existing or vice versa
        contain_a = _containment_ratio(content, old)
        contain_b = _containment_ratio(old, content)
        high_containment = contain_a > 0.9 or contain_b > 0.9

        # Check contradiction
        old_neg = _has_negation(old)
        if same_domain and sim > 0.4:
            old_major = " ".join(_tokenize(old))
            new_major = " ".join(_tokenize(content))

            # Contradiction: same topic, opposite polarity
            shared_tokens = set(_tokenize(old)) & set(_tokenize(new_major))
            topic_ok = len(shared_tokens) >= 3  # same topic
            if topic_ok and new_neg != old_neg and new_neg and sim > 0.3:
                if sim > best_score:
                    best_score = sim
                    best = row
                    best_action = "contradict"

        # Fuzzy dup: high similarity, same domain
        if same_domain and (sim > 0.60 or high_containment) and best_action != "contradict":
            if sim > best_score:
                best_score = sim
                best = row
                best_action = "fuzzy_dup"

        # Complement: moderate similarity, complementary marker
        if same_domain and 0.4 <= sim <= 0.85 and new_comp:
            if sim > best_score:
                best_score = sim
                best = row
                best_action = "complement"

        # Embedding complement: low Jaccard but semantic similarity via embedding
        # NOTE: same_domain check is deliberately omitted here — _infer_domain heuristic
        # is unreliable (returns different values based on content length alone).
        # Cosine similarity is the correct semantic gate.
        logger.debug("embedding complement check: best_action=%s sim=%.2f new_comp=%s embedder=%s avail=%s",
                     best_action, sim, new_comp,
                     type(embedder).__name__ if embedder else "None",
                     getattr(embedder, 'available', 'N/A') if embedder else "N/A")
        if (best_action == "new"
                and sim < 0.4 and new_comp
                and embedder is not None and embedder.available):
            logger.debug("embedding complement: ENTERED for [%d]", row["id"])
            try:
                # Get embedding for new content
                new_vec = embedder.embed(content)
                logger.debug("embedding complement: new_vec=%s, content=%.40s",
                             "loaded" if new_vec else "None", content)
                if new_vec:
                    # Get embedding for candidate from DB
                    stored_vec = _get_embedding(row["id"], conn)
                    if stored_vec:
                        cos_sim = _cosine_similarity(new_vec, stored_vec)
                        if cos_sim >= 0.50:
                            if cos_sim > best_score:
                                best_score = cos_sim
                                best = row
                                best_action = "complement"
                                logger.debug(
                                    "embedding complement: %d vs new (cos=%.2f)",
                                    row["id"], cos_sim)
            except Exception:
                pass

    if best is None:
        return {"action": "new", "target_id": None,
                "target_content": None, "similarity": 0.0, "reason": "no_good_match"}

    return {
        "action": best_action,
        "target_id": best["id"],
        "target_content": best["content"],
        "similarity": round(best_score, 3),
        "reason": f"{best_action}_sim{best_score:.2f}",
    }


def merge_content(old_content: str, new_content: str) -> str:
    """Merge two related pieces of knowledge into one coherent entry.

    Strategy:
      - If new content is mostly a subset of old → keep old
      - If old is subset of new → return new
      - If complementary → concatenate with § separator
    """
    old_tokens = set(_tokenize(old_content))
    new_tokens = set(_tokenize(new_content))

    if not old_tokens or not new_tokens:
        return new_content

    overlap = len(old_tokens & new_tokens) / max(len(old_tokens), len(new_tokens))

    # New is mostly redundant
    if overlap > 0.9:
        return old_content

    # Old is mostly redundant
    new_overlap_in_old = len(new_tokens & old_tokens) / len(new_tokens)
    if new_overlap_in_old > 0.85 and len(old_content) > len(new_content) * 1.5:
        return old_content

    # Complementary → concatenate
    if _has_complement(new_content) or overlap < 0.5:
        return f"{old_content} § {new_content}"

    # Default: merge with §
    return f"{old_content} § {new_content}"


# ── Helpers ─────────────────────────────────────────────────

def _content_hash(content: str) -> str:
    import hashlib
    text = content.strip().lower()
    text = re.sub(r'\s+', ' ', text)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def _extract_keywords(text: str, max_kw: int = 5) -> List[str]:
    tokens = _tokenize(text)
    stop = {"的", "了", "是", "在", "有", "和", "就", "不", "也", "都",
            "这", "那", "对", "被", "把", "the", "a", "an", "is", "are",
            "it", "this", "that", "to", "for", "with", "on", "at", "by"}
    # CJK characters are valid even at length 1
    keywords = []
    for t in tokens:
        is_cjk = len(t) == 1 and '\u4e00' <= t <= '\u9fff'
        if is_cjk and t.lower() not in stop:
            keywords.append(t)
        elif len(t) >= 2 and t.lower() not in stop:
            keywords.append(t)
    # Count frequency, take top N
    freq = Counter(keywords)
    # For CJK, prefer non-adjacent characters for coverage
    deduped = []
    seen = set()
    for k, _ in freq.most_common(max_kw * 3):
        if k not in seen:
            seen.add(k)
            deduped.append(k)
            if len(deduped) >= max_kw:
                break
    return deduped


# ── 集成入口 ───────────────────────────────────────────────

def evolve_on_write(content: str, user_id: str,
                    conn: sqlite3.Connection,
                    embedder=None) -> Dict[str, Any]:
    """Pre-write hook: check if new content should merge into existing.

    Called from write() before creating a new entry.

    Returns:
        {"action": "exact_dup", "target_id": ...} → skip write
        {"action": "fuzzy_dup", "target_id": ...} → update existing (merge)
        {"action": "complement", "target_id": ...} → update existing (append)
        {"action": "contradict", "target_id": ...} → mark conflict, create new
        {"action": "new"} → proceed with normal write
    """
    target = find_merge_target(content, user_id, conn, embedder)

    if target["action"] == "exact_dup":
        # Don't create duplicate
        return target

    if target["action"] == "fuzzy_dup":
        # Update existing: merge content + append to active_summary
        merged = merge_content(target["target_content"], content)
        now = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat()

        # Get existing summary
        old_summary = ""
        try:
            row = conn.execute(
                "SELECT active_summary FROM unified_knowledge WHERE id = ?",
                (target["target_id"],)
            ).fetchone()
            if row and row["active_summary"]:
                old_summary = row["active_summary"]
        except Exception:
            pass

        # Append new content to summary (preserve version chain)
        new_summary = f"{old_summary} § {content}" if old_summary else content
        if len(new_summary) > 500:
            new_summary = new_summary[-500:]  # keep recent

        conn.execute(
            "UPDATE unified_knowledge SET content = ?, active_summary = ?, "
            "  last_accessed = ?, updated_at = ? WHERE id = ?",
            (merged, new_summary, now, now, target["target_id"])
        )
        # Update FTS
        try:
            from .core import _segment_fts
            seg = _segment_fts(merged)
            conn.execute(
                "INSERT INTO knowledge_fts(knowledge_fts, rowid, content) "
                "VALUES ('delete', ?, '')",
                (target["target_id"],)
            )
            conn.execute(
                "INSERT INTO knowledge_fts(rowid, content) VALUES (?, ?)",
                (target["target_id"], seg)
            )
        except Exception:
            pass
        conn.commit()
        logger.info("nexus_evolve: fuzzy_merged %d with new content (summary appended)", target["target_id"])
        target["action"] = "update"  # distinguish from raw fuzzy_dup
        return target

    if target["action"] == "complement":
        # Append as complementary info
        merged = merge_content(target["target_content"], content)
        conn.execute(
            "UPDATE unified_knowledge SET content = ?, last_accessed = ? "
            "WHERE id = ?",
            (merged, __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc).isoformat(),
             target["target_id"])
        )
        # Update FTS
        try:
            from .core import _segment_fts
            seg = _segment_fts(merged)
            conn.execute(
                "INSERT INTO knowledge_fts(knowledge_fts, rowid, content) "
                "VALUES ('delete', ?, '')",
                (target["target_id"],)
            )
            conn.execute(
                "INSERT INTO knowledge_fts(rowid, content) VALUES (?, ?)",
                (target["target_id"], seg)
            )
        except Exception:
            pass
        conn.commit()
        logger.info("nexus_evolve: complemented %d with new content", target["target_id"])
        return target

    if target["action"] == "contradict":
        # Mark conflict — let consolidator handle it
        from .core import NexusCore
        conn.execute(
            "INSERT INTO knowledge_conflicts (knowledge_id_a, knowledge_id_b, "
            "conflict_type, description) "
            "VALUES (?, ?, 'contradiction', ?)",
            (target["target_id"], -1,
             f"冲突: '{content[:80]}' vs '{target['target_content'][:80]}'")
        )
        conn.commit()
        logger.warning("nexus_evolve: contradiction detected with %d", target["target_id"])
        return {"action": "new"}

    return {"action": "new"}


# ═══════════════════════════════════════════════════════════
# Dream Distiller (P2-7)
# ═══════════════════════════════════════════════════════════


class DreamDistiller:
    """4-stage memory distillation (runs during idle/night).

    Orient  → scan recent sessions for raw interactions
    Gather  → extract new facts, decisions, questions
    Consolidate → merge with existing memories, dedup
    Prune   → mark stale/conflicting memories
    """

    def __init__(self, conn=None):
        self.conn = conn

    def run(self) -> Dict[str, Any]:
        """Execute full distillation cycle. Returns summary stats."""
        if self.conn is None:
            from pathlib import Path
            db_path = Path.home() / ".hermes" / "data" / "nexus" / "nexus.db"
            if not db_path.exists():
                return {"status": "no_db"}
            self.conn = sqlite3.connect(str(db_path))
            self.conn.row_factory = sqlite3.Row
            should_close = True
        else:
            should_close = False

        try:
            stats = {"consolidated": 0, "pruned": 0, "new_facts": 0}

            # 1. Orient: scan recent interaction logs
            recent = self._scan_recent_sessions()
            if not recent:
                return {"status": "no_recent_sessions", **stats}

            # 2. Gather: extract facts from recent interactions
            facts = self._extract_facts(recent)
            stats["new_facts"] = len(facts)

            # 3. Consolidate: merge/dedup with existing
            for fact in facts:
                merged = self._try_consolidate(fact)
                if merged:
                    stats["consolidated"] += 1

            # 4. Prune: mark stale memories
            pruned = self._prune_stale()
            stats["pruned"] = pruned

            self.conn.commit()
            return {"status": "ok", **stats}

        except Exception as e:
            logger.debug("DreamDistiller: %s", e)
            return {"status": "error", "detail": str(e)}
        finally:
            if should_close:
                self.conn.close()

    def _scan_recent_sessions(self) -> List[Dict]:
        """Get interactions from the last 24 hours."""
        try:
            rows = self.conn.execute(
                "SELECT id, user_query, model_response, knowledge_used "
                "FROM interaction_log "
                "WHERE created_at > datetime('now', '-1 day') "
                "ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _extract_facts(self, sessions: List[Dict]) -> List[Dict]:
        """Extract memorable facts from session interactions."""
        facts = []
        for s in sessions:
            query = s.get("user_query", "") or ""
            response = s.get("model_response", "") or ""
            for text in [query, response]:
                if len(text) < 20:
                    continue
                if any(w in text for w in ["决定", "选择", "采用", "decided", "chose", "will use"]):
                    facts.append({"content": text[:200], "type": "decision", "source": "dream"})
                if any(w in text for w in ["喜欢", "偏好", "prefer", "like", "favorite"]):
                    facts.append({"content": text[:200], "type": "preference", "source": "dream"})
        return facts[:10]

    def _try_consolidate(self, fact: Dict) -> bool:
        """Try to merge fact with existing memory."""
        content = fact.get("content", "")
        if not content or len(content) < 10:
            return False
        try:
            from .utils import segment_fts
            seg = segment_fts(content)
            if seg:
                existing = self.conn.execute(
                    "SELECT id FROM unified_knowledge "
                    "WHERE content MATCH ? AND status = 'active' LIMIT 1",
                    (seg,)
                ).fetchone()
                if existing:
                    self.conn.execute(
                        "UPDATE unified_knowledge SET positive_feedback = positive_feedback + 1, "
                        "last_accessed = datetime('now') WHERE id = ?",
                        (existing["id"],)
                    )
                    return True
        except Exception:
            pass
        try:
            from .core import _content_hash
            mhash = _content_hash(content)
            self.conn.execute(
                "INSERT INTO unified_knowledge "
                "(content, domain_scores, layer, match_hash, status, user_id) "
                "VALUES (?, '{}', 'instant', ?, 'active', 'dream_distiller')",
                (content, mhash)
            )
            return True
        except Exception:
            return False

    def _prune_stale(self) -> int:
        """Archive memories not accessed in 90+ days with low feedback."""
        try:
            result = self.conn.execute(
                "UPDATE unified_knowledge SET status = 'archived' "
                "WHERE status = 'active' "
                "AND last_accessed < datetime('now', '-90 days') "
                "AND positive_feedback < 2 "
                "AND negative_feedback > positive_feedback"
            )
            return result.rowcount
        except Exception:
            return 0
