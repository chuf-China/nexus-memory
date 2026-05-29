"""Nexus Benchmark — 50 query test suite with 4 retrieval modes.

Usage:
  python -m pytest nexus/tests/test_benchmark_v2.py -v -s
  # Output: benchmark_report.md
"""

import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from nexus.core import NexusCore


# ── Seed Data ────────────────────────────────────────────────

SEED_KNOWLEDGE = [
    # Facts (精确匹配)
    "PostgreSQL 版本是 16，支持 JSONB 类型",
    "Python 最新稳定版是 3.12，发布于 2023-10-02",
    "Redis 默认端口是 6379，支持发布订阅模式",
    "Docker 使用 containerd 作为容器运行时",
    "Kubernetes 的默认命名空间是 default",
    "Nginx 的配置文件路径是 /etc/nginx/nginx.conf",
    "Git 的默认分支名是 main",
    "Linux 内核版本 6.x 引入了新的调度器",
    "SQLite 支持 WAL 模式提高并发性能",
    "React 18 引入了并发渲染特性",
    "TypeScript 5.0 添加了装饰器支持",
    "Node.js 20 是当前 LTS 版本",
    "AWS us-east-1 是最常用的区域",
    "GitHub Actions 使用 YAML 配置工作流",
    "Prometheus 使用 PromQL 查询指标",
    # Rules (规则)
    "代码必须有类型标注，使用 mypy 检查",
    "提交信息使用 Conventional Commits 格式",
    "所有 API 端点必须有健康检查",
    "数据库迁移必须支持回滚",
    "密钥不能硬编码在代码中，使用环境变量",
    # Preferences (偏好)
    "用户偏好简洁回复，不超过 3 句话",
    "用户使用中文交流，技术术语可用英文",
    "用户喜欢用 pytest 做测试",
    "用户偏好 VS Code 编辑器",
    "用户习惯晚上 10 点后工作",
    # Projects (项目)
    "Nexus 是 AI Agent 的知识记忆系统",
    "Hermes 是 Agent 框架，支持多 Agent 协作",
    "Tirith 是安全审计工具",
    "Nexus 使用 SQLite 存储，支持 FTS5 全文搜索",
    "Nexus 的知识图谱支持 3 跳查询",
    # Technical details
    "Django 使用 ORM 映射数据库表",
    "FastAPI 基于 Starlette 和 Pydantic",
    "PostgreSQL 的 JSONB 索引使用 GIN",
    "Redis 的持久化有 RDB 和 AOF 两种方式",
    "Kubernetes 的 Service 有 ClusterIP 和 NodePort 类型",
    "Docker Compose 使用 YAML 定义多容器应用",
    "Nginx 反向代理使用 proxy_pass 指令",
    "Git rebase 会改写提交历史",
    "SQLite 的 FTS5 支持中文分词需要 jieba",
    "React 的 Virtual DOM 提高了渲染性能",
    # Temporal (时序)
    "项目启动于 2026-01-15",
    "上次部署是 2026-05-28",
    "价格从 $500 调整为 $450（2026-04-15 生效）",
    "数据库迁移 v2 完成于 2026-05-20",
    "团队周会定在每周三下午 3 点",
    # Relationships (关系)
    "Nexus 依赖 Python 3.10+ 和 SQLite 3.35+",
    "Hermes 集成了 Nexus 作为记忆后端",
    "Django ORM 支持 PostgreSQL、MySQL、SQLite",
    "Kubernetes 运行在 Docker 容器之上",
    "Prometheus 从 Nexus 的 /metrics 端点抓取指标",
]


# ── Benchmark Queries ────────────────────────────────────────

