#!/usr/bin/env python3
"""test_api_server.py — REST API 与 Web UI 端点集成测试

回归覆盖：外部评估报告指出的旧 schema 引用（FROM knowledge → unified_knowledge）
与旧 API 签名（write(source=, confidence=, domain=)）。
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

from src.nexus_core import NexusCore


def _setup_db():
    """创建临时数据库并写入一条知识，返回 db_path。"""
    f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    f.close()
    nc = NexusCore(f.name)
    nc.write("Python 版本是 3.12，Docker 使用 containerd 运行时",
             source_session_id="test_setup")
    nc.close()
    return f.name


def _cleanup(path):
    os.unlink(path)
    for s in ["-wal", "-shm"]:
        try:
            os.unlink(path + s)
        except FileNotFoundError:
            pass


def test_api_health_and_stats():
    """GET /health 与 GET /stats 不应再查询不存在的 knowledge 表。"""
    db = _setup_db()
    os.environ["NEXUS_DB_PATH"] = db
    try:
        from src import api_server
        with TestClient(api_server.app) as client:
            r = client.get("/health")
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["status"] == "healthy"
            assert data["total_entries"] >= 1

            r = client.get("/stats")
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["total_entries"] >= 1
            assert data["by_source"].get("test_setup", 0) >= 1
            assert isinstance(data["by_domain"], dict)
    finally:
        os.environ.pop("NEXUS_DB_PATH", None)
        _cleanup(db)


def test_api_write_and_search():
    """POST /knowledge（source_session_id 对齐）与 /knowledge/search 响应映射。"""
    db = _setup_db()
    os.environ["NEXUS_DB_PATH"] = db
    try:
        from src import api_server
        with TestClient(api_server.app) as client:
            r = client.post("/knowledge", json={
                "content": "Redis 默认端口是 6379",
                "source": "apitest",
                "confidence": 0.8,
            })
            assert r.status_code == 200, r.text
            assert r.json()["source"] == "apitest"

            r = client.get("/knowledge/search", params={"query": "Redis"})
            assert r.status_code == 200, r.text
            results = r.json()
            assert results, "搜索应返回结果"
            assert isinstance(results[0]["domain"], str)
    finally:
        os.environ.pop("NEXUS_DB_PATH", None)
        _cleanup(db)


def test_web_ui_stats_and_search():
    """Web UI /api/stats 与 /api/search 不应查询旧表/传不存在的参数。"""
    db = _setup_db()
    os.environ["NEXUS_DB_PATH"] = db
    try:
        from src import web_ui
        with TestClient(web_ui.app) as client:
            r = client.get("/api/stats")
            assert r.status_code == 200, r.text
            assert r.json()["total_entries"] >= 1

            r = client.get("/api/search", params={"q": "Python"})
            assert r.status_code == 200, r.text
            assert r.json()["results"], "搜索应返回结果"

            # domain 过滤路径（原实现传入不存在的 domain_filter 参数）
            r = client.get("/api/search", params={"q": "Python", "domain": "workflow"})
            assert r.status_code == 200, r.text
    finally:
        os.environ.pop("NEXUS_DB_PATH", None)
        _cleanup(db)


def test_web_ui_write():
    """Web UI POST /api/knowledge 应使用真实 write() 签名。"""
    db = _setup_db()
    os.environ["NEXUS_DB_PATH"] = db
    try:
        from src import web_ui
        with TestClient(web_ui.app) as client:
            r = client.post("/api/knowledge", json={
                "content": "Web UI 写入的知识",
                "source": "webui_test",
                "confidence": 0.8,
            })
            assert r.status_code == 200, r.text
            assert r.json().get("success")
    finally:
        os.environ.pop("NEXUS_DB_PATH", None)
        _cleanup(db)
