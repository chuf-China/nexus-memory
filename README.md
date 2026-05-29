[![CI](https://github.com/chuf-China/nexus-memory/actions/workflows/python-package.yml/badge.svg)](https://github.com/chuf-China/nexus-memory/actions/workflows/python-package.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](https://github.com/chuf-China/nexus-memory/tree/main/tests)

[中文版](README_CN.md)

# Nexus Memory System

A cross-session persistent memory system for AI Agents. Plug-and-play design, compatible with any Agent framework.

## Key Features

- **3-Layer Knowledge Architecture**: Memory (short-term) → Session (mid-term) → Nexus (long-term)
- **6-Domain Scoring System**: Freshness / Importance / Frequency / Relevance / Confidence / Feedback
- **Hybrid Search Engine**: FTS5 full-text (20ms) + Vector semantic + Graph relationship
- **Security Defense**: 30+ threat pattern detection, 3-tier scope control
- **Self-Evolution**: Auto-classification, immune rules, audit logging

## Performance

| Metric | Value |
|--------|-------|
| Search Latency | 20ms |
| Speedup | 4500x vs LLM-based |
| LLM Dependency | Zero |
| Runtime | Pure local |

## Quick Start

```python
from src.nexus_core import NexusCore

# Initialize
nexus = NexusCore("nexus.db")

# Write knowledge
nexus.write("User prefers concise answers", source="conversation", confidence=0.9)

# Search knowledge
results = nexus.search("What answer style does the user prefer?", limit=5)

# Inject into Agent's system prompt
prompt_block = nexus.system_prompt_block()
```

## Integrate Into Your Agent

### As Standalone Module

```python
from src.nexus_core import NexusCore

class YourAgent:
    def __init__(self):
        self.memory = NexusCore("agent_memory.db")
    
    def chat(self, user_input):
        # Retrieve relevant memories
        context = self.memory.search(user_input, limit=3)
        
        # Build prompt
        prompt = f"Related memories: {context}\nUser input: {user_input}"
        
        # Call LLM
        response = self.llm.generate(prompt)
        
        # Save conversation to memory
        self.memory.write(f"User: {user_input}\nAssistant: {response}", 
                         source="conversation")
        
        return response
```

### Inject into System Prompt

```python
system_prompt = f"""
You are an intelligent assistant.

{nexus.system_prompt_block()}

Please answer based on the above memory information.
"""
```

### Lifecycle Hooks

```python
# Register hooks for automatic memory management
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

## Directory Structure

```
nexus-memory/
├── src/
│   ├── nexus_core.py        # Core engine (2448 lines)
│   ├── nexus_drive.py       # Data persistence layer
│   ├── nexus_extract.py     # Knowledge extractor
│   ├── nexus_search.py      # Hybrid search engine
│   ├── nexus_embedder.py    # Vector embedding
│   ├── nexus_hnsw.py        # HNSW index
│   ├── nexus_graph.py       # Graph relationship storage
│   ├── nexus_belief.py      # Belief network
│   ├── nexus_constitution.py # Security defense system
│   ├── nexus_evolve.py      # Self-evolution mechanism
│   ├── nexus_miner.py       # Knowledge mining
│   ├── nexus_cli.py         # CLI tool
│   ├── nexus_local.py       # Local storage
│   └── nexus_utils.py       # Utility functions
├── tests/
│   ├── test_nexus_core.py   # Core tests
│   └── test_nexus_benchmark.py # Performance benchmarks
├── docs/
│   └── architecture.md      # Architecture documentation
├── setup.py                 # Installation config
└── README.md                # This file
```

## Security Defense

Nexus includes 30+ threat pattern detection:

- **Injection**: Prompt injection, role hijacking, system prompt leakage
- **Exfiltration**: Encoding bypass, covert channels, C2 communication
- **Anti-forensics**: Log tampering, timestamp forgery, evidence destruction

3-tier scope control:
- `all`: Scan all knowledge
- `context`: Scan only context-related knowledge
- `strict`: Strict mode, highest security level

## Performance Benchmarks

| Operation | Latency | Notes |
|-----------|---------|-------|
| FTS5 Search | 20ms | SQLite full-text index |
| Vector Search | 50ms | HNSW approximate nearest neighbor |
| Graph Query | 10ms | Adjacency list traversal |
| Write Knowledge | 5ms | WAL mode batch write |
| Consolidation | 100ms | Merge + dedup + score update |

## Dependencies

- Python 3.9+
- SQLite 3.38+ (FTS5 support)
- numpy (vector computation)
- No external service dependencies, pure local execution

## Installation

```bash
git clone https://github.com/chuf-China/nexus-memory.git
cd nexus-memory
pip install -e .
```

## Run Tests

```bash
python -m pytest tests/
```

## License

MIT License

## Author

chuf-China

## Acknowledgments

Based on the memory system architecture design of [Hermes Agent](https://github.com/NousResearch/hermes-agent).
