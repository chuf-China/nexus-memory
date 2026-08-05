# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-05-29

### Added
- 3-layer knowledge architecture (Memory → Session → Nexus)
- 6-domain scoring system (Freshness/Importance/Frequency/Relevance/Confidence/Feedback)
- Hybrid search engine (FTS5 + Vector + Graph)
- Threat pattern detection with 3-tier scope control (更正：实际 16 类模式 — 6 提示注入 + 7 SQLi + 3 XSS)
- Self-evolution mechanism with auto-classification and immune rules
- CLI tool for direct interaction
- Comprehensive test suite
- Bilingual documentation (English + Chinese)

### Performance
- Search latency: 20ms (FTS5), 50ms (Vector), 10ms (Graph)
- 4500x speedup vs LLM-based solutions
- Zero external service dependencies

> 更正（2026-08-06）：上述延迟与提速数字发布时未实测。50 查询基准实测：
> FTS5 10.9ms avg（P95 28ms）、向量 23.4ms、图 8.6ms、时间感知 19.7ms；
> 稳态（warmup 后）FTS5 3.5ms。4500x 无对比基线，已撤销。

### Security
- Prompt injection detection
- Data exfiltration prevention
- Anti-forensics protection

> 更正（2026-08-06）：Data exfiltration prevention 与 Anti-forensics protection 发布时未实现，
> 代码中无对应功能。实际为：16 类威胁模式（6 提示注入 + 7 SQLi + 3 XSS），
> context/input 两级作用域，system prompt 读取侧拦截。

## [Unreleased]

### Planned
- Web UI dashboard
- REST API server
- More embedding models support
- Production deployment guide
- LangChain integration adapter
- Performance benchmarks documentation
