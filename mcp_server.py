"""nexus_mcp_server.py — MCP Server for Nexus memory system.

Exposes Nexus capabilities as MCP tools for any MCP-compatible AI client.

Usage:
  python -m nexus.mcp_server          # stdio mode
  python -m nexus.mcp_server --port 8080  # SSE mode
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from mcp.server.fastmcp import FastMCP
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

# Lazy singletons
_nc = None
_eg = None
_be = None


def _get_nc():
    global _nc
    if _nc is None:
        from .core import NexusCore
        from pathlib import Path
        db = str(Path.home() / ".hermes" / "data" / "nexus" / "nexus.db")
        _nc = NexusCore(db)
    return _nc


def _get_eg():
    global _eg
    if _eg is None:
        from .graph import EntityGraph
        _eg = EntityGraph(_get_nc()._conn())
    return _eg


def _get_be():
    global _be
    if _be is None:
        from .belief import BeliefEngine
        _be = BeliefEngine(_get_nc()._conn())
    return _be


def create_mcp_server() -> "FastMCP":
    """Create and configure the MCP server."""
    mcp = FastMCP("nexus-memory")

    @mcp.tool()
    def nexus_save(content: str, user_id: str = "default",
                   domain: str = "") -> str:
        """Save a memory to Nexus.

        Args:
            content: The memory content to save.
            user_id: User ID (default: "default").
            domain: Optional domain hint (identity, workflow, rule, etc).
        """
        nc = _get_nc()
        ds = json.dumps({domain: 0.8}) if domain else "{}"
        result = nc.write(content=content, user_id=user_id, domain_scores=ds)
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    def nexus_search(query: str, mode: str = "hybrid",
                     limit: int = 5, user_id: str = "default") -> str:
        """Search Nexus memories.

        Args:
            query: Search query.
            mode: "fts", "semantic", "graph", or "hybrid".
            limit: Max results.
            user_id: User ID filter.
        """
        nc = _get_nc()
        results = nc.search(query, mode=mode, limit=limit, user_id=user_id)
        # Clean internal fields
        cleaned = []
        for r in results:
            cleaned.append({
                "id": r.get("id"),
                "content": (r.get("content") or "")[:200],
                "score": round(r.get("similarity") or r.get("fusion_score", 0), 3),
                "source": r.get("_source", "unknown"),
                "layer": r.get("layer", ""),
            })
        return json.dumps(cleaned, ensure_ascii=False)

    @mcp.tool()
    def nexus_query_fact(subject: Optional[str] = None,
                         predicate: Optional[str] = None,
                         limit: int = 10) -> str:
        """Query structured facts (subject-predicate-object).

        Args:
            subject: Filter by subject (e.g. "PostgreSQL").
            predicate: Filter by predicate (e.g. "version").
            limit: Max results.
        """
        try:
            from .facts import FactStore
            fs = FactStore(_get_nc()._conn())
            results = fs.query(subject=subject, predicate=predicate, limit=limit)
            return json.dumps(results, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)})

    @mcp.tool()
    def nexus_graph_neighbors(entity: str, depth: int = 2) -> str:
        """Find entities related to the given entity via the knowledge graph.

        Args:
            entity: Entity name to search from.
            depth: Number of hops (1-3).
        """
        eg = _get_eg()
        results = eg.traverse(entity, max_depth=depth, min_weight=0.3)
        return json.dumps(results[:20], ensure_ascii=False)

    @mcp.tool()
    def nexus_graph_path(source: str, target: str) -> str:
        """Find shortest path between two entities in the graph.

        Args:
            source: Source entity name.
            target: Target entity name.
        """
        eg = _get_eg()
        path = eg.find_path(source, target)
        return json.dumps(path or {"error": "no path found"}, ensure_ascii=False)

    @mcp.tool()
    def nexus_save_feedback(memory_id: int, positive: bool,
                            reason: str = "") -> str:
        """Provide feedback on a memory (positive/negative).

        Args:
            memory_id: ID of the memory.
            positive: True for positive feedback, False for correction.
            reason: Optional reason.
        """
        be = _get_be()
        feedback = 1 if positive else -1
        result = be.on_feedback(memory_id, feedback)
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    def nexus_health() -> str:
        """Check Nexus system health (all subsystems)."""
        from .health import health_check
        result = health_check()
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    def nexus_stats() -> str:
        """Get Nexus statistics (knowledge count, beliefs, graph size)."""
        nc = _get_nc()
        conn = nc._conn()
        total = conn.execute(
            "SELECT COUNT(*) FROM unified_knowledge WHERE status='active'"
        ).fetchone()[0]
        beliefs = _get_be().stats()
        graph_edges = conn.execute(
            "SELECT COUNT(*) FROM entity_relations"
        ).fetchone()[0]
        return json.dumps({
            "active_memories": total,
            "beliefs": beliefs,
            "graph_edges": graph_edges,
        }, ensure_ascii=False)

    return mcp


def main():
    """Entry point for MCP server."""
    if not _MCP_AVAILABLE:
        print("Error: MCP package not installed. Run: pip install mcp", file=sys.stderr)
        sys.exit(1)

    import argparse
    parser = argparse.ArgumentParser(description="Nexus MCP Server")
    parser.add_argument("--port", type=int, default=0, help="SSE port (0=stdio)")
    args = parser.parse_args()

    mcp = create_mcp_server()

    if args.port:
        mcp.run(transport="sse", port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
