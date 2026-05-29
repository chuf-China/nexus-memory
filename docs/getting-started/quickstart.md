# 快速开始

5 分钟上手 Nexus 记忆系统。

## 安装

```bash
cd ~/.hermes/nexus
pip install -e .
```

## 写入第一条记忆

```python
from nexus.core import NexusCore

nc = NexusCore("~/.hermes/data/nexus/nexus.db")

# 写入知识
result = nc.write(
    content="PostgreSQL 版本是 16，部署在 AWS us-east-1",
    user_id="default"
)
print(result)  # {"success": True, "action": "created", "id": 1}
```

## 搜索记忆

```python
# FTS 全文搜索
results = nc.search("PostgreSQL 版本", mode="fts", limit=5)

# 混合搜索（推荐）
results = nc.search("数据库配置", mode="hybrid", limit=5)

for r in results:
    print(f"[{r.get('similarity', 0):.2f}] {r['content'][:80]}")
```

## 使用 MCP 服务器

```bash
# stdio 模式
python -m nexus.mcp_server

# SSE 模式
python -m nexus.mcp_server --port 8080
```

MCP 工具列表：
- `nexus_save` — 保存记忆
- `nexus_search` — 搜索记忆
- `nexus_query_fact` — 查询结构化事实
- `nexus_graph_neighbors` — 图谱邻居查询
- `nexus_graph_path` — 实体间路径
- `nexus_save_feedback` — 反馈
- `nexus_health` — 健康检查
- `nexus_stats` — 统计信息

## Docker 部署

```bash
docker-compose up -d
# 服务: nexus(8080) + prometheus(9090) + grafana(3000)
```

## 下一步

- [核心概念](../concepts/memory.md) — 了解四层记忆架构
- [检索策略](../concepts/retrieval.md) — 混合检索原理
- [API 参考](../api/core.md) — 完整接口文档
