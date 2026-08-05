# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-05-29

### Added
- 3-layer knowledge architecture (Memory → Session → Nexus)
- 6-domain scoring system (Freshness/Importance/Frequency/Relevance/Confidence/Feedback)
- Hybrid search engine (FTS5 + Vector + Graph)
- Threat pattern detection with 3-tier scope control (更正：实际 13 类模式 — 6 提示注入 + 4 SQLi + 3 XSS，2 层作用域 context/input)
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
> 代码中无对应功能。实际为：13 类威胁模式（6 提示注入 + 4 SQLi + 3 XSS），
> context/input 两级作用域，system prompt 读取侧拦截。

## [Unreleased]

### Fixed (2026-08-06 底层审查 5 轮修复)
- **FTS 索引每次启动重建**：`_ensure_fts_integrity` 用 active 行数对比 FTS 总行数，
  任何 superseded/archived 条目都会误判失同步 → 每次 init 全量 rebuild。
  改为全量行数对比。
- **rebuild 破坏中文索引**：`'rebuild'` 命令用原始文本重新分词（中文整句变单
  token），叠加重复 rowid INSERT 静默产生未定义结果，条目从搜索中失联。
  改为 DROP + CREATE + 全量重插（按 `segment_fts` 分词）。
- **merge 后 FTS 失同步**：`nexus_evolve.py` 引用不存在的 `nexus_core._segment_fts`
  （ImportError 被静默吞掉），fuzzy_dup/complement 合并后条目对 FTS 搜索不可见。
  改为从 `nexus_utils` 导入。
- **HNSW 索引永久过期**：磁盘索引加载后从不校验数据量，新写入的 embedding 永远
  不可被语义搜索命中。增加行数指纹过期检测，build() 自动重建。
- 冗余的双 `@staticmethod` 装饰器。

### Changed
- **FTS5 查询改 OR 语义**：原实现把 unigram+bigram 分词串直接喂 MATCH（隐式 AND），
  ≥4-5 字中文查询召回为 0，检索退化为 LIKE 全表扫描。新增 `fts_or_query()`，
  单字 CJK token 在存在多字 token 时剔除（避免 '发' 命中所有含 '发达' 的文档）。
- **LIKE 兜底改原文连续片段**：不再用分词产物（unigram 全命中 / bigram 非原文
  子串），改用连续 CJK run + ASCII 词。
- **重排器按需调用**：`Reranker.rerank()` 新增 `use_cross_encoder` 参数——单路 FTS
  模式结果 ≤ limit（无物可裁剪）时跳过 cross-encoder 推理（~8ms/次，原占单路
  搜索 99% 耗时），hybrid 多路融合保留模型重排。
- **搜索路径批量写**：`_update_domain_scores` 单条 UPDATE IN(...) 替代逐条；
  `record_domain_hit` 支持 `commit=False`，域命中循环后统一提交一次。

### Performance（2000 条语料实测）
- FTS 模式搜索：~11ms → **0.10ms**（110x）
- hybrid 模式搜索：~11ms → **0.58ms**
- HNSW 稳态 build：每次磁盘加载 → **0.004ms/次**（行数指纹 + 进程内对象缓存）

### Planned
- Web UI dashboard
- REST API server
- More embedding models support
- Production deployment guide
- LangChain integration adapter
- Performance benchmarks documentation
