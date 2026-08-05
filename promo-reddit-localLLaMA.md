Title: I built a persistent memory system for AI agents with sub-10ms local search

Hey r/LocalLLaMA,

I've been frustrated with AI coding agents forgetting everything between sessions. Tried mem0 (too slow, needs external DB), tried agentmemory (opaque internals, JS-only).

So I built **Nexus Memory** — a Python-native persistent memory engine for AI agents.

**What makes it different:**

- **Sub-10ms search** — pure SQLite + FTS5, no external services (measured 3.5ms avg FTS5, 50-query benchmark)
- **3-tier knowledge architecture** — Observation → Belief → Fact, with automatic promotion/degradation
- **Structured scoring** — every entry carries per-domain scores (identity/workflow/behavior/strategy/rule/raw_fact), not flat text
- **Smart corrections** — when you correct the agent, old knowledge degrades (confidence -0.30) AND new knowledge promotes (natural淘汰, not a growing correction list)
- **16 security patterns** — prompt injection, SQL injection, XSS detection, with context-scope scanning on the system prompt read path
- **Zero external services** — retrieval is fully local; fastembed/Ollama are optional add-ons for semantic search

**Quick comparison with agentmemory:**

| | Nexus Memory | agentmemory |
|---|---|---|
| Search latency | 3.5ms (measured, 50 queries) | Unknown |
| Knowledge format | Structured entries + domain scores | Black box (iii engine) |
| Correction handling | Belief degradation + conflict detection | Unknown |
| Security | 16 patterns (prompt injection/SQLi/XSS) | None |
| Language | Python | JavaScript |

agentmemory has broader agent support (20+ platforms) and better marketing. But if you care about memory quality and transparency, give Nexus a try.

```bash
git clone https://github.com/chuf-China/nexus-memory
cd nexus-memory && pip install -e .
```

GitHub: https://github.com/chuf-China/nexus-memory

Feedback welcome — especially from anyone who's built their own agent memory system.
