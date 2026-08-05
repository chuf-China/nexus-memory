Title: Nexus Memory – sub-10ms persistent memory for AI agents (pure SQLite, zero external services)

URL: https://github.com/chuf-China/nexus-memory

Nexus Memory is a cross-session persistent memory system for AI coding agents. It stores knowledge entries with per-domain scoring and automatic lifecycle management.

Key technical decisions:
- Pure SQLite + FTS5 for full-text search — measured 3.5ms average on 50-query benchmark (10.9ms including first-hit cross-encoder scoring); no network hop
- 3-tier knowledge architecture: Observation (0.30-0.50) → Belief (0.50-0.85) → Fact (0.85+), with automatic promotion on repeated patterns and degradation on inactivity
- Correction handling via write-time conflict detection: when a fact is corrected, the old entry's confidence drops 0.30 and the new entry promotes (natural淘汰, not a growing correction list)
- 13 built-in threat patterns (6 prompt injection + 4 SQL injection + 3 XSS), with context-scope detection on the system prompt read path
- Zero external services — SQLite + FTS5 core; optional fastembed/Ollama backends only for semantic search (not required for retrieval)

Comparison with agentmemory (npm, iii engine) and mem0 (Python, Qdrant-backed): Nexus wins on search speed, memory quality transparency, and security. Loses on agent integration breadth (agentmemory supports 20+ platforms).

Written in Python, MIT licensed.
