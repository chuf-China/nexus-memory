# 结构化事实

FactStore 以 subject-predicate-object 三元组形式存储结构化事实。

## 基本用法

```python
from nexus.facts import FactStore

fs = FactStore(conn)

# 添加事实
fs.add("PostgreSQL", "version", "16", confidence=0.95)
fs.add("Python", "uses", "SQLite", confidence=0.9)

# 查询
facts = fs.query(subject="PostgreSQL")
facts = fs.query(predicate="version")
```

## 冲突检测

同一 subject+predicate 只保留最新值：

```python
fs.add("Python", "version", "3.11")  # 添加
fs.add("Python", "version", "3.12")  # 自动取代 3.11

active = fs.query(subject="Python", predicate="version")
# → [{"object": "3.12", "confidence": 0.95}]
```

**冲突类型（LLM 分类）：**
| 类型 | 动作 | 说明 |
|------|------|------|
| CONTRADICTS | supersede | 直接矛盾 |
| UPDATES | supersede | 新值更新旧值 |
| REFINES | add | 新值更精确 |
| DUPLICATE | merge | 重复，合并置信度 |
| COMPATIBLE | add | 不冲突的不同谓词 |

## 事实抽取

```python
from nexus.facts import FactExtractor

fe = FactExtractor(conn)
fe.extract("PostgreSQL uses JSONB and supports full-text search")
# 自动存储提取的 SPO 三元组
```

## 历史追溯

```python
hist = fs.history("Python", "version")
# → [{"object": "3.12", "superseded_by": None},
#    {"object": "3.11", "superseded_by": 2}]
```

## 导出为图边

```python
edges = fs.to_graph_edges()
# → [("PostgreSQL", "version", "16"), ...]
```
