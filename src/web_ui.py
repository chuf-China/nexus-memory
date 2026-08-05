#!/usr/bin/env python3
"""web_ui.py — Web UI 模块

功能:
1. 知识库浏览
2. 搜索界面
3. 统计仪表盘
4. 租户管理

用法:
    python web_ui.py
    python web_ui.py --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from fastapi import FastAPI, Request, Query
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
except ImportError:
    print("Error: FastAPI and Jinja2 are required for Web UI.")
    print("Install with: pip install fastapi jinja2")
    sys.exit(1)

from src.nexus_core import NexusCore


# 初始化
app = FastAPI(title="Nexus Memory Web UI", version="0.2.0")
nexus: NexusCore = None


# HTML 模板
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nexus Memory</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #00d4ff; margin-bottom: 20px; }
        h2 { color: #00d4ff; margin: 20px 0 10px; }

        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: #16213e;
            padding: 20px;
            border-radius: 10px;
            text-align: center;
        }
        .stat-value { font-size: 2em; color: #00d4ff; }
        .stat-label { color: #888; margin-top: 5px; }

        .search-box {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        .search-box input {
            flex: 1;
            padding: 12px;
            border: 1px solid #333;
            border-radius: 5px;
            background: #16213e;
            color: #eee;
            font-size: 16px;
        }
        .search-box button {
            padding: 12px 24px;
            background: #00d4ff;
            color: #000;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-weight: bold;
        }

        .results {
            background: #16213e;
            border-radius: 10px;
            padding: 20px;
        }
        .result-item {
            padding: 15px;
            border-bottom: 1px solid #333;
        }
        .result-item:last-child { border-bottom: none; }
        .result-content { font-size: 1.1em; margin-bottom: 10px; }
        .result-meta { color: #888; font-size: 0.9em; }
        .result-meta span { margin-right: 15px; }

        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 0.8em;
        }
        .badge-domain { background: #2d3436; color: #00d4ff; }
        .badge-source { background: #2d3436; color: #a29bfe; }
        .badge-confidence { background: #2d3436; color: #00b894; }

        .pagination {
            display: flex;
            justify-content: center;
            gap: 10px;
            margin-top: 20px;
        }
        .pagination button {
            padding: 8px 16px;
            background: #16213e;
            color: #eee;
            border: 1px solid #333;
            border-radius: 5px;
            cursor: pointer;
        }
        .pagination button.active { background: #00d4ff; color: #000; }

        .write-form {
            background: #16213e;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        .write-form textarea {
            width: 100%;
            padding: 12px;
            border: 1px solid #333;
            border-radius: 5px;
            background: #1a1a2e;
            color: #eee;
            font-size: 14px;
            min-height: 100px;
            margin-bottom: 10px;
        }
        .write-form select {
            padding: 8px;
            border: 1px solid #333;
            border-radius: 5px;
            background: #1a1a2e;
            color: #eee;
            margin-right: 10px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🧠 Nexus Memory</h1>

        <div class="stats" id="stats">
            <div class="stat-card">
                <div class="stat-value" id="total-entries">-</div>
                <div class="stat-label">Total Entries</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="db-size">-</div>
                <div class="stat-label">Database Size</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="domains">-</div>
                <div class="stat-label">Domains</div>
            </div>
        </div>

        <div class="write-form">
            <h2>Write Knowledge</h2>
            <textarea id="write-content" placeholder="Enter knowledge content..."></textarea>
            <div>
                <select id="write-domain">
                    <option value="general">General</option>
                    <option value="workflow">Workflow</option>
                    <option value="identity">Identity</option>
                    <option value="project">Project</option>
                </select>
                <select id="write-source">
                    <option value="web_ui">Web UI</option>
                    <option value="api">API</option>
                    <option value="cli">CLI</option>
                </select>
                <button onclick="writeKnowledge()">Write</button>
            </div>
        </div>

        <div class="search-box">
            <input type="text" id="search-input" placeholder="Search knowledge..."
                   onkeypress="if(event.key==='Enter') search()">
            <button onclick="search()">Search</button>
        </div>

        <div class="results" id="results">
            <p style="color: #888; text-align: center;">Enter a search query to find knowledge</p>
        </div>

        <div class="pagination" id="pagination"></div>
    </div>

    <script>
        const API_BASE = '';
        let currentPage = 0;
        const PAGE_SIZE = 10;

        // Load stats
        async function loadStats() {
            try {
                const res = await fetch(`${API_BASE}/api/stats`);
                const data = await res.json();
                document.getElementById('total-entries').textContent = data.total_entries || 0;
                document.getElementById('db-size').textContent = `${data.db_size_mb || 0} MB`;
                document.getElementById('domains').textContent = Object.keys(data.by_domain || {}).length;
            } catch (e) {
                console.error('Failed to load stats:', e);
            }
        }

        // Write knowledge
        async function writeKnowledge() {
            const content = document.getElementById('write-content').value.trim();
            if (!content) return;

            const domain = document.getElementById('write-domain').value;
            const source = document.getElementById('write-source').value;

            try {
                const res = await fetch(`${API_BASE}/api/knowledge`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({content, domain, source, confidence: 0.8}),
                });
                const data = await res.json();

                if (data.id) {
                    document.getElementById('write-content').value = '';
                    alert('Knowledge written successfully!');
                    loadStats();
                }
            } catch (e) {
                alert('Failed to write knowledge: ' + e.message);
            }
        }

        // Search
        async function search(page = 0) {
            const query = document.getElementById('search-input').value.trim();
            if (!query) return;

            currentPage = page;

            try {
                const res = await fetch(`${API_BASE}/api/search?q=${encodeURIComponent(query)}&limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`);
                const data = await res.json();

                const resultsDiv = document.getElementById('results');
                if (data.results && data.results.length > 0) {
                    resultsDiv.innerHTML = data.results.map(r => `
                        <div class="result-item">
                            <div class="result-content">${escapeHtml(r.content)}</div>
                            <div class="result-meta">
                                <span class="badge badge-domain">${r.domain || 'general'}</span>
                                <span class="badge badge-source">${r.source || 'unknown'}</span>
                                <span class="badge badge-confidence">Confidence: ${(r.confidence || 0).toFixed(2)}</span>
                                ${r.score ? `<span>Score: ${r.score.toFixed(3)}</span>` : ''}
                                <span>ID: ${r.id}</span>
                            </div>
                        </div>
                    `).join('');

                    // Pagination
                    const paginationDiv = document.getElementById('pagination');
                    paginationDiv.innerHTML = '';
                    if (page > 0) {
                        paginationDiv.innerHTML += `<button onclick="search(${page - 1})">Previous</button>`;
                    }
                    if (data.results.length === PAGE_SIZE) {
                        paginationDiv.innerHTML += `<button onclick="search(${page + 1})">Next</button>`;
                    }
                } else {
                    resultsDiv.innerHTML = '<p style="color: #888; text-align: center;">No results found</p>';
                    document.getElementById('pagination').innerHTML = '';
                }
            } catch (e) {
                console.error('Search failed:', e);
            }
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        // Load stats on start
        loadStats();
    </script>
</body>
</html>
"""


