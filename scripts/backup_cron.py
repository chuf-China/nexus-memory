#!/usr/bin/env python3
"""nexus_backup_cron.py — 每日 Nexus 数据库自动备份。

由 Hermes cron 调度，每天凌晨 2:00 运行。
执行备份 + 保留最近 7 份 + 清理旧指标。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hermes-agent"))

DB_PATH = str(Path.home() / ".hermes" / "data" / "nexus.db")


def main():
    from ..backup import NexusBackup

    backup = NexusBackup(DB_PATH)

    # 1. 执行备份
    entry = backup.snapshot(label="daily")
    if entry:
        print(f"[OK] Backup created: {entry['name']} ({entry['size']} bytes, md5={entry['md5'][:8]})")
    else:
        print("[ERROR] Backup failed!")
        sys.exit(1)

    # 2. 清理旧备份 (保留 7 份)
    backup.prune(keep=7)
    remaining = backup.list_backups()
    print(f"[OK] Backups on disk: {len(remaining)}")

    # 3. 清理旧指标 (保留 30 天)
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        from ..metrics import NexusMetrics
        NexusMetrics(conn).prune(days=30)
        conn.close()
        print("[OK] Old metrics pruned")
    except Exception as e:
        print(f"[WARN] Metrics prune failed: {e}")

    print("[OK] Nexus daily backup complete.")


if __name__ == "__main__":
    main()
