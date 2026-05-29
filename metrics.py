"""nexus_metrics.py — 检索性能指标采集

每次 search/write 自动记录延迟、来源分布、命中率等指标。
数据写入 nexus_meta 表（时序数据）。

用法:
  from .metrics import NexusMetrics
  m = NexusMetrics(conn)
  m.record_search(latency_ms=12.3, sources=["fts", "hnsw"], hit_count=5, query="test")
  m.record_write(latency_ms=8.1, content_len=120)
  m.get_summary()  # → {search_count, avg_latency, ...}
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CREATE_META_SQL = """
CREATE TABLE IF NOT EXISTS nexus_metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_type TEXT NOT NULL,       -- 'search' | 'write' | 'rerank' | 'embed'
    latency_ms  REAL,
    sources     TEXT,                -- JSON array: ["fts", "hnsw", "graph"]
    hit_count   INTEGER DEFAULT 0,
    query_len   INTEGER DEFAULT 0,
    content_len INTEGER DEFAULT 0,
    extra       TEXT,                -- JSON object for ad-hoc data
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nm_type ON nexus_metrics(metric_type);
CREATE INDEX IF NOT EXISTS idx_nm_created ON nexus_metrics(created_at);
"""


class NexusMetrics:
    """轻量级性能指标采集器。写入 nexus_meta 表。"""

    def __init__(self, conn):
        self.conn = conn
        self._ensure_table()

    def _ensure_table(self):
        try:
            self.conn.executescript(_CREATE_META_SQL)
            self.conn.commit()
        except Exception as e:
            logger.debug("NexusMetrics: table init skipped: %s", e)

    def record_search(self, latency_ms: float, sources: List[str],
                      hit_count: int, query_len: int = 0,
                      extra: Optional[Dict] = None):
        """Record a search event."""
        try:
            self.conn.execute(
                "INSERT INTO nexus_metrics (metric_type, latency_ms, sources, hit_count, query_len, extra) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "search",
                    round(latency_ms, 2),
                    json.dumps(sources),
                    hit_count,
                    query_len,
                    json.dumps(extra) if extra else None,
                )
            )
            self.conn.commit()
        except Exception:
            pass

    def record_write(self, latency_ms: float, content_len: int,
                     action: str = "created",
                     extra: Optional[Dict] = None):
        """Record a write event."""
        try:
            self.conn.execute(
                "INSERT INTO nexus_metrics (metric_type, latency_ms, content_len, extra) "
                "VALUES (?, ?, ?, ?)",
                (
                    "write",
                    round(latency_ms, 2),
                    content_len,
                    json.dumps({"action": action, **(extra or {})}),
                )
            )
            self.conn.commit()
        except Exception:
            pass

    def record_rerank(self, latency_ms: float, input_count: int,
                      output_count: int):
        """Record a rerank event."""
        try:
            self.conn.execute(
                "INSERT INTO nexus_metrics (metric_type, latency_ms, hit_count, extra) "
                "VALUES (?, ?, ?, ?)",
                (
                    "rerank",
                    round(latency_ms, 2),
                    output_count,
                    json.dumps({"input_count": input_count}),
                )
            )
            self.conn.commit()
        except Exception:
            pass

    def record_embed(self, latency_ms: float, content_len: int):
        """Record an embedding event."""
        try:
            self.conn.execute(
                "INSERT INTO nexus_metrics (metric_type, latency_ms, content_len) "
                "VALUES (?, ?, ?)",
                ("embed", round(latency_ms, 2), content_len)
            )
            self.conn.commit()
        except Exception:
            pass

    def get_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get aggregated metrics for the last N hours."""
        try:
            cutoff = datetime.now(timezone.utc).isoformat()
            # Simple approach: last N hours of data
            rows = self.conn.execute(
                "SELECT metric_type, latency_ms, sources, hit_count "
                "FROM nexus_metrics "
                "WHERE created_at > datetime('now', ?) "
                "ORDER BY created_at DESC",
                (f"-{hours} hours",)
            ).fetchall()

            # Support both sqlite3.Row and tuple access
            def _get(row, key, idx):
                try:
                    return row[key]
                except (KeyError, IndexError, TypeError):
                    return row[idx]

            if not rows:
                return {"period_hours": hours, "total_events": 0}

            by_type: Dict[str, List[float]] = {}
            total_hits = 0
            source_counts: Dict[str, int] = {}

            for r in rows:
                mtype = _get(r, "metric_type", 0)
                latency = _get(r, "latency_ms", 1) or 0
                sources_raw = _get(r, "sources", 2)
                hits = _get(r, "hit_count", 3) or 0

                by_type.setdefault(mtype, []).append(latency)
                total_hits += hits
                if sources_raw:
                    try:
                        for s in json.loads(sources_raw):
                            source_counts[s] = source_counts.get(s, 0) + 1
                    except Exception:
                        pass

            summary: Dict[str, Any] = {
                "period_hours": hours,
                "total_events": len(rows),
                "total_hits": total_hits,
                "source_distribution": source_counts,
            }

            for mtype, latencies in by_type.items():
                summary[f"{mtype}_count"] = len(latencies)
                summary[f"{mtype}_avg_ms"] = round(sum(latencies) / len(latencies), 1)
                summary[f"{mtype}_p95_ms"] = round(
                    sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0, 1
                )

            return summary
        except Exception as e:
            return {"error": str(e)}

    def format_prometheus(self) -> str:
        """Export metrics in Prometheus exposition format."""
        lines = []

        try:
            # Search metrics
            rows = self.conn.execute(
                "SELECT COUNT(*) as cnt, AVG(latency_ms) as avg_ms "
                "FROM nexus_metrics WHERE metric_type='search' "
                "AND created_at > datetime('now', '-1 hours')"
            ).fetchone()
            lines.append(f"nexus_searches_total {{}} {rows[0]}")
            lines.append(f"nexus_search_avg_latency_ms {{}} {round(rows[1] or 0, 2)}")

            # Write metrics
            rows = self.conn.execute(
                "SELECT COUNT(*) as cnt, AVG(latency_ms) as avg_ms "
                "FROM nexus_metrics WHERE metric_type='write' "
                "AND created_at > datetime('now', '-1 hours')"
            ).fetchone()
            lines.append(f"nexus_writes_total {{}} {rows[0]}")
            lines.append(f"nexus_write_avg_latency_ms {{}} {round(rows[1] or 0, 2)}")

            # Storage metrics
            active = self.conn.execute(
                "SELECT COUNT(*) FROM unified_knowledge WHERE status='active'"
            ).fetchone()[0]
            lines.append(f"nexus_active_memories {{}} {active}")

            try:
                facts = self.conn.execute(
                    "SELECT COUNT(*) FROM facts WHERE superseded_by IS NULL"
                ).fetchone()[0]
                lines.append(f"nexus_active_facts {{}} {facts}")
            except Exception:
                pass

            try:
                entities = self.conn.execute(
                    "SELECT COUNT(DISTINCT entity_a) FROM entity_relations"
                ).fetchone()[0]
                lines.append(f"nexus_entities_total {{}} {entities}")
            except Exception:
                pass

            try:
                edges = self.conn.execute(
                    "SELECT COUNT(*) FROM entity_relations"
                ).fetchone()[0]
                lines.append(f"nexus_edges_total {{}} {edges}")
            except Exception:
                pass

        except Exception as e:
            lines.append(f"nexus_error {{error=\"{e}\"}} 1")

        return "\n".join(lines) + "\n"

    def prune(self, days: int = 30):
        """Remove metrics older than N days."""
        try:
            deleted = self.conn.execute(
                "DELETE FROM nexus_metrics WHERE created_at < datetime('now', ?)",
                (f"-{days} days",)
            ).rowcount
            if deleted:
                self.conn.commit()
                logger.info("NexusMetrics: pruned %d old records", deleted)
        except Exception:
            pass
