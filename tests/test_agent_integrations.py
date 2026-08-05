#!/usr/bin/env python3
"""test_agent_integrations.py — Agent 框架集成回归测试

回归覆盖：外部评估报告指出的 write() 旧参数
（source=/confidence=/domain= → source_session_id=/initial_confidence=）。
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.nexus_core import NexusCore
from src.agent_integrations import BaseIntegration, get_integration


def _make_nc():
    f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    f.close()
    return NexusCore(f.name), f.name


def _cleanup(path):
    os.unlink(path)
    for s in ["-wal", "-shm"]:
        try:
            os.unlink(path + s)
        except FileNotFoundError:
            pass


def test_base_save_context():
    """BaseIntegration.save_context 应使用 source_session_id 参数。"""
    nc, path = _make_nc()
    try:
        intg = BaseIntegration(nc)
        intg.save_context("hello world", "hi there")
        results = nc.search("hello", limit=5)
        assert results, "save_context 写入的内容应可检索到"
    finally:
        nc.close()
        _cleanup(path)


def test_langchain_save_context():
    """LangChain 集成（继承 BaseIntegration）save_context 不崩溃。"""
    nc, path = _make_nc()
    try:
        intg = get_integration("langchain", nc)
        intg.save_context("user input", "assistant output")
        results = nc.search("user input", limit=5)
        assert results, "LangChain save_context 应写入可检索内容"
    finally:
        nc.close()
        _cleanup(path)


def test_autogen_add_message():
    """AutoGen 集成 add() 不崩溃。"""
    nc, path = _make_nc()
    try:
        mem = get_integration("autogen", nc).get_memory()
        mem.add({"role": "user", "content": "autogen test"})
        assert len(mem.get()) == 1
    finally:
        nc.close()
        _cleanup(path)


def test_claude_code_remember():
    """Claude Code 集成 remember() 不崩溃（原实现传不存在的 domain 参数）。"""
    nc, path = _make_nc()
    try:
        mem = get_integration("claude_code", nc).get_memory()
        mem.remember("用户偏好 TypeScript", "general")
        recall = mem.recall("TypeScript", limit=5)
        assert recall, "remember 写入的内容应可 recall"
    finally:
        nc.close()
        _cleanup(path)


def test_custom_agent_write():
    """自定义集成 write() 透传正常。"""
    nc, path = _make_nc()
    try:
        mem = get_integration("custom", nc).get_memory()
        r = mem.write("custom agent 记忆")
        assert r.get("success")
    finally:
        nc.close()
        _cleanup(path)
