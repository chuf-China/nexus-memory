"""nexus_health.py — 四层检索系统健康检查

检查 FTS5、Embedding、Graph、HNSW 四层 + Reranker 是否正常工作。
每项返回 {status, latency_ms, detail}。

用法:
  from .health import health_check
  result = health_check(db_path)
  # → {"fts5": {"status": "ok", ...}, "embedding": {...}, ...}

  或 CLI:
  python -m agent.nexus_health <db_path>
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import struct
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

DB_PATH_DEFAULT = os.path.expanduser("~/.hermes/data/nexus.db")


def _timed(fn):
    """Execute fn(), return (result, latency_ms)."""
    t0 = time.monotonic()
    try:
        result = fn()
    except Exception as e:
        return e, -1
    latency_ms = round((time.monotonic() - t0) * 1000, 1)
    return result, latency_ms


def _check_fts5(conn: sqlite3.Connection) -> Dict[str, Any]:
    """FTS5 层: 计数 + 分词测试。"""
    # Count
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM knowledge_fts"
        ).fetchone()[0]
    except Exception as e:
        return {"status": "error", "latency_ms": 0, "detail": f"COUNT failed: {e}"}

    # Segment test: 分一段中文，看 FTS5 能否 MATCH
    test_query = "系统健康"
    try:
        from .utils import segment_fts
        seg = segment_fts(test_query)
        # Just verify it doesn't crash — actual match depends on data
        conn.execute(
            "SELECT COUNT(*) FROM knowledge_fts WHERE content MATCH ?",
            (seg,)
        )
        segment_ok = True
    except Exception:
        segment_ok = False

    return {
        "status": "ok" if count > 0 else "warn",
        "latency_ms": 0,
        "detail": f"{count} entries indexed, segment_test={'ok' if segment_ok else 'fail'}",
    }


def _check_embedding(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Embedding 层: fastembed 加载 + 维度验证。"""
    # Check knowledge_embeddings table
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM knowledge_embeddings"
        ).fetchone()[0]
    except Exception:
        count = 0

    # Check embed_dim validity
    bad_dim = 0
    if count > 0:
        try:
            bad_dim = conn.execute(
                "SELECT COUNT(*) FROM knowledge_embeddings "
                "WHERE embed_dim IS NULL OR embed_dim <= 0"
            ).fetchone()[0]
        except Exception:
            pass

    # Test fastembed load
    embedder_ok = False
    embed_dim = 0
    try:
        from .embedder import get_embedder
        embedder = get_embedder()
        embedder_ok = embedder.available
        embed_dim = embedder.dim if embedder_ok else 0
    except Exception:
        pass

    status = "ok" if embedder_ok and count > 0 else (
        "warn" if embedder_ok else "error"
    )

    return {
        "status": status,
        "latency_ms": 0,
        "detail": (
            f"fastembed={'ok' if embedder_ok else 'fail'}, "
            f"dim={embed_dim}, "
            f"vectors={count}, "
            f"bad_dim={bad_dim}"
        ),
    }


