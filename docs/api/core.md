# NexusCore API

核心存储引擎，管理 SQLite 数据库的读写和检索。

## 初始化

```python
from nexus.core import NexusCore

nc = NexusCore(db_path="~/.hermes/data/nexus/nexus.db")
```

## write()

写入一条知识。

```python
result = nc.write(
    content="Python 使用 type hints 提高代码可读性",
    user_id="default",
    source_session_id="",
    event_time=None,
    initial_confidence=0.40,
    skip_conflict_detection=False,
)
```

**参数：**
| 参数 | 类型 | 说明 |
|------|------|------|
| content | str | 知识内容（必填，≥5 字符） |
| user_id | str | 用户 ID，默认 "default" |
| initial_confidence | float | 初始置信度，默认 0.40 |
| skip_conflict_detection | bool | 跳过冲突检测 |

**返回：**
```python
{"success": True, "action": "created", "id": 1, "layer": "instant"}
# 或
{"success": False, "error": "Content too short"}
```

**自动行为：**
- 内容去重（match_hash）
- FTS5 索引更新
- 实体图谱构建
- 事实抽取与存储
- 实体消歧
- 信念初始化

## search()

多策略搜索。

```python
results = nc.search(
    query="Python 数据库",
    user_id="default",
    limit=5,
    mode="hybrid",  # fts | semantic | graph | hybrid
)
```

**搜索模式：**
| 模式 | 说明 |
|------|------|
| fts | FTS5 全文搜索 |
| semantic | HNSW 向量搜索 |
| graph | 知识图谱遍历 |
| hybrid | 四路融合（推荐） |

## search_temporal()

时间感知搜索。

```python
results = nc.search_temporal(
    query="价格变化",
    at_time="2026-04-15T00:00:00Z",
    user_id="default",
    limit=5,
)
```

## get_by_id()

按 ID 获取单条知识。

```python
entry = nc.get_by_id(entry_id=42)
```

## delete()

删除知识条目。

```python
nc.delete(entry_id=42, user_id="default")
```