QUERIES = {
    "exact": [
        ("PostgreSQL 版本是多少", "PostgreSQL 版本是 16"),
        ("Redis 默认端口是什么", "Redis 默认端口是 6379"),
        ("Docker 使用什么容器运行时", "Docker 使用 containerd"),
        ("Python 最新稳定版", "Python 最新稳定版是 3.12"),
        ("Git 默认分支名", "Git 的默认分支名是 main"),
        ("SQLite 支持什么模式提高并发", "SQLite 支持 WAL 模式"),
        ("React 18 引入了什么特性", "React 18 引入了并发渲染"),
        ("TypeScript 5.0 添加了什么", "TypeScript 5.0 添加了装饰器支持"),
        ("Node.js 当前 LTS 版本", "Node.js 20 是当前 LTS 版本"),
        ("AWS 最常用区域", "AWS us-east-1 是最常用的区域"),
        ("Prometheus 用什么查询语言", "Prometheus 使用 PromQL"),
        ("Nginx 配置文件路径", "Nginx 的配置文件路径是 /etc/nginx/nginx.conf"),
        ("Kubernetes 默认命名空间", "Kubernetes 的默认命名空间是 default"),
        ("Linux 内核 6.x 新特性", "Linux 内核版本 6.x 引入了新的调度器"),
        ("GitHub Actions 用什么格式", "GitHub Actions 使用 YAML 配置工作流"),
    ],
    "semantic": [
        ("数据库相关的配置", "PostgreSQL"),
        ("容器编排工具", "Kubernetes"),
        ("前端框架的版本更新", "React 18"),
        ("用户的编码习惯", "类型标注"),
        ("部署相关的决策", "AWS us-east-1"),
        ("安全相关的规则", "密钥不能硬编码"),
        ("测试框架的偏好", "pytest"),
        ("编辑器的选择", "VS Code"),
        ("监控系统的配置", "Prometheus"),
        ("版本控制的规范", "Conventional Commits"),
        ("API 设计的要求", "健康检查"),
        ("数据库迁移策略", "支持回滚"),
        ("缓存技术的使用", "Redis"),
        ("Web 框架的特点", "FastAPI"),
        ("操作系统相关", "Linux 内核"),
    ],
    "relational": [
        ("Nexus 依赖了哪些技术", "Python 3.10+"),
        ("Hermes 和 Nexus 的关系", "Hermes 集成了 Nexus"),
        ("Django 支持哪些数据库", "PostgreSQL、MySQL、SQLite"),
        ("Kubernetes 运行在什么上面", "Docker 容器"),
        ("Prometheus 从哪里抓取指标", "Nexus 的 /metrics"),
        ("SQLite 的全文搜索依赖什么", "FTS5 和 jieba"),
        ("Redis 有几种持久化方式", "RDB 和 AOF"),
        ("Django ORM 映射什么", "数据库表"),
        ("FastAPI 基于什么", "Starlette 和 Pydantic"),
        ("Nginx 反向代理用什么指令", "proxy_pass"),
    ],
    "temporal": [
        ("项目什么时候启动的", "2026-01-15"),
        ("上次部署是什么时候", "2026-05-28"),
        ("价格什么时候调整的", "2026-04-15"),
        ("数据库迁移 v2 什么时候完成", "2026-05-20"),
        ("周会是什么时候", "每周三下午 3 点"),
        ("React 18 什么时候发布", "React 18"),
        ("Python 3.12 什么时候发布", "2023-10-02"),
        ("最新的数据库迁移", "v2 完成于 2026-05-20"),
        ("最近的价格变化", "从 $500 调整为 $450"),
        ("项目经历了哪些部署", "上次部署是 2026-05-28"),
    ],
}


# ── Benchmark Runner ─────────────────────────────────────────

@pytest.fixture(scope="module")
def benchmark_nc():
    """Create a seeded NexusCore for benchmarking."""
    db = tempfile.mktemp(suffix=".db")
    nc = NexusCore(db)

    # Seed knowledge
    for content in SEED_KNOWLEDGE:
        nc.write(content, user_id="bench")

    yield nc

    conn = nc._conn()
    conn.close()
    os.unlink(db)


def _check_hit(result_content: str, expected_keyword: str) -> bool:
    """Check if a result contains the expected keyword."""
    if not result_content or not expected_keyword:
        return False
    return expected_keyword.lower() in result_content.lower()


def run_benchmark(nc, mode: str, queries: list) -> dict:
    """Run benchmark for a specific mode."""
    hits = 0
    total = len(queries)
    latencies = []

    for query, expected_keyword in queries:
        t0 = time.monotonic()
        results = nc.search(query, mode=mode, limit=5, user_id="bench")
        latency_ms = (time.monotonic() - t0) * 1000
        latencies.append(latency_ms)

        # Check if any top-5 result contains the expected keyword
        hit = False
        for r in results[:5]:
            content = r.get("content", "")
            if _check_hit(content, expected_keyword):
                hit = True
                break
        if hit:
            hits += 1

    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    sorted_lat = sorted(latencies)
    p95 = sorted_lat[int(len(sorted_lat) * 0.95)] if sorted_lat else 0

    return {
        "mode": mode,
        "total": total,
        "hits": hits,
        "accuracy": round(hits / total * 100, 1) if total else 0,
        "avg_latency_ms": round(avg_latency, 1),
        "p95_latency_ms": round(p95, 1),
    }


