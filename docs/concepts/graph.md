# 知识图谱

EntityGraph 在 SQLite 上构建轻量级知识图谱，支持实体关系存储和图算法分析。

## 实体与关系

```python
from nexus.graph import EntityGraph

eg = EntityGraph(conn)

# 自动从文本抽取实体并建边
eg.extract_and_link(entry_id=1, content="Python uses Django")
```

**实体类型（8 类）：**
1. Person — 张三、Alice
2. Organization — Google、开发团队
3. Technology — Python、PostgreSQL
4. Project — Nexus、Hermes Agent
5. Location — 北京、us-east-1
6. Concept — REST API、SQL injection
7. Document — README.md、RFC 2822
8. Event — sprint planning、incident #1234

## 图遍历

```python
# BFS 遍历（优先使用邻接缓存）
neighbors = eg.traverse("Python", max_depth=2, min_weight=0.5)
# → [{"entity": "Django", "relation_type": "USES", "depth": 1, "weight": 1.0}]

# 最短路径
path = eg.find_path("Python", "PostgreSQL")
# → ["Python", "Django", "PostgreSQL"]
```

## 邻接缓存

预计算 depth 1-3 的邻居，查询 O(1)：

```python
eg.update_adjacency_cache("Python", max_depth=3)
eg.rebuild_adjacency_cache(max_depth=3)  # 全量重建
```

## 图算法

```python
from nexus.algorithms import GraphAlgorithms

ga = GraphAlgorithms(conn)

# PageRank
top = ga.pagerank(top_k=10)

# 社区检测（Louvain）
communities = ga.detect_communities()

# 中心性
centrality = ga.degree_centrality(top_k=10)
betweenness = ga.betweenness_centrality(top_k=10)

# 连通分量
components = ga.connected_components()
```
