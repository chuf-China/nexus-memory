"""nexus_api.py — REST API for Nexus knowledge store

FastAPI endpoints for external access to the four-layer search,
knowledge write, health checks, and metrics.

Usage:
  # Run directly
  python -m agent.nexus_api --host 0.0.0.0 --port 8900

  # Or from code
  from .api import create_app
  app = create_app(db_path="~/.hermes/data/nexus/nexus.db")

Endpoints:
  GET  /health           — Four-layer health check
  GET  /search           — Search knowledge (fts/semantic/graph/hybrid)
  POST /write            — Write new knowledge entry
  GET  /metrics          — Performance metrics summary
  GET  /schema/status    — Schema migration status
  POST /schema/migrate   — Run pending migrations
  GET  /stats            — Database statistics
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy imports — only when API is actually used
_app = None


def create_app(db_path: Optional[str] = None):
    """Create FastAPI app with Nexus routes."""
    from fastapi import FastAPI, Query, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel

    app = FastAPI(
        title="Nexus API",
        description="Hermes NEXUS four-layer knowledge store REST API",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Resolve DB path
    if db_path is None:
        db_path = str(Path.home() / ".hermes" / "data" / "nexus" / "nexus.db")

    def _get_core():
        from .core import NexusCore
        return NexusCore(db_path)

    # ── Health ──────────────────────────────────────────

    @app.get("/health")
    def health():
        """Four-layer health check."""
        from .health import health_check
        result = health_check(db_path)
        status_code = {"ok": 200, "warn": 201, "error": 503}.get(
            result["overall"], 503
        )
        from fastapi.responses import JSONResponse
        return JSONResponse(content=result, status_code=status_code)

    # ── Search ──────────────────────────────────────────

    @app.get("/search")
    def search(
        q: str = Query(..., description="Search query"),
        mode: str = Query("fts", description="Search mode: fts|semantic|graph|hybrid"),
        limit: int = Query(5, ge=1, le=50),
        user_id: str = Query("default"),
        debug: bool = Query(False),
    ):
        """Search knowledge entries."""
        core = _get_core()
        results = core.search(
            query=q, user_id=user_id, limit=limit,
            mode=mode, include_debug=debug,
        )
        return {
            "query": q,
            "mode": mode,
            "count": len(results),
            "results": results,
        }

    # ── Write ───────────────────────────────────────────

    class WriteRequest(BaseModel):
        content: str
        user_id: str = "default"
        source_session_id: str = ""
        event_time: Optional[str] = None
        initial_confidence: Optional[float] = None

    @app.post("/write")
    def write(req: WriteRequest):
        """Write new knowledge entry."""
        if not req.content or len(req.content.strip()) < 3:
            raise HTTPException(400, "Content too short (min 3 chars)")

        core = _get_core()
        result = core.write(
            content=req.content,
            user_id=req.user_id,
            source_session_id=req.source_session_id,
            event_time=req.event_time,
            initial_confidence=req.initial_confidence,
        )
        return result

    # ── Metrics ─────────────────────────────────────────

    @app.get("/metrics")
    def metrics(days: int = Query(7, ge=1, le=90),
                format: str = Query("json", description="json or prometheus")):
        """Performance metrics. Use format=prometheus for Prometheus scraping."""
        from .metrics import NexusMetrics
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        nm = NexusMetrics(conn)

        if format == "prometheus":
            from fastapi.responses import PlainTextResponse
            prom_output = nm.format_prometheus()
            conn.close()
            return PlainTextResponse(content=prom_output, media_type="text/plain")

        summary = nm.get_summary(days=days)
        conn.close()
        return summary

    # ── Schema ──────────────────────────────────────────

    @app.get("/schema/status")
    def schema_status():
        """Schema migration status."""
        from .migration import SchemaMigration
        import sqlite3
        conn = sqlite3.connect(db_path)
        mig = SchemaMigration(conn)
        status = mig.status()
        conn.close()
        return status

    @app.post("/schema/migrate")
    def schema_migrate(target: Optional[int] = None):
        """Run pending migrations."""
        from .migration import SchemaMigration
        import sqlite3
        conn = sqlite3.connect(db_path)
        mig = SchemaMigration(conn)
        applied = mig.run(target=target)
        status = mig.status()
        conn.close()
        return {"applied": applied, "status": status}

    # ── Stats ───────────────────────────────────────────

    @app.get("/stats")
    def stats():
        """Database statistics."""
        import sqlite3
        conn = sqlite3.connect(db_path)

        total = conn.execute(
            "SELECT count(*) FROM unified_knowledge WHERE status = 'active'"
        ).fetchone()[0]

        by_layer = {}
        for row in conn.execute(
            "SELECT layer, count(*) as cnt FROM unified_knowledge "
            "WHERE status = 'active' GROUP BY layer"
        ):
            by_layer[row[0]] = row[1]

        by_user = {}
        for row in conn.execute(
            "SELECT user_id, count(*) as cnt FROM unified_knowledge "
            "WHERE status = 'active' GROUP BY user_id ORDER BY cnt DESC LIMIT 10"
        ):
            by_user[row[0]] = row[1]

        try:
            embeddings = conn.execute(
                "SELECT count(*) FROM knowledge_embeddings"
            ).fetchone()[0]
        except Exception:
            embeddings = 0

        try:
            relations = conn.execute(
                "SELECT count(*) FROM entity_relations"
            ).fetchone()[0]
        except Exception:
            relations = 0

        try:
            conflicts = conn.execute(
                "SELECT count(*) FROM knowledge_conflicts WHERE resolved = 0"
            ).fetchone()[0]
        except Exception:
            conflicts = 0

        conn.close()

        return {
            "total_entries": total,
            "by_layer": by_layer,
            "by_user": by_user,
            "embeddings": embeddings,
            "entity_relations": relations,
            "open_conflicts": conflicts,
        }

    return app


def main():
    """CLI entry point for the API server."""
    parser = argparse.ArgumentParser(description="Nexus REST API server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8900, help="Bind port")
    parser.add_argument("--db", default=None, help="Database path")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    args = parser.parse_args()

    import uvicorn
    app = create_app(db_path=args.db)
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
