# Nexus — AI Agent 知识记忆系统

Nexus 是为 AI Agent 设计的知识记忆系统，提供结构化存储、多策略检索、知识图谱和记忆演化能力。

## 核心特性

- **四层记忆架构**：instant → candidate → consolidated → archived，自动老化与晋升
- **混合检索**：FTS5 全文 + HNSW 向量 + 知识图谱 + 结构化事实，四路融合
- **知识图谱**：实体抽取、关系推理、邻接缓存（深度 1-3）、NetworkX 算法
- **信念引擎**：6 域评分 + 置信度 + 时间衰减 + 反馈学习
- **MCP 服务器**：8 个工具，兼容任何 MCP 客户端
- **实体消歧**：模糊匹配 + 别名管理 + LLM 精排
- **结构化事实**：SPO 三元组 + 自动冲突检测

## 快速安装

```bash
# 从源码安装
cd ~/.hermes/nexus
pip install -e .

# Docker
docker-compose up -d
```

## 文档导航

| 章节 | 说明 |
|------|------|
| [快速开始](getting-started/quickstart.md) | 5 分钟上手 |
| [核心概念](concepts/memory.md) | 记忆系统设计 |
| [检索策略](concepts/retrieval.md) | 四路融合检索 |
| [API 参考](api/core.md) | NexusCore 接口 |
| [Docker 部署](deployment/docker.md) | 容器化部署 |
