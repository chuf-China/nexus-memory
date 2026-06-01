#!/usr/bin/env python3
"""security.py — 安全模块

功能:
1. API Key 认证
2. 请求速率限制
3. 输入验证
4. SQL 注入防护
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from fastapi import HTTPException, Request, Security
    from fastapi.security import APIKeyHeader
except ImportError:
    # 如果 FastAPI 未安装，提供基本功能
    pass


class APIKeyManager:
    """API Key 管理器"""
    
    def __init__(self, keys_file: str = None):
        self.keys_file = keys_file or os.path.expanduser("~/.nexus/api_keys.json")
        self.keys: Dict[str, Dict] = {}
        self._load_keys()
    
    def _load_keys(self):
        """加载 API Keys"""
        import json
        
        if os.path.exists(self.keys_file):
            try:
                with open(self.keys_file, "r") as f:
                    self.keys = json.load(f)
            except Exception:
                self.keys = {}
    
    def _save_keys(self):
        """保存 API Keys"""
        import json
        
        os.makedirs(os.path.dirname(self.keys_file), exist_ok=True)
        with open(self.keys_file, "w") as f:
            json.dump(self.keys, f, indent=2)
    
    def generate_key(self, name: str, permissions: List[str] = None) -> str:
        """生成新的 API Key"""
        key = f"nexus_{secrets.token_hex(32)}"
        
        self.keys[key] = {
            "name": name,
            "permissions": permissions or ["read", "write"],
            "created_at": datetime.now().isoformat(),
            "last_used": None,
            "request_count": 0,
        }
        
        self._save_keys()
        return key
    
    def validate_key(self, key: str) -> Optional[Dict]:
        """验证 API Key"""
        if key not in self.keys:
            return None
        
        key_info = self.keys[key]
        
        # 更新使用记录
        key_info["last_used"] = datetime.now().isoformat()
        key_info["request_count"] += 1
        self._save_keys()
        
        return key_info
    
    def revoke_key(self, key: str) -> bool:
        """撤销 API Key"""
        if key in self.keys:
            del self.keys[key]
            self._save_keys()
            return True
        return False
    
    def list_keys(self) -> Dict[str, Dict]:
        """列出所有 API Keys"""
        return self.keys


class RateLimiter:
    """速率限制器"""
    
    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        self.requests: Dict[str, List[float]] = defaultdict(list)
    
    def is_allowed(self, client_id: str) -> Tuple[bool, Dict]:
        """检查是否允许请求"""
        now = time.time()
        minute_ago = now - 60
        
        # 清理旧记录
        self.requests[client_id] = [
            t for t in self.requests[client_id] if t > minute_ago
        ]
        
        # 检查限制
        if len(self.requests[client_id]) >= self.requests_per_minute:
            return False, {
                "error": "Rate limit exceeded",
                "limit": self.requests_per_minute,
                "remaining": 0,
                "reset": int(minute_ago + 60),
            }
        
        # 记录请求
        self.requests[client_id].append(now)
        
        return True, {
            "limit": self.requests_per_minute,
            "remaining": self.requests_per_minute - len(self.requests[client_id]),
            "reset": int(minute_ago + 60),
        }


class InputValidator:
    """输入验证器"""
    
    # SQL 注入模式
    SQL_INJECTION_PATTERNS = [
        r"((union|select|insert|update|delete|drop|alter))",
        r"(--|;|/\*|\*/)",
        r"((or|and)\s+\d+\s*=\s*\d+)",
        r"('|"|\\)",
    ]
    
    # XSS 模式
    XSS_PATTERNS = [
        r"(<script[^>]*>)",
        r"(javascript:)",
        r"(on\w+\s*=)",
    ]
    
    @classmethod
    def validate_content(cls, content: str) -> Tuple[bool, Optional[str]]:
        """验证内容安全性"""
        # 检查 SQL 注入
        for pattern in cls.SQL_INJECTION_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                return False, f"Potential SQL injection detected: {pattern}"
        
        # 检查 XSS
        for pattern in cls.XSS_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                return False, f"Potential XSS detected: {pattern}"
        
        # 检查长度
        if len(content) > 100000:  # 100KB
            return False, "Content too long (max 100KB)"
        
        return None, None
    
    @classmethod
    def sanitize_content(cls, content: str) -> str:
        """清理内容"""
        # 移除潜在危险字符
        content = content.replace("\", "\\")
        content = content.replace("'", "\'")
        content = content.replace('"', '\"')
        
        return content


class SecurityMiddleware:
    """安全中间件"""
    
    def __init__(
        self,
        api_key_manager: APIKeyManager = None,
        rate_limiter: RateLimiter = None,
        require_api_key: bool = True,
    ):
        self.api_key_manager = api_key_manager or APIKeyManager()
        self.rate_limiter = rate_limiter or RateLimiter()
        self.require_api_key = require_api_key
    
    def authenticate(self, api_key: str = None) -> Dict:
        """认证请求"""
        if not self.require_api_key:
            return {"authenticated": True, "permissions": ["read", "write"]}
        
        if not api_key:
            raise HTTPException(
                status_code=401,
                detail="API key required. Pass it via X-API-Key header.",
            )
        
        key_info = self.api_key_manager.validate_key(api_key)
        if not key_info:
            raise HTTPException(
                status_code=401,
                detail="Invalid API key.",
            )
        
        return {
            "authenticated": True,
            "name": key_info["name"],
            "permissions": key_info["permissions"],
        }
    
    def check_rate_limit(self, client_id: str) -> Dict:
        """检查速率限制"""
        allowed, info = self.rate_limiter.is_allowed(client_id)
        
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Please try again later.",
            )
        
        return info
    
    def validate_input(self, content: str) -> str:
        """验证并清理输入"""
        error, message = InputValidator.validate_content(content)
        
        if error:
            raise HTTPException(
                status_code=400,
                detail=message,
            )
        
        return InputValidator.sanitize_content(content)


# ============ FastAPI 依赖项 ============

try:
    from fastapi import Depends, Request
    from fastapi.security import APIKeyHeader
    
    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
    
    # 默认安全中间件
    _security_middleware = SecurityMiddleware(require_api_key=False)
    
    def get_security_middleware() -> SecurityMiddleware:
        """获取安全中间件"""
        return _security_middleware
    
    def set_security_middleware(middleware: SecurityMiddleware):
        """设置安全中间件"""
        global _security_middleware
        _security_middleware = middleware
    
    async def verify_api_key(
        request: Request,
        api_key: Optional[str] = Security(api_key_header),
    ) -> Dict:
        """验证 API Key"""
        middleware = get_security_middleware()
        
        # 获取客户端 ID
        client_id = request.client.host if request.client else "unknown"
        
        # 检查速率限制
        rate_info = middleware.check_rate_limit(client_id)
        
        # 认证
        auth_info = middleware.authenticate(api_key)
        
        return {**auth_info, **rate_info}
    
except ImportError:
    # FastAPI 未安装
    pass


# ============ 工具函数 ============

def hash_password(password: str) -> str:
    """哈希密码"""
    salt = secrets.token_hex(16)
    hash_obj = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt.encode(),
        100000,
    )
    return f"{salt}${hash_obj.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """验证密码"""
    try:
        salt, hash_hex = hashed.split("$")
        hash_obj = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            salt.encode(),
            100000,
        )
        return hmac.compare_digest(hash_obj.hex(), hash_hex)
    except Exception:
        return False


def generate_token(length: int = 32) -> str:
    """生成随机 token"""
    return secrets.token_hex(length)
