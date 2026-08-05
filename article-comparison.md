# AI Agent Memory Showdown: Nexus Memory vs agentmemory vs mem0

*2026-05-31 | 8 min read*

Every AI coding agent faces the same problem: **they forget everything between sessions.** You explain your project architecture, your coding style, your preferences — and next session, it's gone.

Three projects are trying to solve this. I evaluated all of them. (Note: only Nexus Memory is measured here — see Round 1 for the benchmark scope; third-party latency figures are not independently verified.)

## The Contenders

| | **Nexus Memory** | **agentmemory** | **mem0** |
|---|---|---|---|
| **Language** | Python | JavaScript (Node.js) | Python |
| **Storage** | SQLite (local) | iii engine (local) | Qdrant/Redis (remote) |
| **Install** | `git clone + pip install -e .` | `npm install -g @agentmemory/agentmemory` | `pip install mem0ai` |
| **Approach** | Embedded engine | MCP server + hooks | API service |
| **License** | MIT | Unknown | Apache 2.0 |

## Round 1: Search Speed

Measured on Nexus Memory's internal benchmark (50 queries, 50 seed entries, WSL2 — full suite in `tests/test_benchmark_v2.py`):

| System | FTS5/Full-text | Vector | Combined |
|--------|:-:|:-:|:-:|
| **Nexus Memory** | **10.9ms** avg (P95 28ms) | 23.4ms avg | — |
| agentmemory | Not disclosed | Not disclosed | Not disclosed |
| mem0 | N/A | ~100ms (unverified estimate) | ~150ms (unverified estimate) |

Nexus wins on raw speed because it's pure SQLite — no network hop, no external DB. Caveat: no head-to-head test with identical data was run; third-party figures are estimates from their public docs, not measured here.

## Round 2: Memory Quality

This is where it gets interesting.

### How knowledge is stored

**Nexus Memory** stores entries with per-domain scores:
```
content: "User prefers Python type hints"
domain_scores: {"workflow": 0.85, "behavior": 0.72, ...}
layer: candidate          # instant → candidate → consolidated
confidence: 0.70          # Observation 0.30-0.50 / Belief 0.50-0.85 / Fact 0.85+
source_session_id: conversation
status: active            # active / superseded / archived
```

**agentmemory** stores knowledge inside the iii engine. The format is not publicly documented.

**mem0** stores flat text with metadata:
```json
{"content": "User prefers Python type hints", "category": "coding", "importance": 8}
```

**Winner: Nexus** — structured domain scoring enables conflict detection and version tracking.

### How corrections work

When you say "that's wrong, I actually prefer Go":

**Nexus Memory**:
1. Detects correction signal (6 regex patterns)
2. Old fact: `confidence -= 0.30` (degrades)
3. New fact: `confidence = 0.9` (promotes)
4. SPO conflict detection: same subject+predicate → old fact marked as superseded
5. Net effect: natural淘汰, no context pollution

**agentmemory**: Correction mechanism not publicly documented.

**mem0**: Overwrites the old entry. No degradation tracking.

**Winner: Nexus** — the "degrade old + promote new" pattern prevents the correction list from growing infinitely.

### How knowledge evolves

**Nexus Memory** has automatic lifecycle:
- Observation (0.30-0.50) → raw signal, first seen
- Belief (0.50-0.85) → confirmed by repetition/feedback
- Fact (0.85+) → high confidence, persistent
- Auto-degrade: unused for 48h → -0.05
- Auto-archive: confidence < 0.30

**agentmemory**: Claims "lifecycle" but implementation not disclosed.

**mem0**: No lifecycle. Entries persist until manually deleted.

**Winner: Nexus** — automatic aging prevents stale knowledge from polluting context.

## Round 3: Security

| Feature | Nexus Memory | agentmemory | mem0 |
|---------|:-:|:-:|:-:|
| Prompt injection detection | ✅ 13 patterns (6 prompt injection + 4 SQLi + 3 XSS) | ❌ | ❌ |
| Role hijacking detection | ✅ | ❌ | ❌ |
| Scope control (2-tier) | ✅ context/input | ❌ | ❌ |
| Audit logging | ✅ | ❌ | ❌ |
| Anti-forensics detection | ✅ | ❌ | ❌ |

**Winner: Nexus** — by a mile. This matters if your agent handles sensitive data.

## Round 4: Agent Integration

| Agent | Nexus Memory | agentmemory | mem0 |
|-------|:-:|:-:|:-:|
| Claude Code | ✅ hooks + MCP | ✅ native + MCP | ✅ API |
| Cursor | ✅ MCP | ✅ MCP | ✅ API |
| Codex CLI | ✅ hooks | ✅ native + MCP | ❌ |
| Hermes | ✅ native (deep) | ✅ native + MCP | ❌ |
| Gemini CLI | ✅ MCP | ✅ MCP | ❌ |
| OpenCode | ✅ hooks | ✅ hooks + MCP | ❌ |
| Any Python agent | ✅ direct import | ❌ (JS only) | ✅ API |
| Any JS agent | ❌ | ✅ direct import | ❌ |

**Winner: agentmemory** — broader integration, especially for JS agents. But Nexus wins for Python agents.

## Round 5: External Dependencies

| | Nexus Memory | agentmemory | mem0 |
|---|:-:|:-:|:-:|
| External DB | None | None | Qdrant/Redis |
| External LLM calls | None | iii engine (unclear) | Yes |
| Network required | No | No | Yes |
| Offline capable | ✅ | ✅ | ❌ |

**Winner: Tie** (Nexus & agentmemory) — both work fully offline.

## The Verdict

| Category | Winner |
|----------|--------|
| Search Speed | **Nexus Memory** |
| Memory Quality | **Nexus Memory** |
| Security | **Nexus Memory** |
| Agent Integration | **agentmemory** |
| Ease of Install | **agentmemory** (npm ecosystem) |
| Offline Support | **Tie** |
| Documentation | **agentmemory** |

### Choose Nexus Memory if:
- You want transparent memory quality (per-domain scoring, belief lifecycle, auto-aging)
- You need security (13 threat patterns, context-scope scanning)
- Your agent is Python-based
- You want zero external services

### Choose agentmemory if:
- You need broad agent support (20+ platforms)
- You prefer JavaScript/Node.js
- You want the easiest setup (npm install + done)
- You need MCP server mode

### Choose mem0 if:
- You need a hosted/cloud solution
- You're building a SaaS product
- You don't mind external DB dependencies

---

## Try Nexus Memory

```bash
git clone https://github.com/chuf-China/nexus-memory
cd nexus-memory && pip install -e .
```

```python
from src.nexus_core import NexusCore

nexus = NexusCore("my_agent.db")
nexus.write("User prefers dark mode", source_session_id="conversation", initial_confidence=0.9)
results = nexus.search("UI preferences", limit=5)
```

GitHub: [chuf-China/nexus-memory](https://github.com/chuf-China/nexus-memory)

---

*Disclaimer: I'm the author of Nexus Memory. Benchmarks were run on my local machine (WSL2, Ryzen 7, 32GB RAM). Your results may vary. I tried to be fair but acknowledge my bias.*