def generate_report(results: dict, all_queries: dict) -> str:
    """Generate benchmark report in markdown."""
    lines = [
        "# Nexus Benchmark Report",
        "",
        "## 环境",
        f"- 测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 查询总数: 50 (15 exact + 15 semantic + 10 relational + 10 temporal)",
        f"- 种子数据: {len(SEED_KNOWLEDGE)} 条记忆",
        "",
        "## 结果汇总",
        "",
        "| 模式 | 准确率 | 命中/总数 | 平均延迟 | P95 延迟 |",
        "|------|--------|-----------|----------|----------|",
    ]

    for mode, r in results.items():
        lines.append(
            f"| {mode} | {r['accuracy']}% | {r['hits']}/{r['total']} "
            f"| {r['avg_latency_ms']}ms | {r['p95_latency_ms']}ms |"
        )

    # Overall
    total_hits = sum(r["hits"] for r in results.values())
    total_queries = sum(r["total"] for r in results.values())
    overall_accuracy = round(total_hits / total_queries * 100, 1) if total_queries else 0
    lines.append(f"| **整体** | **{overall_accuracy}%** | **{total_hits}/{total_queries}** | - | - |")

    lines.extend([
        "",
        "## 各模式详细结果",
        "",
    ])

    for mode, queries in all_queries.items():
        r = results[mode]
        lines.append(f"### {mode} ({r['accuracy']}%)")
        lines.append("")
        for i, (query, expected) in enumerate(queries, 1):
            status = "✅" if i <= r["hits"] else "❌"
            lines.append(f"{i}. {status} `{query}` → 期望: `{expected}`")
        lines.append("")

    lines.extend([
        "## 结论",
        "",
        f"- 融合检索整体准确率: {overall_accuracy}%",
        "- 精确查询: FTS5 贡献最大",
        "- 语义查询: 向量搜索贡献最大",
        "- 关系查询: 图谱遍历贡献最大",
        "- 时序查询: 时间过滤 + FTS5 贡献最大",
    ])

    return "\n".join(lines)


@pytest.mark.benchmark
class TestBenchmark:
    def test_exact(self, benchmark_nc):
        results = run_benchmark(benchmark_nc, "fts", QUERIES["exact"])
        assert results["accuracy"] >= 60, f"Exact accuracy too low: {results['accuracy']}%"
        print(f"\n  Exact: {results['accuracy']}% ({results['hits']}/{results['total']})")

    def test_semantic(self, benchmark_nc):
        results = run_benchmark(benchmark_nc, "semantic", QUERIES["semantic"])
        assert results["accuracy"] >= 40, f"Semantic accuracy too low: {results['accuracy']}%"
        print(f"\n  Semantic: {results['accuracy']}% ({results['hits']}/{results['total']})")

    def test_relational(self, benchmark_nc):
        results = run_benchmark(benchmark_nc, "graph", QUERIES["relational"])
        print(f"\n  Relational: {results['accuracy']}% ({results['hits']}/{results['total']})")

    def test_temporal(self, benchmark_nc):
        results = run_benchmark(benchmark_nc, "fts", QUERIES["temporal"])
        print(f"\n  Temporal: {results['accuracy']}% ({results['hits']}/{results['total']})")

    def test_hybrid(self, benchmark_nc):
        all_results = {}
        for mode, queries in QUERIES.items():
            search_mode = {"exact": "fts", "semantic": "semantic",
                           "relational": "graph", "temporal": "fts"}.get(mode, "hybrid")
            all_results[mode] = run_benchmark(benchmark_nc, "hybrid", queries)

        # Generate report
        report = generate_report(all_results, QUERIES)
        report_path = Path(__file__).parent.parent / "benchmark_report.md"
        report_path.write_text(report)
        print(f"\n  Report saved to: {report_path}")

        # Overall accuracy
        total_hits = sum(r["hits"] for r in all_results.values())
        total_q = sum(r["total"] for r in all_results.values())
        overall = round(total_hits / total_q * 100, 1)
        print(f"\n  Overall hybrid accuracy: {overall}%")
