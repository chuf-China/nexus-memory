#!/usr/bin/env python3
"""api_server.py — Nexus Memory REST API

用法:
  python api_server.py
  python api_server.py --host 0.0.0.0 --port 8000
  python api_server.py --db /path/to/nexus.db

API 端点:
  GET  /health              - 健康检查
  POST /knowledge           - 写入知识
  GET  /knowledge/search    - 搜索知识
  GET  /knowledge/{id}      - 获取知识
  GET  /stats               - 统计信息
  POST /consolidate         - 整合用户知识
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
    import uvicorn
except ImportError:
    print("Error: FastAPI and uvicorn are required for REST API.")
    print("Install with: pip install fastapi uvicorn")
    sys.exit(1)

from src.nexus_core import NexusCore


# ============ Models ============

class KnowledgeWrite(BaseModel):
    """写入知识请求"""
    content: str = Field(..., description="知识内容")
    source: str = Field(default="api", description="来源")
    confidence: float = Field(default=0.8, ge=0, le=1, description="置信度 (0-1)")
    domain: str = Field(default="general", description="知识域")
    user_id: str = Field(default="default", description="用户 ID")


class KnowledgeSearch(BaseModel):
    """搜索知识请求"""
    query: str = Field(..., description="搜索查询")
    limit: int = Field(default=5, ge=1, le=100, description="返回数量")
    domain_filter: Optional[str] = Field(default=None, description="域过滤")
    user_id: str = Field(default="default", description="用户 ID")


class ConsolidateRequest(BaseModel):
    """整合请求（按 user_id 整合该用户的知识）"""
    user_id: str = Field(default="default", description="用户 ID")


class FeedbackRequest(BaseModel):
    """反馈请求"""
    knowledge_id: int = Field(..., description="知识 ID")
    feedback_type: str = Field(..., description="反馈类型 (positive/negative)")
    user_id: str = Field(default="default", description="用户 ID")


class KnowledgeResponse(BaseModel):
    """知识响应"""
    id: int
    content: str
    source: str
    confidence: float
    domain: str
    created_at: str
    score: Optional[float] = None


class StatsResponse(BaseModel):
    """统计响应"""
    total_entries: int
    by_source: Dict[str, int]
    by_domain: Dict[str, int]
    db_size_mb: float


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str
    db_exists: bool
    db_readable: bool
    fts5_available: bool
    write_permission: bool
    total_entries: int


# ============ App ============

app = FastAPI(
    title="Nexus Memory API",
    description="Cross-session persistent memory for AI Agents",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global NexusCore instance
nexus: Optional[NexusCore] = None
db_path: str = ""


@app.on_event("startup")
async def startup():
    """初始化数据库连接"""
    global nexus, db_path
    db_path = os.environ.get("NEXUS_DB_PATH", str(Path.home() / ".hermes" / "data" / "nexus.db"))
    nexus = NexusCore(db_path)
    print(f"✓ Connected to {db_path}")


@app.on_event("shutdown")
async def shutdown():
    """关闭数据库连接"""
    if nexus:
        nexus.close()
        print("✓ Database connection closed")


# ============ Endpoints ============

@app.get("/health", response_model=HealthResponse, tags=["系统"])
async def health_check():
    """健康检查"""
    import sqlite3

    checks = {
        "status": "healthy",
        "db_exists": Path(db_path).exists(),
        "db_readable": False,
        "fts5_available": False,
        "write_permission": False,
        "total_entries": 0,
    }

    if checks["db_exists"]:
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("SELECT 1")
            checks["db_readable"] = True

            # Check FTS5
            try:
                conn.execute("SELECT fts5()")
                checks["fts5_available"] = True
            except Exception:
                pass

            # Count entries
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM knowledge")
            checks["total_entries"] = cursor.fetchone()[0]

            conn.close()
        except Exception:
            pass

    # Check write permission
    try:
        test_file = Path(db_path).parent / ".test_write"
        test_file.write_text("test")
        test_file.unlink()
        checks["write_permission"] = True
    except Exception:
        pass

    if not all([checks["db_exists"], checks["db_readable"], checks["write_permission"]]):
        checks["status"] = "unhealthy"

    return HealthResponse(**checks)


@app.post("/knowledge", response_model=KnowledgeResponse, tags=["知识"])
async def write_knowledge(knowledge: KnowledgeWrite):
    """写入知识"""
    if not nexus:
        raise HTTPException(status_code=503, detail="Database not initialized")

    result = nexus.write(
        content=knowledge.content,
        user_id=knowledge.user_id,
        initial_confidence=knowledge.confidence,
    )

    if not result.get("success"):
        raise HTTPException(status_code=500, detail="Failed to write knowledge")

    return KnowledgeResponse(
        id=result["id"],
        content=knowledge.content,
        source=knowledge.source,
        confidence=knowledge.confidence,
        domain=knowledge.domain,
        created_at=result.get("created_at", ""),
    )


@app.get("/knowledge/search", response_model=List[KnowledgeResponse], tags=["知识"])
async def search_knowledge(
    query: str = Query(..., description="搜索查询"),
    limit: int = Query(default=5, ge=1, le=100, description="返回数量"),
    domain: Optional[str] = Query(default=None, description="域过滤"),
    user_id: str = Query(default="default", description="用户 ID"),
):
    """搜索知识"""
    if not nexus:
        raise HTTPException(status_code=503, detail="Database not initialized")

    if domain:
        results = nexus.search_by_domain(domain=domain, user_id=user_id, limit=limit)
    else:
        results = nexus.search(query=query, user_id=user_id, limit=limit)

    return [
        KnowledgeResponse(
            id=r.get("id", 0),
            content=r.get("content", ""),
            source=r.get("source", ""),
            confidence=r.get("confidence", 0),
            domain=r.get("domain", "general"),
            created_at=r.get("created_at", ""),
            score=r.get("score"),
        )
        for r in results
    ]


@app.get("/knowledge/{knowledge_id}", response_model=KnowledgeResponse, tags=["知识"])
async def get_knowledge(knowledge_id: int):
    """获取知识"""
    if not nexus:
        raise HTTPException(status_code=503, detail="Database not initialized")

    # 查询数据库（unified_knowledge: 无 source/confidence/domain 列，
    # source 用 source_session_id，confidence 按层映射）
    import json
    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, content, domain_scores, layer, created_at, source_session_id
           FROM unified_knowledge WHERE id = ?""",
        (knowledge_id,)
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Knowledge not found")

    try:
        domain_scores = json.loads(row[2]) if row[2] else {}
    except (json.JSONDecodeError, TypeError):
        domain_scores = {}
    domain = max(domain_scores, key=domain_scores.get) if domain_scores else "general"
    layer_conf = {"instant": 0.40, "candidate": 0.70, "consolidated": 0.90}

    return KnowledgeResponse(
        id=row[0],
        content=row[1],
        source=row[5] or "unknown",
        confidence=layer_conf.get(row[3], 0.40),
        domain=domain,
        created_at=row[4] or "",
    )


