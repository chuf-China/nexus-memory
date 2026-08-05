[![CI](https://github.com/chuf-China/nexus-memory/actions/workflows/nexus-ci.yml/badge.svg)](https://github.com/chuf-China/nexus-memory/actions/workflows/nexus-ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](https://github.com/chuf-China/nexus-memory/tree/main/tests)

[English](README.md)

# Nexus 记忆系统

可植入任何 AI Agent 的跨会话持久化记忆系统。即插即用设计，兼容任意 Agent 框架。

## 核心特性

- **三层认知架构**：Observation（观察）→ Belief（信念）→ Fact（事实），自动晋升/降级
- **六域评分系统**：新鲜度 / 重要性 / 访问频率 / 关联度 / 置信度 / 反馈分
- **混合检索引擎**：FTS5 全文检索 + 向量语义检索 + 图关系检索
- **安全防御系统**：13 类威胁模式（提示注入 / SQL 注入 / XSS），context 场景检测 + 通用检测两级
- **自进化机制**：知识自动归类、免疫规则、审计日志

## 性能指标

| 指标 | 数值 |
|------|------|
| 检索延迟 | 0.10ms（FTS5，2000 条语料实测）；0.58ms（hybrid 融合） |
| LLM 依赖 | 零（检索纯本地） |
| 运行环境 | 纯本地 |

## 快速开始

```python
from src.nexus_core import NexusCore

# 初始化
nexus = NexusCore("nexus.db")

# 写入知识
nexus.write("用户偏好简洁回答", source_session_id="conversation", initial_confidence=0.9)

# 检索知识
results = nexus.search("用户喜欢什么样的回答风格？", limit=5)

# 注入到 Agent 的 system prompt
prompt_block = nexus.system_prompt_block()
```

## 集成到你的 Agent

### 作为独立模块使用

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
                         source_session_id="conversation")
        
        return response
```

### 注入到 System Prompt

```python
system_prompt = f"""
你是智能助手。

{nexus.system_prompt_block()}

请根据以上记忆信息回答用户问题。
"""
```

## 目录结构

```
nexus-memory/
├── src/
│   ├── nexus_core.py          # 核心引擎（组合以下 mixin）
│   ├── nexus_core_write.py    # 写入 / 信念 / 冲突检测 mixin
│   ├── nexus_core_stats.py    # 统计 / 合并 / system prompt mixin
│   ├── nexus_core_search_ext.py # 混合检索 mixin
│   ├── nexus_core_db.py       # 数据库初始化与 schema mixin
│   ├── nexus_core_audit.py    # 审计 / 时间感知 mixin
│   ├── nexus_core_session.py  # 跨会话身份 mixin
│   ├── nexus_core_snapshot.py # 快照 mixin
│   ├── security.py            # 威胁扫描、API key、限流
│   ├── nexus_drive.py         # 数据持久化层
│   ├── nexus_extract.py       # 知识提取器
│   ├── nexus_search.py        # 增强召回（扩展、否定）
│   ├── nexus_embedder.py      # 向量嵌入（可选）
│   ├── nexus_hnsw.py          # HNSW 索引（可选）
│   ├── nexus_graph.py         # 图关系存储
│   ├── nexus_belief.py        # 信念网络（三层晋升）
│   ├── nexus_constitution.py  # 安全防御系统
│   ├── nexus_evolve.py        # 自进化机制（老化 + 合并）
│   ├── nexus_miner.py         # 知识挖掘
│   ├── nexus_cli.py           # 命令行工具
│   ├── nexus_local.py         # 本地存储
│   └── nexus_utils.py         # 工具函数
├── tests/
│   ├── test_all_modules.py    # 模块集成测试
│   ├── test_core_extended.py  # 扩展核心测试
│   ├── test_security_scan.py  # 威胁扫描测试
│   ├── test_benchmark_v2.py   # 性能基准测试
│   └── test_*.py              # 核心 / 工具 / 基准测试
├── docs/
│   └── architecture.md        # 架构文档
├── pyproject.toml             # 安装配置
└── README_CN.md               # 本文件
```

## 安全防御

Nexus 内置 13 类威胁模式检测（`src/security.py`）：

- **提示注入 / 记忆投毒**（context 作用域）：忽略指令、角色替换、系统提示泄露、base64 编码指令等中英文模式
- **SQL 注入 / XSS**（通用作用域）：SQL 关键词、注释符、引号转义、script 标签、事件属性

两级作用域控制：
- `context`：记忆注入 system prompt 前逐条扫描，命中即替换为 `[BLOCKED: ...]`
- 通用：输入侧 SQL 注入 / XSS 技术性检测

## 性能基准

| 操作 | 延迟（平均） | 说明 |
|------|------|------|
| FTS5 精确检索 | 0.10ms | SQLite 全文索引，2000 条语料实测（修复前 11ms，110x；结果 ≤ limit 时跳过模型重排） |
| hybrid 融合检索 | 0.58ms | FTS + 向量 + 图多路召回合并重排（2000 条语料实测） |
| 向量语义检索 | 23.4ms | 需 fastembed 模型，无模型时回退 FTS |
| 图关系查询 | 8.6ms | 实体关系遍历 |
| 时间感知检索 | 19.7ms | 时间词解析 + 多跳 |

## 依赖

- Python 3.9+
- SQLite 3.38+（FTS5 支持）
- numpy（向量计算）
- 无外部服务依赖，纯本地运行

## 安装

```bash
git clone https://github.com/chuf-China/nexus-memory.git
cd nexus-memory
pip install -e .
```

## 运行测试

```bash
python -m pytest tests/
```

## 许可证

MIT License

## 作者

chuf-China

## 致谢

基于 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的记忆系统架构设计。
