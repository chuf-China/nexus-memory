"""nexus_backup.py — 自动备份系统

功能:
  1. session_end 时 snapshot
  2. 每日定时备份 (cron job)
  3. 保留最近 N 份 (默认 7)
  4. 备份时 WAL checkpoint
  5. 备份完整性: MD5 校验

用法:
  from .backup import NexusBackup
  backup = NexusBackup(db_path)
  backup.snapshot()           # 执行一次备份
  backup.list_backups()       # → [{"name": "...", "size": ..., "md5": "..."}]
  backup.prune(keep=7)        # 清理旧备份
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BACKUP_DIR = Path.home() / ".hermes" / "data" / "backups" / "nexus"
MANIFEST_FILE = "backup_manifest.json"


class NexusBackup:
    """Nexus 数据库自动备份。"""

    def __init__(self, db_path: str, backup_dir: str = ""):
        self.db_path = db_path
        self.backup_dir = Path(backup_dir) if backup_dir else BACKUP_DIR
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def _md5(self, path: str) -> str:
        """计算文件 MD5。"""
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _load_manifest(self) -> List[Dict[str, Any]]:
        manifest_path = self.backup_dir / MANIFEST_FILE
        if manifest_path.exists():
            try:
                return json.loads(manifest_path.read_text())
            except Exception:
                pass
        return []

    def _save_manifest(self, manifest: List[Dict[str, Any]]):
        manifest_path = self.backup_dir / MANIFEST_FILE
        manifest_path.write_text(json.dumps(manifest, indent=2))

    def snapshot(self, label: str = "") -> Optional[Dict[str, Any]]:
        """执行一次备份。

        Returns:
            {"name": str, "path": str, "size": int, "md5": str, "ts": str}
            or None on failure
        """
        if not os.path.exists(self.db_path):
            logger.error("NexusBackup: source DB not found: %s", self.db_path)
            return None

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"nexus_{ts}{'_' + label if label else ''}.db"
        dest = self.backup_dir / name

        try:
            # WAL checkpoint before backup
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.close()
            except Exception:
                pass

            # Copy
            shutil.copy2(self.db_path, str(dest))

            # Verify integrity
            try:
                conn = sqlite3.connect(str(dest))
                result = conn.execute("PRAGMA integrity_check").fetchone()[0]
                conn.close()
                if result != "ok":
                    logger.error("NexusBackup: backup integrity check failed")
                    dest.unlink(missing_ok=True)
                    return None
            except Exception:
                dest.unlink(missing_ok=True)
                return None

            # MD5
            md5 = self._md5(str(dest))
            size = dest.stat().st_size

            # Update manifest
            manifest = self._load_manifest()
            entry = {
                "name": name,
                "path": str(dest),
                "size": size,
                "md5": md5,
                "ts": ts,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            manifest.append(entry)
            self._save_manifest(manifest)

            logger.info("NexusBackup: snapshot created %s (%d bytes, md5=%s)", name, size, md5[:8])
            return entry

        except Exception as e:
            logger.error("NexusBackup: snapshot failed: %s", e)
            return None

    def verify(self, name: str) -> bool:
        """验证备份文件完整性。"""
        manifest = self._load_manifest()
        for entry in manifest:
            if entry["name"] == name:
                path = entry["path"]
                if not os.path.exists(path):
                    return False
                return self._md5(path) == entry["md5"]
        return False

    def list_backups(self) -> List[Dict[str, Any]]:
        """列出所有备份。"""
        manifest = self._load_manifest()
        # Filter to existing files
        return [e for e in manifest if os.path.exists(e.get("path", ""))]

    def restore(self, name: str) -> bool:
        """从备份恢复数据库。"""
        manifest = self._load_manifest()
        for entry in manifest:
            if entry["name"] == name:
                src = entry["path"]
                if not os.path.exists(src):
                    logger.error("NexusBackup: backup file not found: %s", src)
                    return False

                # Verify MD5
                if self._md5(src) != entry["md5"]:
                    logger.error("NexusBackup: backup MD5 mismatch")
                    return False

                # Atomic restore: copy to temp, then rename
                tmp = self.db_path + ".restore_tmp"
                try:
                    shutil.copy2(src, tmp)
                    os.replace(tmp, self.db_path)

                    # Recreate WAL
                    conn = sqlite3.connect(self.db_path)
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.close()

                    logger.info("NexusBackup: restored from %s", name)
                    return True
                except Exception as e:
                    logger.error("NexusBackup: restore failed: %s", e)
                    os.unlink(tmp) if os.path.exists(tmp) else None
                    return False

        logger.error("NexusBackup: backup '%s' not found in manifest", name)
        return False

    def prune(self, keep: int = 7):
        """保留最近 N 份备份，删除更旧的。"""
        manifest = self._load_manifest()
        if len(manifest) <= keep:
            return

        # Sort by timestamp (newest first)
        manifest.sort(key=lambda e: e.get("ts", ""), reverse=True)

        to_delete = manifest[keep:]
        for entry in to_delete:
            path = entry.get("path", "")
            if os.path.exists(path):
                try:
                    os.unlink(path)
                    logger.info("NexusBackup: pruned %s", entry["name"])
                except Exception:
                    pass

        self._save_manifest(manifest[:keep])

    def latest(self) -> Optional[Dict[str, Any]]:
        """获取最新备份信息。"""
        manifest = self._load_manifest()
        existing = [e for e in manifest if os.path.exists(e.get("path", ""))]
        if not existing:
            return None
        return max(existing, key=lambda e: e.get("ts", ""))

    def auto_backup(self, keep: int = 10, min_interval_hours: int = 6) -> Optional[Dict[str, Any]]:
        """Auto backup: skip if last backup < min_interval_hours ago.

        Returns snapshot result or None if skipped.
        """
        last = self.latest()
        if last:
            try:
                from datetime import datetime, timezone
                last_ts = datetime.fromisoformat(last["created_at"])
                age_hours = (datetime.now(timezone.utc) - last_ts).total_seconds() / 3600
                if age_hours < min_interval_hours:
                    logger.debug("NexusBackup: skipping auto backup (last backup %.1fh ago)", age_hours)
                    return None
            except Exception:
                pass

        result = self.snapshot(label="auto")
        if result:
            self.prune(keep=keep)
            logger.info("NexusBackup: auto backup completed (%s)", result["name"])
        return result
