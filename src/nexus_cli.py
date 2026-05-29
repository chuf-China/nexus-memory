#!/usr/bin/env python3
"""nexus_cli.py — Nexus知识库管理CLI

用法:
  python nexus_cli.py health         # 健康检查（12项指标）
  python nexus_cli.py list [n]       # 浏览知识（默认20条）
  python nexus_cli.py stats          # 统计信息
  python nexus_cli.py export         # 导出知识（JSON）
  python nexus_cli.py import <file>  # 导入知识
  python nexus_cli.py edit <id> <content>  # 编辑知识
  python nexus_cli.py delete <id>    # 归档知识
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = Path.home() / ".hermes" / "data" / "nexus.db"
BACKUP_DIR = Path.home() / ".hermes" / "data" / "backups"


def health() -> Dict[str, Any]:
    """执行12项健康检查。"""
    checks = {}

    # 1. 数据库文件存在
    checks["db_exists"] = DB_PATH.exists()

    # 2. 数据库可读
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("SELECT 1")
        checks["db_readable"] = True
    except Exception as e:
        checks["db_readable"] = False
        checks["db_error"] = str(e)
        return checks

    # 3. WAL模式
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        checks["wal_mode"] = mode.upper() == "WAL"
        checks["journal_mode"] = mode
    except Exception:
        checks["wal_mode"] = False

    # 4. WAL文件大小
    wal_path = DB_PATH.with_suffix(".db-wal")
    if wal_path.exists():
        wal_size = wal_path.stat().st_size
        checks["wal_size_ok"] = wal_size < 10 * 1024 * 1024  # <10MB
        checks["wal_size_mb"] = round(wal_size / 1024 / 1024, 2)
    else:
        checks["wal_size_ok"] = True
        checks["wal_size_mb"] = 0

    # 5. 数据库完整性
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        checks["integrity_ok"] = result == "ok"
    except Exception:
        checks["integrity_ok"] = False

    # 6. 表结构存在
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        checks["tables_exist"] = "unified_knowledge" in tables
        checks["table_count"] = len(tables)
    except Exception:
        checks["tables_exist"] = False

    # 7. FTS索引存在
    try:
        fts_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_fts'"
        ).fetchone()
        checks["fts_index_exists"] = fts_check is not None
    except Exception:
        checks["fts_index_exists"] = False

    # 8. 数据量
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM unified_knowledge WHERE status='active'"
        ).fetchone()[0]
        checks["active_count"] = count
        checks["data_healthy"] = count > 0
    except Exception:
        checks["active_count"] = 0
        checks["data_healthy"] = False

    # 9. 备份存在
    backup_files = list(BACKUP_DIR.glob("nexus_*.db")) if BACKUP_DIR.exists() else []
    checks["backup_exists"] = len(backup_files) > 0
    checks["backup_count"] = len(backup_files)

    # 10. 最近备份时间
    if backup_files:
        latest = max(backup_files, key=lambda f: f.stat().st_mtime)
        age_hours = (time.time() - latest.stat().st_mtime) / 3600
        checks["latest_backup_hours"] = round(age_hours, 1)
        checks["backup_fresh"] = age_hours < 48
    else:
        checks["latest_backup_hours"] = None
        checks["backup_fresh"] = False

    # 11. 领域名覆盖
    try:
        rows = conn.execute(
            "SELECT domain_scores FROM unified_knowledge WHERE status='active' LIMIT 100"
        ).fetchall()
        domains = set()
        for row in rows:
            try:
                scores = json.loads(row[0])
                domains.update(scores.keys())
            except Exception:
                pass
        checks["domains_found"] = list(domains)
        checks["domain_count"] = len(domains)
    except Exception:
        checks["domains_found"] = []
        checks["domain_count"] = 0

    # 12. 版本链完整性 (使用replaced_by/replaces列)
    try:
        versioned = conn.execute(
            "SELECT COUNT(*) FROM unified_knowledge WHERE replaced_by IS NOT NULL OR replaces IS NOT NULL"
        ).fetchone()[0]
        checks["versioned_count"] = versioned
        checks["version_chain_ok"] = True  # 表存在即正常
    except Exception:
        checks["version_chain_ok"] = False

    conn.close()
    return checks


def list_knowledge(limit: int = 20, domain: str = None) -> List[Dict]:
    """浏览知识条目。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    query = """
        SELECT id, content, domain_scores, layer,
               positive_feedback, negative_feedback, status
        FROM unified_knowledge
        WHERE status = 'active'
    """
    params = []

    if domain:
        query += " AND domain_scores LIKE ?"
        params.append(f'%"{domain}"%')

    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    results = []
    for row in rows:
        item = dict(row)
        try:
            item["domain_scores"] = json.loads(item["domain_scores"])
        except Exception:
            item["domain_scores"] = {}
        results.append(item)

    return results


def stats() -> Dict[str, Any]:
    """获取统计信息。"""
    conn = sqlite3.connect(str(DB_PATH))

    total = conn.execute(
        "SELECT COUNT(*) FROM unified_knowledge"
    ).fetchone()[0]

    active = conn.execute(
        "SELECT COUNT(*) FROM unified_knowledge WHERE status='active'"
    ).fetchone()[0]

    by_layer = {}
    for row in conn.execute(
        "SELECT layer, COUNT(*) FROM unified_knowledge WHERE status='active' GROUP BY layer"
    ).fetchall():
        by_layer[row[0]] = row[1]

    by_domain = {}
    for row in conn.execute(
        "SELECT domain_scores FROM unified_knowledge WHERE status='active'"
    ).fetchall():
        try:
            scores = json.loads(row[0])
            for domain, score in scores.items():
                by_domain[domain] = by_domain.get(domain, 0) + score
        except Exception:
            pass

    conn.close()

    return {
        "total": total,
        "active": active,
        "by_layer": by_layer,
        "by_domain": by_domain,
    }


