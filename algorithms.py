"""nexus_algorithms.py — Graph algorithms via NetworkX

Provides centrality, community detection, pathfinding, and connectivity
analysis on the Nexus knowledge graph.

Usage:
  from .algorithms import GraphAlgorithms
  ga = GraphAlgorithms(conn)
  top = ga.pagerank(top_k=10)
  communities = ga.detect_communities()
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_NX_AVAILABLE = False
try:
    import networkx as nx
    _NX_AVAILABLE = True
except ImportError:
    pass

_LOUVAIN_AVAILABLE = False
try:
    import community as community_louvain
    _LOUVAIN_AVAILABLE = True
except ImportError:
    pass


class GraphAlgorithms:
    """Graph algorithm suite built on NetworkX + SQLite."""

    def __init__(self, conn):
        self.conn = conn
        self._nx_graph: Optional[Any] = None

    @property
    def available(self) -> bool:
        return _NX_AVAILABLE

    def _build_nx_graph(self) -> Any:
        """Build NetworkX DiGraph from entity_relations."""
        if not _NX_AVAILABLE:
            return None
        G = nx.DiGraph()
        rows = self.conn.execute(
            "SELECT entity_a, entity_b, relation_type, weight, hit_count "
            "FROM entity_relations"
        ).fetchall()
        for r in rows:
            G.add_edge(
                r["entity_a"], r["entity_b"],
                relation=r["relation_type"],
                weight=r["weight"] or 1.0,
                hits=r["hit_count"] or 1,
            )
        self._nx_graph = G
        return G

    @property
    def graph(self):
        if self._nx_graph is None:
            self._build_nx_graph()
        return self._nx_graph

    def invalidate_cache(self):
        self._nx_graph = None

    # ── Centrality ─────────────────────────────────────────

    def degree_centrality(self, top_k: int = 10) -> List[Tuple[str, float]]:
        """Most connected entities."""
        if not self.available:
            return []
        return sorted(
            nx.degree_centrality(self.graph).items(),
            key=lambda x: -x[1]
        )[:top_k]

    def betweenness_centrality(self, top_k: int = 10) -> List[Tuple[str, float]]:
        """Bridge entities (high betweenness = connects different clusters)."""
        if not self.available:
            return []
        return sorted(
            nx.betweenness_centrality(self.graph).items(),
            key=lambda x: -x[1]
        )[:top_k]

    def pagerank(self, top_k: int = 10, alpha: float = 0.85) -> List[Tuple[str, float]]:
        """PageRank — most important entities by link structure."""
        if not self.available:
            return []
        try:
            pr = nx.pagerank(self.graph, alpha=alpha)
            return sorted(pr.items(), key=lambda x: -x[1])[:top_k]
        except Exception:
            return []

    def eigenvector_centrality(self, top_k: int = 10) -> List[Tuple[str, float]]:
        """Eigenvector centrality — entities connected to important entities."""
        if not self.available:
            return []
        try:
            ec = nx.eigenvector_centrality(self.graph, max_iter=1000)
            return sorted(ec.items(), key=lambda x: -x[1])[:top_k]
        except Exception:
            return []

    # ── Community Detection ────────────────────────────────

    def detect_communities(self) -> List[Tuple[int, List[str]]]:
        """Detect communities using Louvain (if available) or greedy modularity."""
        if not self.available:
            return []
        G = self.graph.to_undirected()

        if _LOUVAIN_AVAILABLE:
            partition = community_louvain.best_partition(G)
        else:
            # Fallback: connected components as "communities"
            partition = {}
            for i, component in enumerate(nx.connected_components(G)):
                for node in component:
                    partition[node] = i

        communities: Dict[int, List[str]] = {}
        for node, comm_id in partition.items():
            communities.setdefault(comm_id, []).append(node)

        return sorted(communities.items(), key=lambda x: -len(x[1]))

    def community_summaries(self) -> List[Dict[str, Any]]:
        """Communities with member lists and size info."""
        communities = self.detect_communities()
        summaries = []
        for comm_id, members in communities[:10]:  # top 10 communities
            summaries.append({
                "community_id": comm_id,
                "size": len(members),
                "members": members[:20],  # cap
            })
        return summaries

    # ── Pathfinding ────────────────────────────────────────

    def shortest_path(self, source: str, target: str) -> Optional[List[str]]:
        """Shortest path between two entities."""
        if not self.available:
            return None
        try:
            return nx.shortest_path(self.graph, source, target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def all_paths(self, source: str, target: str,
                  cutoff: int = 4) -> List[List[str]]:
        """All simple paths up to cutoff length."""
        if not self.available:
            return []
        try:
            return list(nx.all_simple_paths(self.graph, source, target, cutoff=cutoff))
        except nx.NodeNotFound:
            return []

    # ── Connectivity ───────────────────────────────────────

    def connected_components(self) -> List[List[str]]:
        """Connected components (knowledge islands)."""
        if not self.available:
            return []
        G = self.graph.to_undirected()
        return [list(c) for c in sorted(
            nx.connected_components(G), key=len, reverse=True
        )]

    def isolates(self) -> List[str]:
        """Isolated entities (no connections)."""
        if not self.available:
            return []
        return list(nx.isolates(self.graph))

    def density(self) -> float:
        """Graph density (0-1)."""
        if not self.available:
            return 0.0
        return nx.density(self.graph)

    # ── Summary ────────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        """Full graph analysis summary."""
        if not self.available:
            return {"available": False, "reason": "networkx not installed"}
        G = self.graph
        return {
            "available": True,
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "density": round(nx.density(G), 4),
            "components": len(list(nx.connected_components(G.to_undirected()))),
            "top_pagerank": self.pagerank(top_k=5),
            "top_degree": self.degree_centrality(top_k=5),
            "community_count": len(self.detect_communities()),
        }