def _check_graph(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Graph 层: entity_relations 计数。"""
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM entity_relations"
        ).fetchone()[0]
    except Exception:
        return {"status": "error", "latency_ms": 0, "detail": "entity_relations table missing"}

    # Check entity extraction works
    try:
        from .graph import extract_entities
        entities = extract_entities("fastembed 依赖 ONNX Runtime 进行推理")
        extract_ok = len(entities) > 0
    except Exception:
        extract_ok = False

    return {
        "status": "ok" if count > 0 else "warn",
        "latency_ms": 0,
        "detail": f"{count} relations, extract_test={'ok' if extract_ok else 'fail'}",
    }


def _check_hnsw(conn: sqlite3.Connection) -> Dict[str, Any]:
    """HNSW 层: build() + search() 测试。"""
    try:
        from .hnsw import get_hnsw_index
        hnsw = get_hnsw_index(conn, dim=512)
        build_ok = hnsw.available or hnsw.build()

        if not build_ok:
            return {
                "status": "warn",
                "latency_ms": 0,
                "detail": f"backend={hnsw._backend}, no embeddings or backend unavailable",
            }

        # Test search with a zero vector
        test_vec = [0.0] * 512
        results = hnsw.search(test_vec, k=1)
        search_ok = results is not None

        return {
            "status": "ok" if search_ok else "warn",
            "latency_ms": 0,
            "detail": (
                f"backend={hnsw._backend}, "
                f"build={'ok' if build_ok else 'fail'}, "
                f"search={'ok' if search_ok else 'fail'}, "
                f"entries={len(hnsw._entry_ids)}"
            ),
        }
    except Exception as e:
        return {"status": "error", "latency_ms": 0, "detail": str(e)}


def _check_reranker() -> Dict[str, Any]:
    """Reranker 层: 模型加载测试。"""
    try:
        from .embedder import Reranker
        reranker = Reranker()
        reranker._load_cross_encoder()

        if reranker._model is None or reranker._model == "score_only":
            return {
                "status": "warn",
                "latency_ms": 0,
                "detail": "cross-encoder not available, using score_only mode",
            }

        # Quick rerank test
        test_results = [
            {"content": "fastembed 向量检索", "similarity": 0.8},
            {"content": "FTS5 全文搜索", "similarity": 0.6},
        ]
        reranked = reranker.rerank("向量检索", test_results, top_k=2)
        rerank_ok = len(reranked) > 0 and "rerank_score" in reranked[0]

        return {
            "status": "ok" if rerank_ok else "warn",
            "latency_ms": 0,
            "detail": f"model={'loaded' if reranker._model != 'score_only' else 'score_only'}, rerank_test={'ok' if rerank_ok else 'fail'}",
        }
    except Exception as e:
        return {"status": "error", "latency_ms": 0, "detail": str(e)}


def _check_facts(conn) -> Dict[str, Any]:
    """Check fact store subsystem."""
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='facts'"
        ).fetchone()
        if not exists:
            return {"status": "warn", "detail": "facts table not created yet"}
        total = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM facts WHERE superseded_by IS NULL"
        ).fetchone()[0]
        return {"status": "ok", "detail": f"total={total}, active={active}"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def _check_episodes(conn) -> Dict[str, Any]:
    """Check episode store subsystem."""
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='episodes'"
        ).fetchone()
        if not exists:
            return {"status": "warn", "detail": "episodes table not created yet"}
        total = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        sessions = conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM episodes"
        ).fetchone()[0]
        return {"status": "ok", "detail": f"episodes={total}, sessions={sessions}"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def health_check(db_path: str = DB_PATH_DEFAULT) -> Dict[str, Any]:
    """执行完整的四层健康检查。

    Returns:
        {
            "overall": "ok" | "warn" | "error",
            "checks": {
                "fts5": {status, latency_ms, detail},
                "embedding": {status, latency_ms, detail},
                "graph": {status, latency_ms, detail},
                "hnsw": {status, latency_ms, detail},
                "reranker": {status, latency_ms, detail},
            },
            "db_path": str,
            "timestamp": str,
        }
    """
    from datetime import datetime, timezone

    result = {
        "overall": "ok",
        "checks": {},
        "db_path": db_path,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Open connection
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        result["overall"] = "error"
        result["checks"]["connection"] = {
            "status": "error",
            "latency_ms": 0,
            "detail": str(e),
        }
        return result

    # Run each check with timing
    checks = [
        ("fts5", lambda: _check_fts5(conn)),
        ("embedding", lambda: _check_embedding(conn)),
        ("graph", lambda: _check_graph(conn)),
        ("hnsw", lambda: _check_hnsw(conn)),
        ("reranker", _check_reranker),
        ("facts", lambda: _check_facts(conn)),
        ("episodes", lambda: _check_episodes(conn)),
    ]

    for name, check_fn in checks:
        check_result, latency = _timed(check_fn)
        if isinstance(check_result, Exception):
            check_result = {
                "status": "error",
                "latency_ms": latency,
                "detail": str(check_result),
            }
        else:
            check_result["latency_ms"] = latency
        result["checks"][name] = check_result

    # Overall status
    statuses = [c["status"] for c in result["checks"].values()]
    if "error" in statuses:
        result["overall"] = "error"
    elif "warn" in statuses:
        result["overall"] = "warn"

    conn.close()
    return result


def format_health(result: Dict[str, Any]) -> str:
    """Format health check result for CLI output."""
    lines = []
    lines.append("=" * 55)
    lines.append("  NEXUS 四层健康检查")
    lines.append("=" * 55)
    lines.append(f"  DB: {result.get('db_path', '?')}")
    lines.append(f"  时间: {result.get('timestamp', '?')}")
    lines.append("")

    icons = {"ok": "[ok]", "warn": "[!!]", "error": "[XX]"}

    checks = result.get("checks", {})
    for name in ["fts5", "embedding", "graph", "hnsw", "reranker"]:
        c = checks.get(name, {})
        icon = icons.get(c.get("status", "error"), "[??]")
        latency = c.get("latency_ms", 0)
        detail = c.get("detail", "")
        lines.append(f"  {icon} {name:12s}  {latency:6.1f}ms  {detail}")

    lines.append("")
    overall = result.get("overall", "error")
    overall_icon = icons.get(overall, "[??]")
    lines.append(f"  Overall: {overall_icon} {overall.upper()}")
    lines.append("=" * 55)
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else DB_PATH_DEFAULT
    result = health_check(db)
    print(format_health(result))
    # Exit code: 0=ok, 1=warn, 2=error
    sys.exit({"ok": 0, "warn": 1, "error": 2}.get(result["overall"], 2))