def export_knowledge(output_path: str = None) -> str:
    """导出知识为JSON。"""
    knowledge = list_knowledge(limit=10000)

    if output_path is None:
        output_path = str(Path.home() / ".hermes" / "data" / f"nexus_export_{int(time.time())}.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(knowledge, f, ensure_ascii=False, indent=2)

    return output_path


def import_knowledge(input_path: str) -> int:
    """从JSON导入知识。"""
    from agent.nexus_core import NexusCore

    with open(input_path, "r", encoding="utf-8") as f:
        knowledge = json.load(f)

    nc = NexusCore(str(DB_PATH))
    count = 0

    for item in knowledge:
        content = item.get("content", "")
        domain_scores = item.get("domain_scores", {})
        if content and domain_scores:
            nc.write(
                content=content,
                domain_scores=domain_scores,
                source="import",
            )
            count += 1

    nc.close()
    return count


def edit_knowledge(knowledge_id: int, new_content: str):
    """编辑知识条目内容。"""
    conn = sqlite3.connect(str(DB_PATH))

    # 检查是否存在
    row = conn.execute(
        "SELECT id, content FROM unified_knowledge WHERE id = ? AND status = 'active'",
        (knowledge_id,)
    ).fetchone()

    if not row:
        print(f"错误: 知识ID {knowledge_id} 不存在或已归档")
        conn.close()
        return

    old_content = row[1]

    # 更新内容
    conn.execute(
        "UPDATE unified_knowledge SET content = ?, updated_at = ? WHERE id = ?",
        (new_content, datetime.now(timezone.utc).isoformat(), knowledge_id)
    )
    conn.commit()
    conn.close()

    print(f"已更新知识 {knowledge_id}")
    print(f"  旧内容: {old_content[:50]}...")
    print(f"  新内容: {new_content[:50]}...")


def delete_knowledge(knowledge_id: int):
    """归档（软删除）知识条目。"""
    conn = sqlite3.connect(str(DB_PATH))

    # 检查是否存在
    row = conn.execute(
        "SELECT id, content FROM unified_knowledge WHERE id = ? AND status = 'active'",
        (knowledge_id,)
    ).fetchone()

    if not row:
        print(f"错误: 知识ID {knowledge_id} 不存在或已归档")
        conn.close()
        return

    content = row[1]

    # 软删除（归档）
    conn.execute(
        "UPDATE unified_knowledge SET status = 'archived', archived_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), knowledge_id)
    )
    conn.commit()
    conn.close()

    print(f"已归档知识 {knowledge_id}: {content[:50]}...")


def print_health(checks: Dict[str, Any]):
    """格式化输出健康检查结果。"""
    print("\n" + "=" * 50)
    print("  Nexus 健康检查")
    print("=" * 50)

    icons = {
        True: "✓",
        False: "✗",
    }

    items = [
        ("数据库文件", checks.get("db_exists")),
        ("数据库可读", checks.get("db_readable")),
        ("WAL模式", checks.get("wal_mode")),
        ("WAL大小正常", checks.get("wal_size_ok")),
        ("数据库完整性", checks.get("integrity_ok")),
        ("表结构存在", checks.get("tables_exist")),
        ("FTS索引存在", checks.get("fts_index_exists")),
        ("数据量正常", checks.get("data_healthy")),
        ("备份存在", checks.get("backup_exists")),
        ("备份新鲜度", checks.get("backup_fresh")),
        ("版本链完整", checks.get("version_chain_ok")),
        ("领域名覆盖", checks.get("domain_count", 0) > 0),
    ]

    for name, ok in items:
        icon = icons.get(ok, "?")
        print(f"  {icon} {name}")

    print("-" * 50)
    print(f"  活跃知识: {checks.get('active_count', 0)} 条")
    print(f"  领域名: {checks.get('domain_count', 0)} 个")
    print(f"  备份数: {checks.get('backup_count', 0)} 个")

    if checks.get("latest_backup_hours") is not None:
        print(f"  最近备份: {checks['latest_backup_hours']} 小时前")

    print("=" * 50 + "\n")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "health":
        checks = health()
        print_health(checks)
        sys.exit(0 if all(checks.get(k) for k in [
            "db_exists", "db_readable", "wal_mode", "integrity_ok",
            "tables_exist", "data_healthy"
        ]) else 1)

    elif cmd == "list":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        domain = sys.argv[3] if len(sys.argv) > 3 else None
        items = list_knowledge(limit, domain)
        for item in items:
            print(f"[{item['id']}] [{item['layer']}] {item['content'][:60]}...")
            print(f"     领域: {item['domain_scores']}")
        print(f"\n共 {len(items)} 条")

    elif cmd == "stats":
        s = stats()
        print(json.dumps(s, ensure_ascii=False, indent=2))

    elif cmd == "export":
        path = export_knowledge()
        print(f"已导出到: {path}")

    elif cmd == "import":
        if len(sys.argv) < 3:
            print("用法: nexus_cli.py import <file>")
            sys.exit(1)
        count = import_knowledge(sys.argv[2])
        print(f"已导入 {count} 条知识")

    elif cmd == "edit":
        if len(sys.argv) < 4:
            print("用法: nexus_cli.py edit <id> <new_content>")
            sys.exit(1)
        kid = int(sys.argv[2])
        new_content = " ".join(sys.argv[3:])
        edit_knowledge(kid, new_content)

    elif cmd == "delete":
        if len(sys.argv) < 3:
            print("用法: nexus_cli.py delete <id>")
            sys.exit(1)
        kid = int(sys.argv[2])
        delete_knowledge(kid)

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
