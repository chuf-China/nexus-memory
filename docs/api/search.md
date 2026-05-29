# 检索 API

增强检索管线，包装 NexusCore.search()，加入查询扩展、多跳检索和上下文构建。

## EnhancedSearch

```python
from nexus.search import EnhancedSearch

es = EnhancedSearch(nc)
results = es.search("数据库版本", user_id="default", limit=5)
context = es.build_context(results, max_tokens=2000, question="数据库版本")
```

## search()

```python
results = es.search(
    query="PostgreSQL 版本",
    user_id="default",
    limit=5,
    mode="hybrid",
)
```

**增强特性：**
1. **查询扩展** — 同义词替换 + 实体提取
2. **多跳检索** — 时间推理 + 实体关联
3. **否定感知** — 否定句特殊策略
4. **事实融合** — 四路检索（FTS + 向量 + 图谱 + 事实）
5. **Cross-encoder 重排** — 精确相关性排序
6. **留存感知** — 结合 retrieval_priority 重新排序

## build_context()

将搜索结果构建为 LLM 上下文。

```python
context = es.build_context(
    results=results,
    max_tokens=2000,
    question="数据库版本",
)
```

**输出格式：**
```
[1] [2026-05-28] PostgreSQL 版本是 16 (相似度: 0.92)
[2] [2026-05-27] 部署在 AWS us-east-1 (相似度: 0.85)
```

## 意图分类

系统自动识别查询意图并调整权重：

| 意图 | FTS | 向量 | 图谱 | 事实 |
|------|-----|------|------|------|
| exact（精确） | 0.6 | 0.1 | 0.1 | 0.2 |
| semantic（语义） | 0.1 | 0.6 | 0.2 | 0.1 |
| relational（关系） | 0.1 | 0.1 | 0.7 | 0.1 |
| auto（自动） | 0.33 | 0.33 | 0.33 | 0.0 |
