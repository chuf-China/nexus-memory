# Nexus Memory API 参考文档

## 概述

Nexus Memory 提供两种 API：
1. **Python SDK** - 直接在 Python 代码中使用
2. **REST API** - 通过 HTTP 调用

## Python SDK

### 初始化

```python
from src.nexus_core import NexusCore

# 使用默认数据库
nexus = NexusCore()

# 使用自定义数据库
nexus = NexusCore("/path/to/nexus.db")
```

### 写入知识

```python
result = nexus.write(
    content="User prefers Python type hints",
    source="conversation",
    confidence=0.9,
    domain="workflow",
    user_id="user123",
)

print(result)
# {
#     "success": True,
#     "id": 123,
#     "created_at": "2026-06-01T12:00:00Z",
# }
```

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| content | str | 必填 | 知识内容 |
| source | str | "conversation" | 来源 |
| confidence | float | 0.8 | 置信度 (0-1) |
| domain | str | "general" | 知识域 |
| user_id | str | "default" | 用户 ID |

### 搜索知识

```python
results = nexus.search(
    query="What coding style does the user prefer?",
    limit=5,
    domain_filter="workflow",
    user_id="user123",
)

for result in results:
    print(f"ID: {result['id']}")
    print(f"Content: {result['content']}")
    print(f"Score: {result['score']}")
```

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| query | str | 必填 | 搜索查询 |
| limit | int | 5 | 返回数量 |
| domain_filter | str | None | 域过滤 |
| user_id | str | "default" | 用户 ID |

**返回：**

```python
[
    {
        "id": 123,
        "content": "User prefers Python type hints",
        "source": "conversation",
        "confidence": 0.9,
        "domain": "workflow",
        "score": 0.95,
        "created_at": "2026-06-01T12:00:00Z",
    },
    ...
]
```

### 系统提示注入

```python
block = nexus.system_prompt_block(user_id="user123")
print(block)
# [
#   Knowledge:
#   - User prefers Python type hints (confidence: 0.9)
#   - ...
# ]
```

### 整合会话

```python
nexus.consolidate(session_id="session_123")
```

### 提交反馈

```python
nexus.feedback(
    knowledge_id=123,
    feedback_type="positive",  # or "negative"
    user_id="user123",
)
```

### 获取统计

```python
stats = nexus.get_stats()
print(stats)
# {
#     "total_entries": 1000,
#     "by_source": {"conversation": 800, "manual": 200},
#     "by_domain": {"workflow": 500, "identity": 300, "general": 200},
# }
```

### 注册钩子

```python
def pre_llm_call_hook(context):
    """在 LLM 调用前执行"""
    context["alerts"] = nexus.get_alerts()
    context["temporal"] = nexus.search_temporal(context["query"])
    return context

nexus.register_hook("pre_llm_call", pre_llm_call_hook)
```

### 关闭连接

```python
nexus.close()
```

---

## REST API

### 启动服务器

```bash
# 使用默认配置
python src/api_server.py

# 自定义配置
python src/api_server.py --host 0.0.0.0 --port 8000 --db /path/to/nexus.db
```

### API 端点

#### 健康检查

```http
GET /health
```

**响应：**

```json
{
    "status": "healthy",
    "db_exists": true,
    "db_readable": true,
    "fts5_available": true,
    "write_permission": true,
    "total_entries": 1000
}
```

#### 写入知识

```http
POST /knowledge
Content-Type: application/json

{
    "content": "User prefers Python type hints",
    "source": "api",
    "confidence": 0.9,
    "domain": "workflow",
    "user_id": "user123"
}
```

**响应：**

```json
{
    "id": 123,
    "content": "User prefers Python type hints",
    "source": "api",
    "confidence": 0.9,
    "domain": "workflow",
    "created_at": "2026-06-01T12:00:00Z"
}
```

#### 搜索知识

```http
GET /knowledge/search?query=Python&limit=5&domain=workflow&user_id=user123
```

**响应：**

