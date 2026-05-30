Title: Nexus Memory – 20ms persistent memory for AI agents, 4500x faster than LLM-based retrieval

URL: https://github.com/chuf-China/nexus-memory

Nexus Memory is a cross-session persistent memory system for AI coding agents. It stores knowledge as SPO triplets (Subject-Predicate-Object) with confidence scoring and automatic lifecycle management.

Key technical decisions:
- Pure SQLite + FTS5 for full-text search (20ms), HNSW for vector search (50ms), NetworkX for graph queries (10ms)
- 3-tier knowledge architecture: Observation (0.3-0.5) → Belief (0.7-0.85) → Fact (0.85+), with automatic promotion on repeated patterns and degradation on inactivity
- Correction handling via SPO conflict detection: when a fact is corrected, the old entry's confidence drops 0.30 and the new entry promotes, creating a natural淘汰 effect
- 30+ built-in threat patterns (prompt injection, role hijacking, exfiltration detection)
- Zero external dependencies beyond numpy — no Qdrant, no Redis, no LLM API calls for retrieval

Comparison with agentmemory (npm, iii engine) and mem0 (Python, Qdrant-backed): Nexus wins on search speed, memory quality transparency, and security. Loses on agent integration breadth (agentmemory supports 20+ platforms).

Written in Python, MIT licensed.
