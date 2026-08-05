#!/usr/bin/env python3
"""agent_integrations.py — Agent 框架集成

支持的框架:
1. LangChain
2. LlamaIndex
3. AutoGen
4. Claude Code
5. 自定义 Agent

用法:
    from src.agent_integrations import get_integration

    # LangChain 集成
    integration = get_integration("langchain", nexus_core)
    memory = integration.get_memory()

    # LlamaIndex 集成
    integration = get_integration("llamaindex", nexus_core)
    memory = integration.get_memory()
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .nexus_core import NexusCore


class BaseIntegration:
    """集成基类"""

    def __init__(self, nexus: NexusCore):
        self.nexus = nexus

    def get_memory(self):
        """获取框架特定的内存对象"""
        raise NotImplementedError

    def save_context(self, input_text: str, output_text: str):
        """保存对话上下文"""
        self.nexus.write(
            content=f"User: {input_text}\nAssistant: {output_text}",
            source_session_id="conversation",
            initial_confidence=0.7,
        )

    def load_memory_variables(self, query: str) -> Dict:
        """加载内存变量"""
        results = self.nexus.search(query, limit=3)
        return {
            "history": "\n".join([r["content"] for r in results]),
            "relevant_memories": results,
        }


class LangChainIntegration(BaseIntegration):
    """LangChain 集成"""

    def get_memory(self):
        """获取 LangChain 兼容的内存对象"""
        try:
            from langchain.memory import BaseMemory
            from pydantic import Field
        except ImportError:
            raise ImportError("LangChain is required. Install with: pip install langchain")

        nexus = self.nexus

        class NexusMemory(BaseMemory):
            """Nexus Memory for LangChain"""

            memory_key: str = "history"
            nexus: NexusCore = Field(default_factory=lambda: nexus)

            @property
            def memory_variables(self) -> List[str]:
                return [self.memory_key]

            def load_memory_variables(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
                query = inputs.get("input", "")
                results = self.nexus.search(query, limit=3)
                history = "\n".join([r["content"] for r in results])
                return {self.memory_key: history}

            def save_context(self, inputs: Dict[str, Any], outputs: Dict[str, str]) -> None:
                input_text = inputs.get("input", "")
                output_text = outputs.get("output", "")
                self.nexus.write(
                    content=f"User: {input_text}\nAssistant: {output_text}",
                    source_session_id="langchain_conversation",
                    initial_confidence=0.7,
                )

            def clear(self) -> None:
                pass  # Nexus doesn't support clearing

        return NexusMemory(nexus=nexus)

    def get_retriever(self):
        """获取 LangChain 兼容的检索器"""
        try:
            from langchain.schema import BaseRetriever, Document
            from pydantic import Field
        except ImportError:
            raise ImportError("LangChain is required. Install with: pip install langchain")

        nexus = self.nexus

        class NexusRetriever(BaseRetriever):
            """Nexus Retriever for LangChain"""

            nexus: NexusCore = Field(default_factory=lambda: nexus)
            k: int = 5

            def _get_relevant_documents(self, query: str) -> List[Document]:
                results = self.nexus.search(query, limit=self.k)
                return [
                    Document(
                        page_content=r["content"],
                        metadata={
                            "id": r.get("id"),
                            "source": r.get("source"),
                            "confidence": r.get("confidence"),
                            "domain": r.get("domain"),
                        },
                    )
                    for r in results
                ]

        return NexusRetriever(nexus=nexus, k=5)


class LlamaIndexIntegration(BaseIntegration):
    """LlamaIndex 集成"""

    def get_memory(self):
        """获取 LlamaIndex 兼容的内存对象"""
        try:
            from llama_index.core.memory import BaseMemory
        except ImportError:
            raise ImportError("LlamaIndex is required. Install with: pip install llama-index")

        nexus = self.nexus

        class NexusMemory(BaseMemory):
            """Nexus Memory for LlamaIndex"""

            def __init__(self, nexus_core: NexusCore):
                self.nexus = nexus_core

            def get(self, key: str, default=None):
                return default

            def put(self, key: str, value: Any):
                pass

            def get_all(self) -> Dict[str, Any]:
                return {}

            def load_memory(self, query: str) -> str:
                results = self.nexus.search(query, limit=3)
                return "\n".join([r["content"] for r in results])

            def save_memory(self, key: str, memory: str):
                self.nexus.write(
                    content=memory,
                    source_session_id="llamaindex_conversation",
                    initial_confidence=0.7,
                )

            def reset(self):
                pass

        return NexusMemory(nexus)

    def get_index(self):
        """获取 LlamaIndex 兼容的索引"""
        try:
            from llama_index.core import VectorStoreIndex
            from llama_index.core.schema import BaseNode, TextNode
        except ImportError:
            raise ImportError("LlamaIndex is required. Install with: pip install llama-index")

        nexus = self.nexus

        class NexusIndex:
            """Nexus Index for LlamaIndex"""

            def __init__(self, nexus_core: NexusCore):
                self.nexus = nexus_core

            def as_query_engine(self, **kwargs):
                """返回查询引擎"""
                nexus = self.nexus

                class NexusQueryEngine:
                    def query(self, query_str: str):
                        results = nexus.search(query_str, limit=5)
                        response = "\n".join([r["content"] for r in results])
                        return type('Response', (), {'response': response})()

                return NexusQueryEngine()

            def as_retriever(self, **kwargs):
                """返回检索器"""
                nexus = self.nexus

                class NexusRetriever:
                    def retrieve(self, query_str: str):
                        results = nexus.search(query_str, limit=kwargs.get("similarity_top_k", 5))
                        return [
                            type('NodeWithScore', (), {
                                'node': type('Node', (), {'get_content': lambda: r["content"]})(),
                                'score': r.get("score", 0),
                            })()
                            for r in results
                        ]

                return NexusRetriever()

        return NexusIndex(nexus)


class AutoGenIntegration(BaseIntegration):
    """AutoGen 集成"""

    def get_memory(self):
        """获取 AutoGen 兼容的内存对象"""
        nexus = self.nexus

        class NexusMemory:
            """Nexus Memory for AutoGen"""

            def __init__(self, nexus_core: NexusCore):
                self.nexus = nexus_core
                self.messages = []

            def add(self, message: Dict):
                """添加消息"""
                self.messages.append(message)
                self.nexus.write(
                    content=json.dumps(message),
                    source_session_id="autogen_conversation",
                    initial_confidence=0.7,
                )

            def get(self, limit: int = 10) -> List[Dict]:
                """获取消息"""
                return self.messages[-limit:]

            def search(self, query: str, limit: int = 5) -> List[Dict]:
                """搜索消息"""
                results = self.nexus.search(query, limit=limit)
                return [json.loads(r["content"]) for r in results if r["content"].startswith("{")]

            def clear(self):
                """清空内存"""
                self.messages = []

        return NexusMemory(nexus)


class ClaudeCodeIntegration(BaseIntegration):
    """Claude Code 集成"""

    def get_memory(self):
        """获取 Claude Code 兼容的内存对象"""
        nexus = self.nexus

        class NexusMemory:
            """Nexus Memory for Claude Code"""

            def __init__(self, nexus_core: NexusCore):
                self.nexus = nexus_core

            def remember(self, fact: str, category: str = "general"):
                """记住事实"""
                self.nexus.write(
                    content=fact,
                    source_session_id="claude_code",
                    initial_confidence=0.9,
                )

            def recall(self, query: str, limit: int = 5) -> List[str]:
                """回忆事实"""
                results = self.nexus.search(query, limit=limit)
                return [r["content"] for r in results]

            def get_context(self, query: str) -> str:
                """获取上下文"""
                results = self.nexus.search(query, limit=3)
                if results:
                    return "Relevant memories:\n" + "\n".join([f"- {r['content']}" for r in results])
                return ""

            def get_system_prompt(self) -> str:
                """获取系统提示"""
                return self.nexus.system_prompt_block()

        return NexusMemory(nexus)


class CustomAgentIntegration(BaseIntegration):
    """自定义 Agent 集成"""

    def get_memory(self):
        """获取通用内存接口"""
        nexus = self.nexus

        class NexusMemory:
            """通用 Nexus Memory 接口"""

            def __init__(self, nexus_core: NexusCore):
                self.nexus = nexus_core

            def write(self, content: str, **kwargs) -> Dict:
                """写入知识"""
                return self.nexus.write(content, **kwargs)

            def search(self, query: str, **kwargs) -> List[Dict]:
                """搜索知识"""
                return self.nexus.search(query, **kwargs)

            def get_context(self, query: str, limit: int = 3) -> str:
                """获取上下文"""
                results = self.nexus.search(query, limit=limit)
                return "\n".join([r["content"] for r in results])

            def get_system_prompt(self) -> str:
                """获取系统提示"""
                return self.nexus.system_prompt_block()

            def consolidate(self, session_id: str):
                """整合会话"""
                self.nexus.consolidate(session_id)

        return NexusMemory(nexus)


def get_integration(framework: str, nexus: NexusCore) -> BaseIntegration:
    """获取框架集成

    Args:
        framework: 框架名称 (langchain, llamaindex, autogen, claude_code, custom)
        nexus: NexusCore 实例

    Returns:
        集成实例
    """
    integrations = {
        "langchain": LangChainIntegration,
        "llamaindex": LlamaIndexIntegration,
        "autogen": AutoGenIntegration,
        "claude_code": ClaudeCodeIntegration,
        "custom": CustomAgentIntegration,
    }

    if framework not in integrations:
        raise ValueError(f"Unsupported framework: {framework}. Supported: {list(integrations.keys())}")

    return integrations[framework](nexus)
