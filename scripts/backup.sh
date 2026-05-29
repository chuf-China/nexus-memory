#!/bin/bash
# nexus_backup.sh — 每日备份nexus.db，保留7天滚动备份

set -e

DB_PATH="$HOME/.hermes/data/nexus.db"
BACKUP_DIR="$HOME/.hermes/data/backups"
DATE=$(date +%Y%m%d_%H%M%S)
KEEP_DAYS=7

# 确保备份目录存在
mkdir -p "$BACKUP_DIR"

# 检查数据库是否存在
if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: $DB_PATH not found"
    exit 1
fi

# 执行WAL checkpoint，确保数据完整
sqlite3 "$DB_PATH" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true

# 复制数据库
cp "$DB_PATH" "$BACKUP_DIR/nexus_${DATE}.db"

# 清理超过KEEP_DAYS天的备份
find "$BACKUP_DIR" -name "nexus_*.db" -mtime +$KEEP_DAYS -delete 2>/dev/null || true

echo "Backup completed: nexus_${DATE}.db"
