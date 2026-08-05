#!/usr/bin/env python3
"""multi_tenant.py — 多租户隔离模块

功能:
1. 租户管理（创建、删除、查询）
2. 数据隔离（按租户 ID 隔离）
3. 资源配额（限制每个租户的存储量）
4. 访问控制（租户级别的权限管理）

用法:
    from src.multi_tenant import TenantManager

    manager = TenantManager(storage_backend)

    # 创建租户
    tenant = manager.create_tenant("tenant_001", name="Acme Corp", quota_mb=100)

    # 写入知识（自动关联租户）
    manager.write_for_tenant("tenant_001", "User prefers dark mode")

    # 搜索知识（只返回该租户的数据）
    results = manager.search_for_tenant("tenant_001", "user preferences")
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .storage_backend import StorageBackend


class Tenant:
    """租户数据类"""

    def __init__(self, tenant_id: str, name: str = None, api_key: str = None,
                 quota_mb: int = 100, created_at: str = None, metadata: Dict = None):
        self.tenant_id = tenant_id
        self.name = name or tenant_id
        self.api_key = api_key or f"nx_{secrets.token_hex(32)}"
        self.quota_mb = quota_mb
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.metadata = metadata or {}

    def to_dict(self) -> Dict:
        return {
            "tenant_id": self.tenant_id,
            "name": self.name,
            "api_key": self.api_key,
            "quota_mb": self.quota_mb,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Tenant':
        return cls(**data)


class TenantManager:
    """租户管理器"""

    def __init__(self, storage: StorageBackend):
        self.storage = storage
        self._ensure_tenant_tables()

    def _ensure_tenant_tables(self):
        """确保租户表存在"""
        self.storage.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                tenant_id VARCHAR(100) PRIMARY KEY,
                name VARCHAR(200),
                api_key VARCHAR(100) UNIQUE NOT NULL,
                quota_mb INTEGER DEFAULT 100,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT DEFAULT '{}',
                is_active BOOLEAN DEFAULT TRUE
            )
        """)

        self.storage.execute("""
            CREATE TABLE IF NOT EXISTS tenant_usage (
                tenant_id VARCHAR(100) PRIMARY KEY,
                entry_count INTEGER DEFAULT 0,
                total_size_bytes BIGINT DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
            )
        """)

        self.storage.commit()

    def create_tenant(self, tenant_id: str, name: str = None,
                      api_key: str = None, quota_mb: int = 100,
                      metadata: Dict = None) -> Tenant:
        """创建租户"""
        # 检查是否已存在
        existing = self.get_tenant(tenant_id)
        if existing:
            raise ValueError(f"Tenant {tenant_id} already exists")

        tenant = Tenant(
            tenant_id=tenant_id,
            name=name,
            api_key=api_key,
            quota_mb=quota_mb,
            metadata=metadata,
        )

        # 插入租户记录
        self.storage.execute("""
            INSERT INTO tenants (tenant_id, name, api_key, quota_mb, metadata)
            VALUES (?, ?, ?, ?, ?)
        """, (tenant.tenant_id, tenant.name, tenant.api_key,
              tenant.quota_mb, json.dumps(tenant.metadata)))

        # 初始化使用量记录
        self.storage.execute("""
            INSERT INTO tenant_usage (tenant_id) VALUES (?)
        """, (tenant_id,))

        self.storage.commit()
        return tenant

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """获取租户"""
        rows = self.storage.execute("""
            SELECT tenant_id, name, api_key, quota_mb, created_at, metadata
            FROM tenants WHERE tenant_id = ? AND is_active = TRUE
        """, (tenant_id,))

        if rows:
            row = rows[0]
            return Tenant(
                tenant_id=row[0],
                name=row[1],
                api_key=row[2],
                quota_mb=row[3],
                created_at=row[4],
                metadata=json.loads(row[5]) if row[5] else {},
            )
        return None

    def get_tenant_by_api_key(self, api_key: str) -> Optional[Tenant]:
        """通过 API Key 获取租户"""
        rows = self.storage.execute("""
            SELECT tenant_id, name, api_key, quota_mb, created_at, metadata
            FROM tenants WHERE api_key = ? AND is_active = TRUE
        """, (api_key,))

        if rows:
            row = rows[0]
            return Tenant(
                tenant_id=row[0],
                name=row[1],
                api_key=row[2],
                quota_mb=row[3],
                created_at=row[4],
                metadata=json.loads(row[5]) if row[5] else {},
            )
        return None

    def list_tenants(self) -> List[Tenant]:
        """列出所有租户"""
        rows = self.storage.execute("""
            SELECT tenant_id, name, api_key, quota_mb, created_at, metadata
            FROM tenants WHERE is_active = TRUE
        """)

        return [
            Tenant(
                tenant_id=row[0],
                name=row[1],
                api_key=row[2],
                quota_mb=row[3],
                created_at=row[4],
                metadata=json.loads(row[5]) if row[5] else {},
            )
            for row in rows
        ]

    def delete_tenant(self, tenant_id: str, hard_delete: bool = False):
        """删除租户"""
        if hard_delete:
            # 硬删除：删除所有数据
            self.storage.execute("DELETE FROM knowledge WHERE user_id = ?", (tenant_id,))
            self.storage.execute("DELETE FROM tenant_usage WHERE tenant_id = ?", (tenant_id,))
            self.storage.execute("DELETE FROM tenants WHERE tenant_id = ?", (tenant_id,))
        else:
            # 软删除：标记为不活跃
            self.storage.execute("""
                UPDATE tenants SET is_active = FALSE WHERE tenant_id = ?
            """, (tenant_id,))

        self.storage.commit()

    def update_quota(self, tenant_id: str, quota_mb: int):
        """更新配额"""
        self.storage.execute("""
            UPDATE tenants SET quota_mb = ? WHERE tenant_id = ?
        """, (quota_mb, tenant_id))
        self.storage.commit()

    def get_usage(self, tenant_id: str) -> Dict:
        """获取使用量"""
        rows = self.storage.execute("""
            SELECT entry_count, total_size_bytes, last_updated
            FROM tenant_usage WHERE tenant_id = ?
        """, (tenant_id,))

        if rows:
            row = rows[0]
            return {
                "tenant_id": tenant_id,
                "entry_count": row[0],
                "total_size_bytes": row[1],
                "total_size_mb": round(row[1] / 1024 / 1024, 2),
                "last_updated": row[2],
            }

        return {
            "tenant_id": tenant_id,
            "entry_count": 0,
            "total_size_bytes": 0,
            "total_size_mb": 0,
            "last_updated": None,
        }

    def check_quota(self, tenant_id: str, additional_bytes: int = 0) -> bool:
        """检查配额"""
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            return False

        usage = self.get_usage(tenant_id)
        quota_bytes = tenant.quota_mb * 1024 * 1024

        return (usage["total_size_bytes"] + additional_bytes) <= quota_bytes

    def update_usage(self, tenant_id: str, entry_count_delta: int = 0,
                     size_delta_bytes: int = 0):
        """更新使用量"""
        self.storage.execute("""
            UPDATE tenant_usage
            SET entry_count = entry_count + ?,
                total_size_bytes = total_size_bytes + ?,
                last_updated = CURRENT_TIMESTAMP
            WHERE tenant_id = ?
        """, (entry_count_delta, size_delta_bytes, tenant_id))
        self.storage.commit()

    def write_for_tenant(self, tenant_id: str, content: str,
                         source: str = "api", confidence: float = 0.8,
                         domain: str = "general") -> Dict:
        """为租户写入知识"""
        # 检查配额
        content_size = len(content.encode('utf-8'))
        if not self.check_quota(tenant_id, content_size):
            raise ValueError(f"Tenant {tenant_id} exceeded quota")

        # 写入知识
        content_hash = hashlib.md5(content.encode()).hexdigest()
        knowledge_id = self.storage.insert_knowledge(
            content=content,
            content_hash=content_hash,
            source=source,
            confidence=confidence,
            domain=domain,
            user_id=tenant_id,  # 使用 tenant_id 作为 user_id
        )

        # 更新使用量
        self.update_usage(tenant_id, entry_count_delta=1, size_delta_bytes=content_size)

        return {
            "success": True,
            "id": knowledge_id,
            "tenant_id": tenant_id,
        }

    def search_for_tenant(self, tenant_id: str, query: str,
                          limit: int = 5, domain_filter: str = None) -> List[Dict]:
        """为租户搜索知识"""
        return self.storage.search_fts(
            query=query,
            limit=limit,
            domain_filter=domain_filter,
            user_id=tenant_id,  # 按 tenant_id 过滤
        )

    def get_tenant_stats(self, tenant_id: str) -> Dict:
        """获取租户统计"""
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            return {"error": "Tenant not found"}

        usage = self.get_usage(tenant_id)
        stats = self.storage.get_stats(user_id=tenant_id)

        return {
            "tenant": tenant.to_dict(),
            "usage": usage,
            "stats": stats,
        }


class TenantMiddleware:
    """租户中间件（用于 API 认证）"""

    def __init__(self, tenant_manager: TenantManager):
        self.tenant_manager = tenant_manager

    def authenticate(self, api_key: str) -> Optional[Tenant]:
        """认证 API Key"""
        return self.tenant_manager.get_tenant_by_api_key(api_key)

    def require_tenant(self, api_key: str) -> Tenant:
        """要求租户认证"""
        tenant = self.authenticate(api_key)
        if not tenant:
            raise ValueError("Invalid API key")
        return tenant
