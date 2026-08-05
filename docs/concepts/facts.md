# 结构化事实

结构化事实以 subject-predicate-object 三元组形式存储。写入 Nexus 时由
写时合并（`evolve_on_write`）自动维护"同一事实只保留最新值"的不变式。

## 基本用法

```python
from src.nexus_core import NexusCore

nexus = NexusCore("nexus.db")

# 写入事实（高初始置信度）
nexus.write("PostgreSQL 版本 16", source_session_id="facts", initial_confidence=0.95)
nexus.write("Python 使用 SQLite", source_session_id="facts", initial_confidence=0.9)

# 查询
results = nexus.search("PostgreSQL 版本", limit=5)
```

## 冲突检测

write 时自动合并/取代重复或矛盾的内容：

```python
nexus.write("Python 版本 3.11")
nexus.write("Python 版本 3.12")  # 自动取代 3.11（supersede）
```

**写时合并动作（`evolve_on_write`）：**

| 动作 | 说明 |
|------|------|
| exact_dup | 完全重复，不新增条目 |
| fuzzy_dup | 语义重复，合并到目标条目 |
| complement | 互补内容，合并到目标条目 |
| supersede | 新值取代旧值（旧条目标记 superseded） |

## 事实抽取

```python
from src.nexus_extract import extract_knowledge, extract_on_turn

# 从单条消息抽取知识
for k in extract_knowledge("PostgreSQL 使用 JSONB 并支持全文检索"):
    print(k)  # {"content": ..., "domain": ...}

# 从对话轮次抽取（含纠正检测）
extract_on_turn("PostgreSQL 是什么？", "PostgreSQL 是关系型数据库")
```

## 历史追溯

```python
history = nexus.get_history(knowledge_id)
# → [{"changed_at": ..., "note": "superseded ..."}, ...]
```

## 导出为图边

```python
from src.nexus_graph import EntityGraph

eg = EntityGraph(conn)  # conn = sqlite3 connection
edges = eg.search_by_graph("PostgreSQL")  # 实体关系遍历
```
