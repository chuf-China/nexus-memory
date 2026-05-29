# 检索策略

Nexus 采用四路融合检索，结合不同检索策略的优势。

## 四路融合

```
Query → ┬→ FTS5 全文搜索 ─────→ ┐
        ├→ HNSW 向量搜索 ────→ ├→ 加权融合 → Cross-encoder 重排 → 结果
        ├→ 知识图谱遍历 ────→ ┘
        └→ 结构化事实搜索 ──→
```

## 检索策略对比

| 策略 | 优势 | 适用场景 |
|------|------|----------|
| FTS5 | 精确匹配、高性能 | 关键词查询、ID 查询 |
| 向量 | 语义理解 | 同义表达、模糊描述 |
| 图谱 | 关系推理 | 实体关联、路径查询 |
| 事实 | 结构化精确 | 版本、配置、属性查询 |

## 意图分类

系统自动识别查询意图：

- **exact** — 包含精确实体名或数字
- **semantic** — 自然语言描述
- **relational** — 询问关系或连接
- **auto** — 无法判断时均匀分配

## Cross-encoder 重排

两阶段检索：粗排（四路融合）→ 精排（cross-encoder）

```python
from nexus.embedder import Reranker

reranker = Reranker()
reranked = reranker.rerank(query, candidates, top_k=5)
```

模型优先级：
1. LoCoMo 微调模型（如有）
2. cross-encoder/ms-marco-MiniLM-L-6-v2
3. BAAI/bge-reranker-v2-m3
4. score_only 降级

## 代词消解

```python
from nexus.extract import resolve_pronouns

resolve_pronouns("张三来了。他说方案可行。")
# → "张三来了。张三说方案可行。"

resolve_pronouns("Alice went to the store. She bought milk.")
# → "Alice went to the store. Alice bought milk."
```
