<div align="center">

# 🧠 Nexus Memory

### Cross-Session Persistent Memory for AI Agents

**Your agent remembers everything. Across sessions. Across restarts. Forever.**

[![CI](https://github.com/chuf-China/nexus-memory/actions/workflows/python-package.yml/badge.svg)](https://github.com/chuf-China/nexus-memory/actions/workflows/python-package.yml)
[![PyPI](https://img.shields.io/pypi/v/nexus-memory?color=CB3837&label=PyPI&logo=pypi)](https://pypi.org/project/nexus-memory/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

<br/>

![Search Latency](https://img.shields.io/badge/Search_Latency-20ms-brightgreen?style=for-the-badge)
![Speedup](https://img.shields.io/badge/Speedup-4500x-FF6B35?style=for-the-badge)
![Threat Patterns](https://img.shields.io/badge/Threat_Patterns-30+-red?style=for-the-badge)
![LLM Dependency](https://img.shields.io/badge/LLM_Dependency-Zero-brightgreen?style=for-the-badge)

[Install](#install) · [Quick Start](#quick-start) · [vs Competitors](#vs-competitors) · [Architecture](#architecture) · [API](#api) · [中文](README_CN.md)

</div>

---

## The Problem

Every AI coding agent starts from zero. You explain your project, your preferences, your stack — again and again. Context windows fill up. Token costs explode. The agent never learns.

## The Solution

Nexus Memory gives your agent a persistent brain. It remembers facts, preferences, corrections, and context across sessions — with **zero LLM calls**, **20ms retrieval**, and **automatic knowledge promotion**.

```python
from src.nexus_core import NexusCore

nexus = NexusCore("nexus.db")

# One line to remember
nexus.write("User prefers Python type hints", source="conversation", confidence=0.9)

# One line to recall
results = nexus.search("What coding style does the user prefer?", limit=5)

# One line to inject into any agent
system_prompt = f"You are helpful.\n{nexus.system_prompt_block()}"
```

## Install

```bash
pip install nexus-memory
```

Or from source:

```bash
git clone https://github.com/chuf-China/nexus-memory.git
cd nexus-memory
pip install -e .
```

## Quick Start

### Standalone (any agent)

```python
from src.nexus_core import NexusCore

nexus = NexusCore("agent_memory.db")

class YourAgent:
    def __init__(self):
        self.memory = NexusCore("agent_memory.db")

    def chat(self, user_input):
        # Retrieve relevant memories (20ms)
        context = self.memory.search(user_input, limit=3)
        # Build prompt with memory
        prompt = f"Memories: {context}\nUser: {user_input}"
        response = self.llm.generate(prompt)
        # Auto-save conversation
        self.memory.write(f"Q: {user_input}\nA: {response}", source="conversation")
        return response
```

### Lifecycle Hooks

```python
nexus.register_hook("pre_llm_call", lambda ctx: ctx.update({
    "alerts": nexus.get_alerts(),
    "temporal": nexus.search_temporal(ctx["query"]),
    "history": nexus.get_history(ctx["session_id"])
}))

nexus.register_hook("session_end", lambda ctx: {
    nexus.consolidate(ctx["session_id"]),
    nexus.knowledge_snapshot()
})
```

### CLI

```bash
nexus-memory status          # Show DB stats
nexus-memory search "query"  # Search knowledge
nexus-memory export          # Export to JSON
nexus-memory benchmark       # Run performance test
```

---

## vs Competitors

| Feature | Nexus Memory | [agentmemory](https://github.com/rohitg00/agentmemory) | [mem0](https://github.com/mem0ai/mem0) |
|---------|:---:|:---:|:---:|
| **Search Latency** | **20ms** | Unknown | ~100ms |
| **LLM Dependency** | **Zero** | Uses iii engine | Requires LLM |
| **Knowledge Layers** | **3-tier auto** | Claimed | Flat |
| **Correction Handling** | **SPO conflict + belief degradation** | Unknown | Basic overwrite |
| **Threat Detection** | **30+ patterns** | None | None |
| **Graph Relations** | **Yes (NetworkX)** | Unknown | No |
| **Vector Search** | **HNSW + fastembed** | Yes | Yes |
| **Full-Text Search** | **FTS5** | Yes | Yes |
| **Auto Extraction** | **Dual-channel (regex + LLM)** | Hooks only | LLM only |
| **Memory Aging** | **48h decay + auto-archive** | Unknown | None |
| **Scope Control** | **3-tier security** | None | None |
| **External DB Required** | **No** | No | Yes (Qdrant/Redis) |
| **Language** | **Python** | JavaScript | Python |
| **License** | **MIT** | Unknown | Apache 2.0 |

### Why Nexus Wins on Memory Quality

**Agentmemory** is a great product with broad agent support (20+ integrations). But it treats memory as a black box inside the iii engine. You can't see how knowledge is stored, promoted, or corrected.

**Nexus Memory** is transparent and precise:

- **SPO Triplets**: Facts stored as Subject-Predicate-Object with confidence scores, not flat text
- **Belief Network**: Knowledge auto-promotes from Observation → Belief → Fact based on evidence
- **Correction Intelligence**: When you say "that's wrong", the old fact degrades AND the new fact promotes — a natural淘汰 system, not a permanent correction list that pollutes context
- **Write-time Merging**: 5 strategies (exact_dup, fuzzy_dup, complement, contradict, new) prevent knowledge bloat

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Agent / LLM                       │
│              (Claude Code, Cursor, etc.)             │
└──────────────┬──────────────────────┬───────────────┘
               │ search()             │ write()
               ▼                      ▼
┌─────────────────────────────────────────────────────┐
│               Nexus Core Engine                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ Extract  │ │  Search  │ │  Belief  │           │
│  │ (dual-   │ │ (4-way   │ │ (3-tier  │           │
│  │  channel)│ │  fusion) │ │  promo)  │           │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘           │
│       │            │            │                   │
│  ┌────▼────────────▼────────────▼────┐             │
│  │        SQLite + FTS5 + WAL        │             │
│  │   HNSW Vectors │ NetworkX Graph   │             │
│  └───────────────────────────────────┘             │
│                                                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │Constitu- │ │  Evolve  │ │  Miner   │           │
│  │tion (30+ │ │ (auto-   │ │ (know-   │           │
│  │ threats) │ │  aging)  │ │  ledge)  │           │
│  └──────────┘ └──────────┘ └──────────┘           │
└─────────────────────────────────────────────────────┘
```

### 3-Layer Knowledge Architecture

| Layer | Confidence | Behavior |
|-------|-----------|----------|
| **Observation** | 0.30 - 0.50 | Raw signal, first appearance |
| **Belief** | 0.70 - 0.85 | Multiple confirmations, emerging pattern |
| **Fact** | 0.85 + | High confidence, persistent knowledge |

- Auto-promote: Same pattern ≥3 times OR user confirmation
- Auto-degrade: 48h unused → -0.05, corrected → -0.30
- Auto-archive: confidence < 0.30 → archived

### 6-Domain Scoring

Every knowledge entry is scored on:
**Freshness** · **Importance** · **Frequency** · **Relevance** · **Confidence** · **Feedback**

### 4-Way Search Fusion

1. **FTS5 Full-Text** (20ms) — SQLite native full-text index
2. **Vector Search** (50ms) — HNSW approximate nearest neighbor with fastembed
3. **Graph Query** (10ms) — NetworkX adjacency traversal
4. **Cross-Encoder Reranking** — Final relevance scoring

---

## Performance Benchmarks

| Operation | Latency | Notes |
|-----------|---------|-------|
| FTS5 Search | 20ms | SQLite full-text index |
| Vector Search | 50ms | HNSW approximate nearest neighbor |
| Graph Query | 10ms | Adjacency list traversal |
| Write Knowledge | 5ms | WAL mode batch write |
| Consolidation | 100ms | Merge + dedup + score update |

**4500x faster** than LLM-based memory retrieval. Zero external service dependencies.

---

## Security Defense

30+ built-in threat patterns:

- **Injection**: Prompt injection, role hijacking, system prompt leakage
- **Exfiltration**: Encoding bypass, covert channels, C2 communication
- **Anti-forensics**: Log tampering, timestamp forgery, evidence destruction

3-tier scope control:
- `all` — Scan all knowledge
- `context` — Scan only context-related knowledge
- `strict` — Strict mode, highest security level

---

## Directory Structure

```
nexus-memory/
├── src/
│   ├── nexus_core.py          # Core engine (2448 lines)
│   ├── nexus_drive.py         # Data persistence layer
│   ├── nexus_extract.py       # Knowledge extractor (dual-channel)
│   ├── nexus_search.py        # Hybrid search engine (4-way fusion)
│   ├── nexus_embedder.py      # Vector embedding (fastembed)
│   ├── nexus_hnsw.py          # HNSW index
│   ├── nexus_graph.py         # Graph relationship (NetworkX)
│   ├── nexus_belief.py        # Belief network (3-tier promotion)
│   ├── nexus_constitution.py  # Security defense (30+ patterns)
│   ├── nexus_evolve.py        # Self-evolution (aging + consolidation)
│   ├── nexus_miner.py         # Knowledge mining
│   ├── nexus_cli.py           # CLI tool
│   ├── nexus_local.py         # Local storage
│   └── nexus_utils.py         # Utility functions
├── tests/
│   ├── test_nexus_core.py     # Core tests
│   └── test_nexus_benchmark.py # Performance benchmarks
├── docs/
│   └── architecture.md        # Architecture documentation
├── pyproject.toml             # PyPI packaging
├── setup.py                   # Installation config
└── README.md                  # This file
```

## Dependencies

- Python 3.9+
- SQLite 3.38+ (FTS5 support)
- numpy (vector computation)
- **No external service dependencies** — pure local execution

## API

```python
from src.nexus_core import NexusCore

nexus = NexusCore("nexus.db")

# Write
nexus.write(content, source="conversation", confidence=0.9, domain="workflow")

# Search
results = nexus.search(query, limit=5, domain_filter=None)

# System prompt injection
block = nexus.system_prompt_block()

# Consolidate session
nexus.consolidate(session_id)

# Knowledge snapshot
nexus.knowledge_snapshot()

# Register lifecycle hooks
nexus.register_hook(event_name, callback)

# Get alerts
alerts = nexus.get_alerts()

# Temporal search
results = nexus.search_temporal(query)

# Session history
history = nexus.get_history(session_id)
```

## Run Tests

```bash
python -m pytest tests/ -v
```

## License

MIT License

## Acknowledgments

Built as the memory backbone of [Hermes Agent](https://github.com/NousResearch/hermes-agent). Inspired by Karpathy's LLM Wiki pattern — extended with confidence scoring, lifecycle management, knowledge graphs, and hybrid search.

---

<div align="center">

**If Nexus Memory helps your agent, give it a ⭐**

</div>
