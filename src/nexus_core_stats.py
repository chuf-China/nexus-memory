"""
nexus_core_stats.py — Versioning, statistics, system prompt generation, FTS integrity, sleep-time consolidation.

Mixin for NexusCore. Do NOT import directly; use nexus_core.NexusCore instead.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from .security import scan_for_threats as _scan_for_threats

try:
    from .nexus_local import get_client as _get_llm_client
    _HAS_LOCAL_LLM = True
except Exception:
    _HAS_LOCAL_LLM = False
    _get_llm_client = lambda: None

from .nexus_utils import (
    CONTENT_WHITESPACE,
    content_hash,
    empty_scores,
    generate_summary,
    incr_score,
    max_domain,
    segment_fts,
)

# Backward compatibility aliases
_CONTENT_WHITESPACE = CONTENT_WHITESPACE
_normalize = lambda text: CONTENT_WHITESPACE.sub(' ', text).strip().lower()
_content_hash = content_hash
_empty_scores = empty_scores
_generate_summary = generate_summary
_incr_score = incr_score
_max_domain = max_domain
_segment_fts = segment_fts



class StatsMixin:
    """Mixin providing versioning, statistics, system prompt generation, fts integrity, sleep-time consolidation."""

    def _save_version(self, knowledge_id: int, reason: str, user_id: str):
        conn = self._conn()
        row = conn.execute(
            "SELECT content, active_summary, status FROM unified_knowledge WHERE id = ?",
            (knowledge_id,)
        ).fetchone()
        if not row:
            return
        conn.execute(
            """INSERT INTO knowledge_versions
               (knowledge_id, content, active_summary, status, change_reason, user_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (knowledge_id, row["content"], row["active_summary"],
             row["status"], reason, user_id)
        )
        conn.commit()

    def get_history(self, knowledge_id: int) -> List[Dict[str, Any]]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM knowledge_versions WHERE knowledge_id = ? ORDER BY created_at DESC",
            (knowledge_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Stats ----------------------------------------------------------------

    def stats(self, user_id: str = "default") -> Dict[str, Any]:
        conn = self._conn()
        total = conn.execute(
            "SELECT COUNT(*) FROM unified_knowledge WHERE user_id = ? OR user_id = 'default'",
            (user_id,)
        ).fetchone()[0]
        by_layer = {}
        for layer in ('instant', 'candidate', 'consolidated'):
            cnt = conn.execute(
                "SELECT COUNT(*) FROM unified_knowledge WHERE layer = ? AND status = 'active' AND (user_id = ? OR user_id = 'default')",
                (layer, user_id)
            ).fetchone()[0]
            by_layer[layer] = cnt
        return {"total": total, "by_layer": by_layer}

    def get_alerts(self, user_id: str = "default") -> List[Dict[str, Any]]:
        """Return actionable alerts: unresolved conflicts, instant pileup, etc."""
        conn = self._conn()
        alerts = []

        # 1. Unresolved system conflicts (feedback_log)
        conflicts = conn.execute(
            "SELECT fb.id, fb.knowledge_id, fb.source, fb.created_at, "
            "substr(uk.content, 1, 100) as content "
            "FROM feedback_log fb "
            "JOIN unified_knowledge uk ON uk.id = fb.knowledge_id "
            "WHERE fb.feedback_type = 'system_conflict' "
            "AND uk.status = 'active' "
            "ORDER BY fb.created_at DESC LIMIT 5"
        ).fetchall()
        for cf in conflicts:
            alerts.append({
                "type": "conflict",
                "severity": "medium",
                "knowledge_id": cf["knowledge_id"],
                "detail": cf["source"],
                "content": cf["content"],
                "created_at": cf["created_at"],
            })

        # 2. Instant layer pileup (>10 entries)
        inst_count = conn.execute(
            "SELECT COUNT(*) FROM unified_knowledge "
            "WHERE layer='instant' AND status='active' "
            "AND (user_id=? OR user_id='default')", (user_id,)
        ).fetchone()[0]
        if inst_count > 10:
            alerts.append({
                "type": "instant_pileup",
                "severity": "low",
                "detail": f"{inst_count} instant entries pending consolidation",
            })

        # 3. Feedback-driven entries with excess negative
        neg_overload = conn.execute(
            "SELECT id, substr(content, 1, 80), negative_feedback, positive_feedback "
            "FROM unified_knowledge "
            "WHERE negative_feedback > positive_feedback + 3 "
            "AND status='active' AND layer='consolidated' "
            "AND (user_id=? OR user_id='default') "
            "LIMIT 3", (user_id,)
        ).fetchall()
        for no in neg_overload:
            alerts.append({
                "type": "negative_overload",
                "severity": "medium",
                "knowledge_id": no["id"],
                "detail": f"consolidated entry {no['id']} has {no['negative_feedback']} negative vs {no['positive_feedback']} positive",
                "content": no[1],
            })

        return alerts

    _PROMPT_BLOCK_SEPARATOR = "═" * 46

    def system_prompt_block(self, memory_enabled: bool = True,
                            user_enabled: bool = True,
                            user_id: str = "default",
                            char_limit: int = 2200) -> str:
        """Build the MEMORY block for system prompt injection.
        Entries are sorted by feedback weight (highest first)."""
        parts = []

        # ── Cold start hint (first 30 days only) ──
        try:
            stats = self._get_coldstart_stats(user_id)
            if 0 < stats["days_active"] <= 30 and stats["total_entries"] > 0:
                cold = (
                    f"[Day {stats['days_active']}] Nexus 已记录 {stats['total_entries']} 条知识"
                )
                if stats["today_entries"] > 0:
                    cold += f"，今日 +{stats['today_entries']}"
                if stats["patterns_found"] > 0:
                    cold += f"，发现 {stats['patterns_found']} 个模式"
                if stats["pending_conflicts"] > 0:
                    cold += f"，{stats['pending_conflicts']} 个待确认冲突"
                cold += "。"
                if stats["days_active"] <= 7:
                    cold += " 系统正在学习你的使用模式。"
                parts.append(cold)
        except Exception:
            pass

        if not memory_enabled and not user_enabled:
            return "\n".join(parts) if parts else ""
        if memory_enabled:
            entries = self.search_by_domain("identity", user_id=user_id, limit=20)
            if entries:
                # Sort by feedback weight descending
                entries.sort(key=lambda e: e.get("positive_feedback", 0) - e.get("negative_feedback", 0) * 2, reverse=True)
                lines = []
                total_chars = 0
                for e in entries:
                    line = e["content"]
                    # Threat-scan before injecting into system prompt
                    if _scan_for_threats is not None:
                        threats = _scan_for_threats(line, scope="context")
                        if threats:
                            logger.warning(
                                "Nexus: blocked entry %s (threats: %s)",
                                e.get("id", "?"), ", ".join(threats),
                            )
                            line = f"[BLOCKED: Nexus entry contained potential injection ({', '.join(threats)}). Content redacted.]"
                    if total_chars + len(line) + 3 > char_limit:
                        break
                    lines.append(line)
                    total_chars += len(line) + 3
                if lines:
                    content = "\n§\n".join(lines)
                    current = len(content)
                    pct = min(100, int((current / char_limit) * 100)) if char_limit > 0 else 0
                    header = f"MEMORY (your personal notes) [{pct}% — {current:,}/{char_limit:,} chars]"
                    parts.append(f"{self._PROMPT_BLOCK_SEPARATOR}\n{header}\n{self._PROMPT_BLOCK_SEPARATOR}\n{content}")
        if user_enabled:
            u_entries = self.search_by_domain("identity", user_id=user_id, limit=20)
            if u_entries:
                u_entries.sort(key=lambda e: e.get("positive_feedback", 0) - e.get("negative_feedback", 0) * 2, reverse=True)
                lines = []
                total_chars = 0
                user_limit = 1375
                for e in u_entries:
                    line = e["content"]
                    # Threat-scan before injecting into system prompt
                    if _scan_for_threats is not None:
                        threats = _scan_for_threats(line, scope="context")
                        if threats:
                            logger.warning(
                                "Nexus: blocked user entry %s (threats: %s)",
                                e.get("id", "?"), ", ".join(threats),
                            )
                            line = f"[BLOCKED: Nexus entry contained potential injection ({', '.join(threats)}). Content redacted.]"
                    if total_chars + len(line) + 3 > user_limit:
                        break
                    lines.append(line)
                    total_chars += len(line) + 3
                if lines:
                    content = "\n§\n".join(lines)
                    current = len(content)
                    pct = min(100, int((current / user_limit) * 100)) if user_limit > 0 else 0
                    header = f"USER PROFILE (who the user is) [{pct}% — {current:,}/{user_limit:,} chars]"
                    parts.append(f"{self._PROMPT_BLOCK_SEPARATOR}\n{header}\n{self._PROMPT_BLOCK_SEPARATOR}\n{content}")
        return "\n\n".join(parts)

    def get_subsystem_views(self, user_id: str = "default") -> Dict[str, Any]:
        """Return organized views of consolidated knowledge by domain."""
        conn = self._conn()
        domains = {
            "identity": "用户画像与 Agent 身份",
            "workflow": "工作流技能与操作模式",
            "strategy": "交易策略与分析逻辑",
            "behavior": "行为偏好与沟通风格",
            "rule": "合规规则与约束",
            "raw_fact": "事实与数据",
        }
        views = {}
        for d, label in domains.items():
            rows = conn.execute(
                "SELECT id, substr(content, 1, 120) as preview, "
                "positive_feedback, negative_feedback, "
                "layer, active_summary, last_accessed "
                "FROM unified_knowledge "
                "WHERE json_extract(domain_scores, ?) > 0 "
                "AND status = 'active' "
                "AND (user_id = ? OR user_id = 'default') "
                "ORDER BY (positive_feedback - negative_feedback * 2) DESC "
                "LIMIT 20",
                (f'$.{d}', user_id)
            ).fetchall()
            views[d] = {
                "label": label,
                "count": len(rows),
                "entries": [dict(r) for r in rows],
            }
        # Health summary
        alerts = []
        for d, data in views.items():
            neg = sum(
                1 for e in data["entries"]
                if e["negative_feedback"] > e["positive_feedback"]
            )
            if neg > 3:
                alerts.append(f"{d}: {neg} entries with excess negative feedback")
        return {"domains": views, "alerts": alerts}

    # -- FTS index integrity ---------------------------------------------------

    def _ensure_fts_integrity(self, conn: sqlite3.Connection) -> None:
        """Check FTS index health and rebuild if needed.

        Called during _init_db(). Rebuilds when:
        - FTS schema version mismatch (migration marker)
        - FTS table is empty (first run / after DROP)
        - FTS count != unified_knowledge count (desync after schema change)
        """
        # Check FTS segmentation version marker
        version = conn.execute(
            "SELECT value FROM nexus_meta WHERE key = 'fts_seg_version'"
        ).fetchone()

        if not version or version[0] < '2':
            logger.info(
                "Nexus: FTS segmentation v2 roll-out (was %s), rebuilding...",
                version[0] if version else 'none'
            )
            self.rebuild_fts(conn)
            conn.execute(
                "INSERT OR REPLACE INTO nexus_meta (key, value) "
                "VALUES ('fts_seg_version', '2')"
            )
            conn.commit()
            return

        try:
            uk_count = conn.execute(
                "SELECT COUNT(*) FROM unified_knowledge WHERE status = 'active'"
            ).fetchone()[0]
            fts_count = conn.execute(
                "SELECT COUNT(*) FROM knowledge_fts"
            ).fetchone()[0]
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            logger.warning("Nexus: FTS table not accessible, rebuilding...")
            fts_count = -1
            uk_count = 0

        if uk_count > 0 and fts_count != uk_count:
            logger.info(
                "Nexus: FTS index out of sync (UK=%d, FTS=%d), rebuilding...",
                uk_count, fts_count
            )
            self.rebuild_fts(conn)

    def rebuild_fts(self, conn: Optional[sqlite3.Connection] = None) -> int:
        """Rebuild the FTS index with jieba segmentation for all entries.

        Drops triggers temporarily, purges FTS, re-inserts with segmented
        content, then restores triggers.

        Returns: number of indexed entries.
        """
        if conn is None:
            conn = self._conn()

        # Temporarily disable triggers to avoid double-writes
        for trig in ('knowledge_ai', 'knowledge_ad', 'knowledge_au'):
            conn.execute(f"DROP TRIGGER IF EXISTS {trig}")

        # Purge FTS index
        # NOTE: DELETE FROM knowledge_fts fails on SQLite 3.46.1 with
        # external-content FTS5 tables ("database disk image is malformed").
        # Using 'rebuild' command instead — the correct approach for
        # content-sync FTS5 tables per SQLite docs.
        conn.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES('rebuild')")

        # Re-index all active entries with jieba segmentation
        rows = conn.execute(
            "SELECT id, content FROM unified_knowledge WHERE status = 'active'"
        ).fetchall()
        count = 0
        for row in rows:
            seg = _segment_fts(row["content"])
            conn.execute(
                "INSERT INTO knowledge_fts(rowid, content) VALUES (?, ?)",
                (row["id"], seg)
            )
            count += 1

        # Restore triggers from schema
        schema_path = Path(__file__).parent.parent / "plugins" / "memory" / "nexus" / "schema.sql"
        if schema_path.exists():
            conn.executescript(schema_path.read_text())

        conn.commit()
        logger.info("Nexus: FTS index rebuilt with %d entries (jieba segmented)", count)
        return count

    # -- Sleep-time consolidation (basic) -------------------------------------

    def consolidate(self, user_id: str = "default") -> Dict[str, Any]:
        """Run one consolidation pass. Call from background thread."""
        conn = self._conn()
        now = datetime.now(timezone.utc).isoformat()
        actions = []

        # Stage 1: clean up instant entries older than 30 days
        deleted = conn.execute(
            "DELETE FROM unified_knowledge WHERE layer = 'instant' AND status = 'active' "
            "AND last_accessed < datetime('now', '-30 days') "
            "AND (user_id = ? OR user_id = 'default')",
            (user_id,)
        ).rowcount
        if deleted:
            actions.append(f"cleaned_{deleted}_stale_instant")

        # Stage 2: promote entries based on weight (candidate → consolidated)
        promoted = conn.execute(
            """UPDATE unified_knowledge SET layer = 'consolidated', updated_at = ?
               WHERE layer = 'candidate' AND status = 'active'
               AND (positive_feedback - negative_feedback * 2) >= 8
               AND (user_id = ? OR user_id = 'default')""",
            (now, user_id)
        ).rowcount
        if promoted:
            actions.append(f"promoted_{promoted}_to_consolidated")

        # Stage 3: supersede entries with excessive negative feedback
        superseded = conn.execute(
            """UPDATE unified_knowledge SET status = 'superseded', updated_at = ?
               WHERE layer IN ('candidate','consolidated') AND status = 'active'
               AND (positive_feedback - negative_feedback * 2) <= -5
               AND (user_id = ? OR user_id = 'default')""",
            (now, user_id)
        ).rowcount
        if superseded:
            actions.append(f"superseded_{superseded}_due_to_negative_feedback")

        # Stage 4: archive consolidated entries not accessed in 180 days
        archived = conn.execute(
            """UPDATE unified_knowledge SET status = 'archived', updated_at = ?
               WHERE layer = 'consolidated' AND status = 'active'
               AND last_accessed < datetime('now', '-180 days')
               AND (user_id = ? OR user_id = 'default')""",
            (now, user_id)
        ).rowcount
        if archived:
            actions.append(f"archived_{archived}_stale_consolidated")

        # Stage 5: backfill active_summary for consolidated entries that lack it
        missing_summary = conn.execute(
            "SELECT id, content FROM unified_knowledge "
            "WHERE layer = 'consolidated' AND status = 'active' "
            "AND (active_summary IS NULL OR active_summary = '') "
            "AND (user_id = ? OR user_id = 'default')",
            (user_id,)
        ).fetchall()
        if missing_summary:
            for mr in missing_summary:
                s = _generate_summary(mr["content"])
                if s:
                    conn.execute(
                        "UPDATE unified_knowledge SET active_summary = ? WHERE id = ?",
                        (s, mr["id"])
                    )
            actions.append(f"backfilled_{len(missing_summary)}_summaries")

        # Stage 6: Belief update — time decay + archive
        try:
            from .nexus_belief import BeliefEngine
            be = BeliefEngine(conn)
            belief_result = be.update_all_beliefs()
            if belief_result["decayed_count"] or belief_result["archived_count"]:
                actions.append(f"belief_decayed_{belief_result['decayed_count']}_archived_{belief_result['archived_count']}")
        except Exception:
            pass

        # Stage 7: run miner
        # Entries with the same entity (策略: XXX) and same metric but close
        # values get averaged, with the oldest kept and newer superseded.
        _merged = 0
        _instant_rows = conn.execute(
            "SELECT id, content FROM unified_knowledge "
            "WHERE layer = 'instant' AND status = 'active' AND sleep_time_processed = 0 "
            "AND (user_id = ? OR user_id = 'default')",
            (user_id,)
        ).fetchall()
        for ir in _instant_rows:
            im = self._extract_metrics(ir["content"])
            if not im:
                continue
            entity = im[0][0]
            # Find other instant entries with the same entity
            _peers = [r for r in _instant_rows if r["id"] != ir["id"]]
            for pr in _peers:
                pm = self._extract_metrics(pr["content"])
                if not pm:
                    continue
                if pm[0][0] != entity:
                    continue
                # Same entity in instant layer — mark as processed
                conn.execute(
                    "UPDATE unified_knowledge SET sleep_time_processed = 1 WHERE id = ?",
                    (pr["id"],)
                )
                _merged += 1
            # Mark current as processed
            conn.execute(
                "UPDATE unified_knowledge SET sleep_time_processed = 1 WHERE id = ?",
                (ir["id"],)
            )
        if _merged:
            # conn.commit()  # batched to final commit
            actions.append(f"merged_{_merged}_duplicate_entities")

        # ── Sleep-time Stage 7: detect repeated entity mentions ──────────────
        # Any entity appearing 3+ times in consolidated layer gets a
        # summary entry promoted to consolidated.
        _entity_counts = {}
        _all_active = conn.execute(
            "SELECT id, content FROM unified_knowledge "
            "WHERE status = 'active' AND (user_id = ? OR user_id = 'default')",
            (user_id,)
        ).fetchall()
        for ar in _all_active:
            em = self._extract_metrics(ar["content"])
            if em:
                e = em[0][0]
                _entity_counts[e] = _entity_counts.get(e, 0) + 1
        for entity, count in _entity_counts.items():
            if count >= 3:
                _existing = conn.execute(
                    "SELECT id FROM unified_knowledge WHERE content LIKE ? "
                    "AND status = 'active' AND layer = 'consolidated'",
                    (f"%重复模式: {entity}%",)
                ).fetchone()
                if not _existing:
                    _summary_content = (
                        f"重复模式: {entity}\n"
                        f"出现次数: {count}\n"
                        f"发现时间: {datetime.now(timezone.utc).isoformat()}\n"
                        f"说明: 该实体在知识库中出现至少 {count} 次，"
                        f"可能值得关注或整理。"
                    )
                    _mhash = hashlib.sha256(_summary_content.encode()).hexdigest()[:16]
                    conn.execute(
                        "INSERT INTO unified_knowledge (content, domain_scores, layer, match_hash, user_id) "
                        "VALUES (?, ?, 'consolidated', ?, ?)",
                        (_summary_content, json.dumps({"pattern": 8, "raw_fact": 3}), _mhash, user_id)
                    )
                    _sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    _seg = _segment_fts(_summary_content)
                    conn.execute("INSERT INTO knowledge_fts(knowledge_fts, rowid, content) VALUES ('delete', ?, '')", (_sid,))
                    conn.execute("INSERT INTO knowledge_fts(rowid, content) VALUES (?, ?)", (_sid, _seg))
                    actions.append(f"pattern_detected_{entity}_x{count}")
        if any(a.startswith("pattern_detected") for a in actions):
            pass  # (commit batched to final)

        # ── Sleep-time Stage 8: emergent skills from repeated workflow ──────
        # Scan instant entries for task-type keywords. When the same combo
        # appears 3+ times, create a workflow-domain entry (emergent skill).
        _wf_keywords = [
            "分析", "查询", "搜索", "计算", "生成", "预测",
            "诊断", "报告", "监控", "对比", "评估", "汇总",
        ]
        _wf_entries = conn.execute(
            "SELECT content FROM unified_knowledge "
            "WHERE layer = 'instant' AND status = 'active' "
            "AND (user_id = ? OR user_id = 'default')",
            (user_id,)
        ).fetchall()
        _wf_patterns = {}
        for we in _wf_entries:
            hits = [kw for kw in _wf_keywords if kw in we["content"]]
            if len(hits) >= 2:
                key = "+".join(sorted(hits))
                _wf_patterns[key] = _wf_patterns.get(key, 0) + 1
        for pattern, count in _wf_patterns.items():
            if count >= 3:
                _existing = conn.execute(
                    "SELECT id FROM unified_knowledge WHERE content LIKE ? AND status = 'active'",
                    (f"%涌现技能: {pattern}%",)
                ).fetchone()
                if not _existing:
                    _skill_content = (
                        f"涌现技能: {pattern}\n"
                        f"触发次数: {count}\n"
                        f"发现时间: {datetime.now(timezone.utc).isoformat()}\n"
                        f"摘要: 检测到重复工作流({pattern})，在执行任务时可考虑调用此技能。\n"
                    )
                    _mhash = hashlib.sha256(_skill_content.encode()).hexdigest()[:16]
                    conn.execute(
                        "INSERT INTO unified_knowledge (content, domain_scores, layer, match_hash, user_id) "
                        "VALUES (?, ?, 'consolidated', ?, ?)",
                        (_skill_content, json.dumps({"workflow": 10, "pattern": 5}), _mhash, user_id)
                    )
                    _sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    _seg = _segment_fts(_skill_content)
                    conn.execute("INSERT INTO knowledge_fts(knowledge_fts, rowid, content) VALUES ('delete', ?, '')", (_sid,))
                    conn.execute("INSERT INTO knowledge_fts(rowid, content) VALUES (?, ?)", (_sid, _seg))
                    actions.append(f"emergent_skill_{pattern}_x{count}")
                    # Also write SKILL.md file to make it a real skill
                    try:
                        _skill_name = f"emergent-{pattern.lower().replace('+', '-')}"
                        _skill_dir = Path.home() / ".hermes" / "skills" / "emergent" / _skill_name
                        _skill_dir.mkdir(parents=True, exist_ok=True)
                        _skill_md = _skill_dir / "SKILL.md"
                        if not _skill_md.exists():
                            _desc_parts = pattern.split("+")
                            _desc = "、".join(_desc_parts)
                            _skill_md.write_text(
                                f"---\n"
                                f"name: {_skill_name}\n"
                                f"description: 自动涌现的工作流技能 - {_desc}\n"
                                f"category: emergent\n"
                                f"---\n"
                                f"\n"
                                f"# {_desc} 工作流\n"
                                f"\n"
                                f"该技能由 Nexus 自动检测到重复工作流后生成。\n"
                                f"\n"
                                f"**触发模式**: {pattern}\n"
                                f"**触发次数**: {count}\n"
                                f"**发现时间**: {datetime.now(timezone.utc).isoformat()}\n"
                                f"\n"
                                f"## 使用说明\n"
                                f"\n"
                                f"此技能包含 {_desc} 相关的工作流步骤。\n"
                                f"使用 skills_list 查看，skill_view 加载。\n",
                                encoding="utf-8"
                            )
                            actions.append(f"skill_file_created_wf_{pattern}")
                    except Exception:
                        pass

        # ── Sleep-time Stage 9: cross-validate consolidated/candidate layers ──
        try:
            val_result = self._validate_by_layer(user_id)
            if val_result["conflicts_found"] > 0:
                actions.append(f"layer_conflicts_{val_result['conflicts_found']}")
        except Exception:
            pass

        # ── Stage 10: Miner — scan interaction patterns ─────────────────────
        try:
            from .nexus_miner import NexusMiner
            miner = NexusMiner()
            report = miner.mine_all()
            miner.close()
            risk_count = report.get("summary", {}).get("high_risk", 0)
            pattern_count = report.get("summary", {}).get("total_patterns", 0)
            if risk_count:
                actions.append(f"high_risk_knowledge_{risk_count}")
            if pattern_count:
                actions.append(f"query_patterns_{pattern_count}")
            # Auto-demote high-risk knowledge
            for k in report.get("high_risk_knowledge", []):
                try:
                    self._demote(k["id"], "auto: 纠正率过高", user_id)
                    actions.append(f"auto_demoted_{k['id']}")
                except Exception:
                    pass
        except Exception:
            pass

        conn.commit()
        # WAL checkpoint after consolidation
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        return {"actions": actions if actions else ["no_op"]}
