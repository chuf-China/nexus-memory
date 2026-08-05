<div align="center">

# 🧠 Nexus Memory

### Cross-Session Persistent Memory for AI Agents

**Local-first memory system. Zero LLM dependency for core operations.**

[![CI](https://github.com/chuf-China/nexus-memory/actions/workflows/nexus-ci.yml/badge.svg)](https://github.com/chuf-China/nexus-memory/actions/workflows/nexus-ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

[Install](#install) · [Quick Start](#quick-start) · [API](#api) · [Architecture](#architecture)

</div>

---

## The Problem

Every AI coding agent starts from zero. You explain your project, your preferences, your stack — again and again. Context windows fill up. Token costs explode. The agent never learns.

## The Solution

Nexus Memory gives your agent a persistent brain. It remembers facts, preferences, corrections, and context across sessions — with zero LLM calls for core operations.

```python
from src.nexus_core import NexusCore

nexus = NexusCore("nexus.db")

# One line to remember
nexus.write("User prefers Python type hints", source_session_id="conversation", initial_confidence=0.9)

# One line to recall
results = nexus.search("What coding style does the user prefer?", limit=5)

# One line to inject into any agent
system_prompt = f"You are helpful.\n{nexus.system_prompt_block()}"
```

## Install

From source:

```bash
git clone https://github.com/chuf-China/nexus-memory.git
cd nexus-memory
pip install -e .
```

Optional extras (defined in `pyproject.toml`): `[vector]` for fastembed/hnswlib, `[llm]` for LLM integration, `[full]` for everything.

## Quick Start

### Standalone (any agent)

```python
from src.nexus_core import NexusCore

nexus = NexusCore("agent_memory.db")

class YourAgent:
    def __init__(self):
        self.memory = NexusCore("agent_memory.db")

    def chat(self, user_input):
        # Retrieve relevant memories
        context = self.memory.search(user_input, limit=3)
        
        # Build prompt with memory
        prompt = f"Memories: {context}\nUser: {user_input}"
        response = self.llm.generate(prompt)
        
        # Auto-save conversation
        self.memory.write(f"Q: {user_input}\nA: {response}", source_session_id="conversation")
        return response
```

## CLI

```bash
nexus-memory status          # Show DB stats
nexus-memory search "query"  # Search knowledge
nexus-memory export          # Export to JSON
nexus-memory benchmark       # Run performance test
```

## API

```python
from src.nexus_core import NexusCore

nexus = NexusCore("nexus.db")

# Write
nexus.write(content, source_session_id="conversation", initial_confidence=0.9)

# Search
results = nexus.search(query, limit=5)
# Search by domain (instead of a domain_filter parameter)
results = nexus.search_by_domain(domain="workflow", limit=5)

# System prompt injection (with built-in threat scanning)
block = nexus.system_prompt_block()

# Consolidate user knowledge
nexus.consolidate(user_id="default")

# Knowledge snapshot
nexus.knowledge_snapshot()

# Get alerts
alerts = nexus.get_alerts()

# Temporal search
results = nexus.search_temporal(query)

# Knowledge version history
history = nexus.get_history(knowledge_id)
```

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
│  │ (regex   │ │ (FTS5 +  │ │ (3-tier  │           │
│  │  + LLM)  │ │  vector) │ │  promo)  │           │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘           │
│       │            │            │                   │
│  ┌────▼────────────▼────────────▼────┐             │
│  │        SQLite + FTS5 + WAL        │             │
│  │   HNSW Vectors │ NetworkX Graph   │             │
│  └───────────────────────────────────┘             │
│                                                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │Constitu- │ │  Evolve  │ │  Miner   │           │
│  │tion      │ │ (auto-   │ │ (know-   │           │
│  │(security)│ │  aging)  │ │  ledge)  │           │
│  └──────────┘ └──────────┘ └──────────┘           │
└─────────────────────────────────────────────────────┘
```

### 3-Layer Knowledge Architecture

| Layer | Confidence | Behavior |
|-------|------------|----------|
| Observation | 0.30 - 0.50 | Raw signal, first appearance |
| Belief | 0.50 - 0.85 | Multiple confirmations, emerging pattern |
| Fact | 0.85 + | High confidence, persistent knowledge |

- **Auto-promote**: Same pattern ≥3 times OR user confirmation
- **Auto-degrade**: 48h unused → -0.05, corrected → -0.30
- **Auto-archive**: confidence < 0.30 → archived

### 6-Domain Scoring

Every knowledge entry is scored on: Freshness · Importance · Frequency · Relevance · Confidence · Feedback

### Search Capabilities

- **FTS5 Full-Text** — SQLite native full-text index
- **CJK-Aware Queries** — OR-semantics matching with unigram pruning; multi-character Chinese queries recall correctly instead of silently returning zero results
- **Vector Search** — HNSW approximate nearest neighbor (optional)
- **Temporal Search** — Time-aware retrieval
- **Cross-Encoder Reranking** — Final relevance scoring (skipped when results ≤ limit, where no pruning is possible)

## Directory Structure

```
nexus-memory/
├── src/
│   ├── nexus_core.py          # Core engine (composes the mixins below)
│   ├── nexus_core_write.py    # Write / belief / conflict-detection mixin
│   ├── nexus_core_stats.py    # Stats / consolidate / system-prompt mixin
│   ├── nexus_core_search_ext.py # Hybrid search mixin
│   ├── nexus_core_db.py       # DB init & schema mixin
│   ├── nexus_core_audit.py    # Audit / temporal mixin
│   ├── nexus_core_session.py  # Cross-session identity mixin
│   ├── nexus_core_snapshot.py # Snapshot mixin
│   ├── security.py            # Threat scanning, API keys, rate limiting
│   ├── nexus_drive.py         # Data persistence layer
│   ├── nexus_extract.py       # Knowledge extractor
│   ├── nexus_search.py        # Enhanced recall (expansion, negation)
│   ├── nexus_embedder.py      # Vector embedding (optional)
│   ├── nexus_hnsw.py          # HNSW index (optional)
│   ├── nexus_graph.py         # Graph relationship
│   ├── nexus_belief.py        # Belief network (3-tier promotion)
│   ├── nexus_constitution.py  # Security defense
│   ├── nexus_evolve.py        # Self-evolution (aging + consolidation)
│   ├── nexus_miner.py         # Knowledge mining
│   ├── nexus_cli.py           # CLI tool
│   ├── nexus_local.py         # Local storage
│   └── nexus_utils.py         # Utility functions
├── tests/
│   ├── test_all_modules.py    # Module integration tests
│   ├── test_core_extended.py  # Extended core tests
│   ├── test_security_scan.py  # Threat scanning tests
│   ├── test_benchmark_v2.py   # Performance benchmarks
│   └── test_*.py              # Core / utils / benchmark tests
├── docs/
│   └── architecture.md        # Architecture documentation
├── pyproject.toml             # PyPI packaging
└── README.md                  # This file
```

## Dependencies

**Core (required):**
- Python 3.9+
- SQLite 3.38+ (FTS5 support)
- numpy

**Optional:**
- fastembed + hnswlib (vector search)
- openai (LLM integration)
- sentence-transformers (advanced embeddings)

**No external service dependencies** — pure local execution.

## Run Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ -v --cov=src --cov-report=html
```

## License

MIT License

## Acknowledgments

Built as the memory backbone of Hermes Agent. Inspired by Karpathy's LLM Wiki pattern — extended with confidence scoring, lifecycle management, knowledge graphs, and hybrid search.

---

**If Nexus Memory helps your agent, give it a ⭐**
