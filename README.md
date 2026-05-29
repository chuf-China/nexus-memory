# Nexus Memory System

一个可植入任何 AI Agent 的跨会话持久化记忆系统。

## 核心特性

- **三层知识架构**：Memory（短期）→ Session（中期）→ Nexus（长期）
- **六域评分系统**：新鲜度/重要性/访问频率/关联度/置信度/反馈分
- **混合检索引擎**：FTS5 全文检索（20ms）+ 向量语义检索 + 图关系检索
- **安全防御系统**：30+ 威胁模式检测，三级作用域控制
- **自进化机制**：知识自动归类、免疫规则、审计日志

## 性能

- 检索响应：20ms（原版 90s + LLM 调用）
- 提速倍数：4500x
- 成本：零 LLM 调用

## 快速开始

```python
from src.nexus_core import NexusCore

# 初始化
nexus = NexusCore("nexus.db")

# 写入知识
nexus.write("用户偏好简洁回答", source="conversation", confidence=0.9)

# 检索知识
results = nexus.search("用户喜欢什么样的回答风格", limit=5)

# 获取系统提示词块（注入到 Agent 的 system prompt）
prompt_block = nexus.system_prompt_block()
```

## 目录结构

```
nexus-memory/
├── src/
│   ├── nexus_core.py        # 核心引擎（2448行）
│   ├── nexus_drive.py       # 数据持久化层
│   ├── nexus_extract.py     # 知识提取器
│   ├── nexus_search.py      # 混合检索引擎
│   ├── nexus_embedder.py    # 向量嵌入
│   ├── nexus_hnsw.py        # HNSW 索引
│   ├── nexus_graph.py       # 图关系存储
│   ├── nexus_belief.py      # 信念网络
│   ├── nexus_constitution.py # 安全防御系统
│   ├── nexus_evolve.py      # 自进化机制
│   ├── nexus_miner.py       # 知识挖掘
│   ├── nexus_cli.py         # 命令行工具
│   ├── nexus_local.py       # 本地存储
│   └── nexus_utils.py       # 工具函数
├── tests/
│   ├── test_nexus_core.py   # 核心测试
│   └── test_nexus_benchmark.py # 性能基准
├── docs/
│   └── architecture.md      # 架构文档
├── setup.py                 # 安装配置
└── README.md                # 本文件
```

## 集成到你的 Agent

### 1. 作为独立模块使用

```python
from src.nexus_core import NexusCore

class YourAgent:
    def __init__(self):
        self.memory = NexusCore("agent_memory.db")
    
    def chat(self, user_input):
        # 检索相关记忆
        context = self.memory.search(user_input, limit=3)
        
        # 构建 prompt
        prompt = f"相关记忆：{context}\n用户输入：{user_input}"
        
        # 调用 LLM
        response = self.llm.generate(prompt)
        
        # 保存对话到记忆
        self.memory.write(f"用户：{user_input}\n助手：{response}", 
                         source="conversation")
        
        return response
```

### 2. 注入到 System Prompt

```python
# 在 Agent 的 system prompt 中注入记忆
system_prompt = f"""
你是智能助手。

{nexus.system_prompt_block()}

请根据以上记忆信息回答用户问题。
"""
```

### 3. 使用 Hooks 自动管理

```python
# 注册生命周期钩子
nexus.register_hook("pre_llm_call", lambda ctx: ctx.update({
    "alerts": nexus.get_alerts(),
    "temporal": nexus.search_temporal(ctx["query"]),
    "history": nexus.get_history(ctx["session_id"])
}))

nexus.register_hook("session_end", lambda ctx: {
    nexus.consolidate(ctx["session_id"]),
    nexus.knowledge_snapshot()
})
```

## 安全防御

Nexus 内置 30+ 威胁模式检测：

- **注入攻击**：Prompt injection、角色劫持、系统提示泄露
- **数据外泄**：编码绕过、隐蔽通道、C2 通信
- **反取证**：日志篡改、时间戳伪造、证据销毁

三级作用域控制：
- `all`：所有知识都扫描
- `context`：仅扫描上下文相关知识
- `strict`：严格模式，最高安全级别

## 性能基准

| 操作 | 响应时间 | 说明 |
|------|---------|------|
| FTS5 检索 | 20ms | SQLite 全文索引 |
| 向量检索 | 50ms | HNSW 近似最近邻 |
| 图关系查询 | 10ms | 邻接表遍历 |
| 写入知识 | 5ms | WAL 模式批量写入 |
| 知识整合 | 100ms | 合并去重 + 评分更新 |

## 依赖

- Python 3.9+
- SQLite 3.38+（FTS5 支持）
- numpy（向量计算）
- 无外部服务依赖，纯本地运行

## 许可证

MIT License

## 作者

chuf-China

## 致谢

基于 Hermes Agent 的记忆系统架构设计。
