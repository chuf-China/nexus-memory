Title: I built a persistent memory system for AI agents that runs 4500x faster than LLM-based retrieval

Hey r/LocalLLaMA,

I've been frustrated with AI coding agents forgetting everything between sessions. Tried mem0 (too slow, needs external DB), tried agentmemory (opaque internals, JS-only).

So I built **Nexus Memory** — a Python-native persistent memory engine for AI agents.

**What makes it different:**

- **20ms search** — pure SQLite + FTS5, no external services
- **3-tier knowledge architecture** — Observation → Belief → Fact, with automatic promotion/degradation
- **SPO triplets** — facts stored as Subject-Predicate-Object, not flat text
- **Smart corrections** — when you correct the agent, old knowledge degrades AND new knowledge promotes (natural淘汰, not a growing correction list)
- **30+ security patterns** — detects prompt injection, role hijacking, data exfiltration
- **Zero LLM dependency** — all retrieval is local, no API calls

**Quick comparison with agentmemory:**

| | Nexus Memory | agentmemory |
|---|---|---|
| Search latency | 20ms | Unknown |
| Knowledge format | SPO triplets | Black box (iii engine) |
| Correction handling | Belief degradation + SPO conflict | Unknown |
| Security | 30+ patterns | None |
| Language | Python | JavaScript |

agentmemory has broader agent support (20+ platforms) and better marketing. But if you care about memory quality and transparency, give Nexus a try.

```bash
pip install nexus-memory
```

GitHub: https://github.com/chuf-China/nexus-memory

Feedback welcome — especially from anyone who's built their own agent memory system.