@app.get("/stats", response_model=StatsResponse, tags=["系统"])
async def get_stats():
    """获取统计信息"""
    if not nexus:
        raise HTTPException(status_code=503, detail="Database not initialized")

    import sqlite3

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 总条目数
    cursor.execute("SELECT COUNT(*) FROM knowledge")
    total_entries = cursor.fetchone()[0]

    # 按来源统计
    cursor.execute("SELECT source, COUNT(*) FROM knowledge GROUP BY source")
    by_source = dict(cursor.fetchall())

    # 按域统计
    cursor.execute("SELECT domain, COUNT(*) FROM knowledge GROUP BY domain")
    by_domain = dict(cursor.fetchall())

    conn.close()

    # 数据库大小
    db_size_mb = Path(db_path).stat().st_size / 1024 / 1024

    return StatsResponse(
        total_entries=total_entries,
        by_source=by_source,
        by_domain=by_domain,
        db_size_mb=round(db_size_mb, 2),
    )


@app.post("/consolidate", tags=["知识"])
async def consolidate_session(request: ConsolidateRequest):
    """整合用户知识"""
    if not nexus:
        raise HTTPException(status_code=503, detail="Database not initialized")

    nexus.consolidate(request.user_id)
    return {"status": "success", "user_id": request.user_id}


@app.post("/feedback", tags=["知识"])
async def submit_feedback(request: FeedbackRequest):
    """提交反馈"""
    if not nexus:
        raise HTTPException(status_code=503, detail="Database not initialized")

    nexus.feedback(request.knowledge_id, request.feedback_type, user_id=request.user_id)
    return {"status": "success", "knowledge_id": request.knowledge_id}


@app.get("/system-prompt", tags=["系统"])
async def get_system_prompt(
    user_id: str = Query(default="default", description="用户 ID"),
):
    """获取系统提示"""
    if not nexus:
        raise HTTPException(status_code=503, detail="Database not initialized")

    block = nexus.system_prompt_block(user_id=user_id)
    return {"system_prompt": block}


# ============ Main ============

def main():
    """启动服务器"""
    import argparse

    parser = argparse.ArgumentParser(description="Nexus Memory REST API")
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址 (默认: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="端口 (默认: 8000)")
    parser.add_argument("--db", default=None, help="数据库路径")

    args = parser.parse_args()

    if args.db:
        os.environ["NEXUS_DB_PATH"] = args.db

    print(f"Starting Nexus Memory API on {args.host}:{args.port}")
    print(f"Docs: http://{args.host}:{args.port}/docs")

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
