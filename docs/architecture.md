# Nexus Memory 架构文档

## 概述

Nexus Memory 是一个为 AI Agent 设计的跨会话持久化记忆系统。它使用 SQLite 作为存储后端，提供本地优先、零外部依赖的知识管理能力。

## 设计原则

1. **本地优先**：所有数据存储在本地 SQLite 数据库
2. **零依赖**：核心功能无需 LLM 或外部服务
3. **高性能**：FTS5 全文索引，毫秒级检索
4. **可扩展**：模块化设计，支持插件扩展
5. **安全**：输入验证、SQL 注入防护

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                      Client Layer                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Python   │  │ REST API │  │   CLI    │  │   MCP    │   │
│  │   SDK    │  │  Server  │  │  Tool    │  │  Server  │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘   │
│       │              │              │              │         │
└───────┼──────────────┼──────────────┼──────────────┼─────────┘
        │              │              │              │
        ▼              ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Core Engine                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                    NexusCore                         │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐          │  │
│  │  │  Write   │  │  Search  │  │ Feedback │          │  │
│  │  └──────────┘  └──────────┘  └──────────┘          │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Extract  │  │  Belief  │  │ Evolve   │  │  Miner   │   │
│  │  Layer   │  │  Network │  │  Engine  │  │  Module  │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────────┘
        │              │              │              │
        ▼              ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Storage Layer                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                    SQLite + FTS5                     │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐          │  │
│  │  │knowledge │  │  belief  │  │  events  │          │  │
│  │  │  table   │  │  table   │  │  table   │          │  │
│  │  └──────────┘  └──────────┘  └──────────┘          │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │  HNSW    │  │ NetworkX │  │  Local   │                  │
│  │  Index   │  │  Graph   │  │ Storage  │                  │
│  └──────────┘  └──────────┘  └──────────┘                  │
└─────────────────────────────────────────────────────────────┘
```

## 核心组件

### 1. NexusCore（核心引擎）

主入口点，协调所有子系统。

**职责：**
- 接收客户端请求
- 调用子系统处理
- 返回结果

**主要方法：**
- `write()`：写入知识
- `search()`：搜索知识
- `feedback()`：提交反馈
- `consolidate()`：整合会话
- `system_prompt_block()`：生成系统提示

### 2. Extract Layer（提取层）

从原始文本中提取结构化知识。

**功能：**
- 文本分词
- 实体识别
- 关系提取
- 置信度评估

**实现：**
- 正则表达式（默认）
- LLM 辅助（可选）

### 3. Belief Network（信念网络）

管理知识的生命周期。

**三层知识架构：**

| 层级 | 置信度范围 | 行为 |
|------|-----------|------|
| Observation | 0.30 - 0.50 | 原始信号，首次出现 |
| Belief | 0.70 - 0.85 | 多次确认，新兴模式 |
| Fact | 0.85+ | 高置信度，持久知识 |

**自动机制：**
- **升级**：相同模式 ≥3 次 OR 用户确认
- **降级**：48h 未使用 → -0.05，被纠正 → -0.30
- **归档**：置信度 < 0.30 → 归档

### 4. Evolve Engine（进化引擎）

知识的自动维护和优化。

**功能：**
- 知识老化
- 重复检测
- 冲突解决
- 知识整合

### 5. Miner Module（挖掘模块）

从对话中自动提取知识。

**策略：**
- 关键词提取
- 模式匹配
- 上下文分析

## 数据模型

### knowledge 表

```sql
CREATE TABLE knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source TEXT DEFAULT 'unknown',
    confidence REAL DEFAULT 0.5,
    domain TEXT DEFAULT 'general',
    user_id TEXT DEFAULT 'default',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    access_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMP,
    is_archived BOOLEAN DEFAULT 0
);

-- FTS5 全文索引
CREATE VIRTUAL TABLE knowledge_fts USING fts5(
    content,
    content=knowledge,
    content_rowid=id
);
```

### belief 表

```sql
CREATE TABLE belief (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_id INTEGER NOT NULL,
    pattern TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_count INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (knowledge_id) REFERENCES knowledge(id)
);
```

### events 表

```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    knowledge_id INTEGER,
    payload TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (knowledge_id) REFERENCES knowledge(id)
);
```

## 搜索架构

### 4 路搜索融合

```
┌─────────────────────────────────────────────────────────┐
│                   Search Query                          │
└───────────────┬─────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────┐
│              Search Coordinator                         │
└───┬───────────┬───────────┬───────────┬─────────────────┘
    │           │           │           │
    ▼           ▼           ▼           ▼