```json
[
    {
        "id": 123,
        "content": "User prefers Python type hints",
        "source": "api",
        "confidence": 0.9,
        "domain": "workflow",
        "created_at": "2026-06-01T12:00:00Z",
        "score": 0.95
    }
]
```

#### 获取知识

```http
GET /knowledge/123
```

**响应：**

```json
{
    "id": 123,
    "content": "User prefers Python type hints",
    "source": "api",
    "confidence": 0.9,
    "domain": "workflow",
    "created_at": "2026-06-01T12:00:00Z"
}
```

#### 获取统计

```http
GET /stats
```

**响应：**

```json
{
    "total_entries": 1000,
    "by_source": {"api": 600, "cli": 400},
    "by_domain": {"workflow": 500, "identity": 300, "general": 200},
    "db_size_mb": 1.5
}
```

#### 整合会话

```http
POST /consolidate
Content-Type: application/json

{
    "session_id": "session_123"
}
```

#### 提交反馈

```http
POST /feedback
Content-Type: application/json

{
    "knowledge_id": 123,
    "feedback_type": "positive",
    "user_id": "user123"
}
```

#### 获取系统提示

```http
GET /system-prompt?user_id=user123
```

**响应：**

```json
{
    "system_prompt": "[\n  Knowledge:\n  - User prefers Python type hints (confidence: 0.9)\n]"
}
```

---

## 错误处理

### HTTP 状态码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 401 | 未认证 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 429 | 请求过多 |
| 500 | 服务器内部错误 |
| 503 | 服务不可用 |

### 错误响应

```json
{
    "detail": "Error message"
}
```

---

## 认证

### API Key 认证

如果启用了 API Key 认证，需要在请求头中传递：

```http
X-API-Key: nexus_xxxxxxxxxxxxxxxxxxxxxxxx
```

### 生成 API Key

```python
from src.security import APIKeyManager

manager = APIKeyManager()
key = manager.generate_key(
    name="my_app",
    permissions=["read", "write"],
)
print(key)  # nexus_xxxxxxxxxxxxxxxxxxxxxxxx
```

---

## 速率限制

默认速率限制：60 请求/分钟

响应头：

```http
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 59
X-RateLimit-Reset: 1622505600
```

---

## 示例代码

### Python SDK 完整示例

```python
from src.nexus_core import NexusCore

# 初始化
nexus = NexusCore("my_agent.db")

# 写入知识
nexus.write("User prefers dark mode", source="settings")
nexus.write("User is a Python developer", source="conversation")

# 搜索知识
results = nexus.search("What does the user prefer?", limit=3)
for r in results:
    print(r["content"])

# 系统提示注入
prompt = f"""You are a helpful assistant.

{nexus.system_prompt_block()}

User: How can I help you?"""

# 整合会话
nexus.consolidate("session_001")

# 关闭
nexus.close()
```

### REST API 完整示例

```python
import requests

BASE_URL = "http://localhost:8000"

# 写入知识
response = requests.post(f"{BASE_URL}/knowledge", json={
    "content": "User prefers dark mode",
    "source": "api",
    "confidence": 0.9,
})
print(response.json())

# 搜索知识
response = requests.get(f"{BASE_URL}/knowledge/search", params={
    "query": "What does the user prefer?",
    "limit": 5,
})
print(response.json())

# 获取统计
response = requests.get(f"{BASE_URL}/stats")
print(response.json())
```

---

## 最佳实践

1. **置信度管理**
   - 高置信度 (0.8-1.0)：用户明确确认的信息
   - 中置信度 (0.5-0.8)：从对话中推断的信息
   - 低置信度 (0-0.5)：自动提取的信息

2. **知识域划分**
   - `identity`：用户身份信息（姓名、角色、偏好）
   - `workflow`：工作流程和习惯
   - `project`：项目相关信息
   - `general`：通用知识

3. **定期整合**
   - 每个会话结束后调用 `consolidate()`
   - 定期调用 `knowledge_snapshot()` 备份

4. **反馈循环**
   - 对错误的搜索结果提交负面反馈
   - 对正确的搜索结果提交正面反馈

---

**最后更新**: 2026-06-01
**版本**: v0.2.0