@app.on_event("startup")
async def startup():
    """初始化"""
    global nexus
    db_path = os.environ.get("NEXUS_DB_PATH", str(Path.home() / ".hermes" / "data" / "nexus.db"))
    nexus = NexusCore(db_path)


@app.get("/", response_class=HTMLResponse)
async def index():
    """主页"""
    return HTML_TEMPLATE


@app.get("/api/stats")
async def get_stats():
    """获取统计"""
    if not nexus:
        return {"total_entries": 0, "db_size_mb": 0, "by_domain": {}}

    import sqlite3
    db_path = os.environ.get("NEXUS_DB_PATH", str(Path.home() / ".hermes" / "data" / "nexus.db"))

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM unified_knowledge WHERE status != 'archived'")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT last_query_domain, COUNT(*) FROM unified_knowledge WHERE status != 'archived' GROUP BY last_query_domain")
    by_domain = {k or "unknown": v for k, v in cursor.fetchall()}

    conn.close()

    db_size = Path(db_path).stat().st_size / 1024 / 1024 if Path(db_path).exists() else 0

    return {
        "total_entries": total,
        "db_size_mb": round(db_size, 2),
        "by_domain": by_domain,
    }


@app.get("/api/search")
async def search_knowledge(
    q: str = Query(..., description="Search query"),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    domain: str = Query(default=None),
):
    """搜索知识"""
    if not nexus:
        return {"results": [], "total": 0}

    if domain:
        results = nexus.search_by_domain(domain=domain, user_id="default", limit=limit + offset)
    else:
        results = nexus.search(q, limit=limit + offset)
    results = results[offset:offset + limit]

    return {"results": results, "total": len(results)}


@app.post("/api/knowledge")
async def write_knowledge(request: Request):
    """写入知识"""
    if not nexus:
        return {"error": "Database not initialized"}

    data = await request.json()
    content = data.get("content")
    source = data.get("source", "web_ui")
    confidence = data.get("confidence", 0.8)

    if not content:
        return {"error": "Content is required"}

    result = nexus.write(
        content=content,
        source_session_id=source,
        initial_confidence=confidence,
    )

    return result


def main():
    """启动服务器"""
    import argparse

    parser = argparse.ArgumentParser(description="Nexus Memory Web UI")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=8080, help="Port")
    parser.add_argument("--db", default=None, help="Database path")

    args = parser.parse_args()

    if args.db:
        os.environ["NEXUS_DB_PATH"] = args.db

    import uvicorn
    print(f"Starting Nexus Memory Web UI on {args.host}:{args.port}")
    print(f"Open http://{args.host}:{args.port} in your browser")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