┌───────┐  ┌───────┐  ┌───────┐  ┌───────┐
│ FTS5  │  │Vector │  │Graph  │  │Cross  │
│Search │  │Search │  │Query  │  │Encoder│
│(20ms) │  │(50ms) │  │(10ms) │  │(30ms) │
└───┬───┘  └───┬───┘  └───┬───┘  └───┬───┘
    │          │          │          │
    ▼          ▼          ▼          ▼
┌─────────────────────────────────────────────────────────┐
│              Result Merger & Ranker                     │
└─────────────────────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────┐
│              Ranked Results                             │
└─────────────────────────────────────────────────────────┘
```

### 搜索策略

1. **FTS5 全文搜索**
   - SQLite 原生全文索引
   - 支持中文分词
   - 延迟：~20ms

2. **向量搜索**（可选）
   - HNSW 近似最近邻
   - fastembed 嵌入
   - 延迟：~50ms

3. **图查询**
   - NetworkX 邻接遍历
   - 关系推理
   - 延迟：~10ms

4. **交叉编码器重排序**
   - 最终相关性评分
   - 延迟：~30ms

## 安全架构

### 输入验证

```
┌─────────────────────────────────────────────────────────┐
│                   User Input                            │
└───────────────┬─────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────┐
│              Input Validator                            │
│  ┌──────────────────────────────────────────────────┐  │
│  │  1. SQL Injection Check                          │  │
│  │  2. XSS Check                                    │  │
│  │  3. Length Check                                 │  │
│  │  4. Encoding Check                               │  │
│  └──────────────────────────────────────────────────┘  │
└───────────────┬─────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────┐
│              Sanitized Input                            │
└─────────────────────────────────────────────────────────┘
```

### API 认证

```
┌─────────────────────────────────────────────────────────┐
│                   API Request                           │
└───────────────┬─────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────┐
│              API Key Validator                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │  1. Extract X-API-Key header                     │  │
│  │  2. Validate key format                          │  │
│  │  3. Check key permissions                        │  │
│  │  4. Rate limiting                                │  │
│  └──────────────────────────────────────────────────┘  │
└───────────────┬─────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────┐
│              Authenticated Request                      │
└─────────────────────────────────────────────────────────┘
```

## 部署架构

### 单机部署

```
┌─────────────────────────────────────────────────────────┐
│                    Server                               │
│  ┌──────────────────────────────────────────────────┐  │
│  │                 Nexus Memory                     │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐      │  │
│  │  │ REST API │  │   CLI    │  │ Python   │      │  │
│  │  │  Server  │  │  Tool    │  │   SDK    │      │  │
│  │  └──────────┘  └──────────┘  └──────────┘      │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │                 SQLite Database                  │  │
│  │              /data/nexus.db                      │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### Docker 部署

```yaml
version: '3.8'

services:
  nexus-memory:
    image: nexus-memory:latest
    ports:
      - "8000:8000"
    volumes:
      - nexus-data:/data
    environment:
      - NEXUS_DB_PATH=/data/nexus.db
      - NEXUS_API_KEY=your_api_key_here
    command: python src/api_server.py --host 0.0.0.0 --port 8000

volumes:
  nexus-data:
```

## 性能优化

### 1. 批量写入

```python
# 使用事务批量插入
with nexus.transaction():
    for entry in entries:
        nexus.write(entry)
```

### 2. 连接池

```python
# 复用数据库连接
nexus = NexusCore(db_path, pool_size=10)
```

### 3. 缓存

```python
# 启用查询缓存
nexus = NexusCore(db_path, cache_size=1000)
```

### 4. 异步 I/O

```python
# 使用异步 API
import asyncio

async def main():
    nexus = AsyncNexusCore(db_path)
    results = await nexus.search("query")
```

## 扩展点

### 1. 自定义提取器

```python
class CustomExtractor:
    def extract(self, text):
        # 自定义提取逻辑
        return extracted_knowledge

nexus.set_extractor(CustomExtractor())
```

### 2. 自定义存储后端

```python
class PostgreSQLBackend:
    def write(self, knowledge):
        # PostgreSQL 写入逻辑
        pass

nexus.set_backend(PostgreSQLBackend())
```

### 3. 自定义搜索策略

```python
class CustomSearchStrategy:
    def search(self, query, limit):
        # 自定义搜索逻辑
        pass

nexus.set_search_strategy(CustomSearchStrategy())
```

## 未来演进

### Phase 3：分布式支持
- PostgreSQL 后端
- 读写分离
- 数据分片

### Phase 4：企业级功能
- 审计日志
- 合规报告
- SSO 集成

### Phase 5：AI 增强
- 自动知识图谱构建
- 智能冲突解决
- 预测性知识推荐

---

**最后更新**: 2026-06-01
**版本**: v0.2.0
