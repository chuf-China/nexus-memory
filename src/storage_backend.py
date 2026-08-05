#!/usr/bin/env python3
"""storage_backend.py — 存储后端抽象层

支持:
1. SQLite (默认，本地)
2. PostgreSQL (分布式)

用法:
    from src.storage_backend import get_storage_backend

    # 使用 SQLite
    storage = get_storage_backend("sqlite:///path/to/nexus.db")

    # 使用 PostgreSQL
    storage = get_storage_backend("postgresql://user:pass@localhost/nexus")
"""

from __future__ import annotations

import os
import sqlite3
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


class StorageBackend(ABC):
    """存储后端抽象基类"""

    @abstractmethod
    def connect(self):
        """建立连接"""
        pass

    @abstractmethod
    def close(self):
        """关闭连接"""
        pass

    @abstractmethod
    def execute(self, query: str, params: Tuple = None) -> List[Tuple]:
        """执行查询"""
        pass

    @abstractmethod
    def execute_many(self, query: str, params_list: List[Tuple]) -> int:
        """批量执行"""
        pass

    @abstractmethod
    def commit(self):
        """提交事务"""
        pass

    @abstractmethod
    def rollback(self):
        """回滚事务"""
        pass

    @abstractmethod
    def create_tables(self):
        """创建表结构"""
        pass

    @abstractmethod
    def insert_knowledge(self, content: str, content_hash: str, source: str,
                        confidence: float, domain: str, user_id: str) -> int:
        """插入知识"""
        pass

    @abstractmethod
    def search_fts(self, query: str, limit: int, domain_filter: str = None,
                   user_id: str = None) -> List[Dict]:
        """全文搜索"""
        pass

    @abstractmethod
    def get_knowledge(self, knowledge_id: int) -> Optional[Dict]:
        """获取知识"""
        pass

    @abstractmethod
    def update_feedback(self, knowledge_id: int, feedback_type: str):
        """更新反馈"""
        pass

    @abstractmethod
    def get_stats(self, user_id: str = None) -> Dict:
        """获取统计"""
        pass

    @abstractmethod
    def health_check(self) -> Dict:
        """健康检查"""
        pass


class SQLiteBackend(StorageBackend):
    """SQLite 存储后端"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None

    def connect(self):
        """建立连接"""
        os.makedirs(os.path.dirname(self.db_path) or '.', exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.row_factory = sqlite3.Row

    def close(self):
        """关闭连接"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def execute(self, query: str, params: Tuple = None) -> List[Tuple]:
        """执行查询"""
        cursor = self.conn.cursor()
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        return cursor.fetchall()

    def execute_many(self, query: str, params_list: List[Tuple]) -> int:
        """批量执行"""
        cursor = self.conn.cursor()
        cursor.executemany(query, params_list)
        self.conn.commit()
        return cursor.rowcount

    def commit(self):
        """提交事务"""
        self.conn.commit()

    def rollback(self):
        """回滚事务"""
        self.conn.rollback()

    def create_tables(self):
        """创建表结构"""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                source TEXT DEFAULT 'unknown',
                confidence REAL DEFAULT 0.5,
                domain TEXT DEFAULT 'general',
                user_id TEXT DEFAULT 'default',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 0,
                last_accessed TIMESTAMP,
                is_archived BOOLEAN DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_knowledge_hash ON knowledge(content_hash);
            CREATE INDEX IF NOT EXISTS idx_knowledge_domain ON knowledge(domain);
            CREATE INDEX IF NOT EXISTS idx_knowledge_user ON knowledge(user_id);

            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                content,
                content=knowledge,
                content_rowid=id
            );

            CREATE TRIGGER IF NOT EXISTS knowledge_ai AFTER INSERT ON knowledge BEGIN
                INSERT INTO knowledge_fts(rowid, content) VALUES (new.id, new.content);
            END;

            CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON knowledge BEGIN
                INSERT INTO knowledge_fts(knowledge_fts, rowid, content) VALUES('delete', old.id, old.content);
            END;

            CREATE TRIGGER IF NOT EXISTS knowledge_au AFTER UPDATE ON knowledge BEGIN
                INSERT INTO knowledge_fts(knowledge_fts, rowid, content) VALUES('delete', old.id, old.content);
                INSERT INTO knowledge_fts(rowid, content) VALUES (new.id, new.content);
            END;
        """)
        self.conn.commit()

    def insert_knowledge(self, content: str, content_hash: str, source: str,
                        confidence: float, domain: str, user_id: str) -> int:
        """插入知识"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO knowledge (content, content_hash, source, confidence, domain, user_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (content, content_hash, source, confidence, domain, user_id))
        self.conn.commit()
        return cursor.lastrowid

    def search_fts(self, query: str, limit: int, domain_filter: str = None,
                   user_id: str = None) -> List[Dict]:
        """全文搜索"""
        sql = """
            SELECT k.*, rank
            FROM knowledge_fts fts
            JOIN knowledge k ON k.id = fts.rowid
            WHERE knowledge_fts MATCH ?
            AND k.is_archived = 0
        """
        params = [query]

        if domain_filter:
            sql += " AND k.domain = ?"
            params.append(domain_filter)

        if user_id:
            sql += " AND k.user_id = ?"
            params.append(user_id)

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        rows = self.execute(sql, tuple(params))
        return [dict(row) for row in rows]

    def get_knowledge(self, knowledge_id: int) -> Optional[Dict]:
        """获取知识"""
        rows = self.execute("SELECT * FROM knowledge WHERE id = ?", (knowledge_id,))
        return dict(rows[0]) if rows else None

    def update_feedback(self, knowledge_id: int, feedback_type: str):
        """更新反馈"""
        if feedback_type == "positive":
            self.execute("""
                UPDATE knowledge SET confidence = MIN(1.0, confidence + 0.1),
                access_count = access_count + 1, last_accessed = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (knowledge_id,))
        elif feedback_type == "negative":
            self.execute("""
                UPDATE knowledge SET confidence = MAX(0.0, confidence - 0.2)
                WHERE id = ?
            """, (knowledge_id,))
        self.commit()

    def get_stats(self, user_id: str = None) -> Dict:
        """获取统计"""
        sql = "SELECT COUNT(*) as total FROM knowledge WHERE is_archived = 0"
        params = []

        if user_id:
            sql += " AND user_id = ?"
            params.append(user_id)

        total = self.execute(sql, tuple(params))[0][0]

        by_source = self.execute("""
            SELECT source, COUNT(*) FROM knowledge
            WHERE is_archived = 0 GROUP BY source
        """)

        by_domain = self.execute("""
            SELECT domain, COUNT(*) FROM knowledge
            WHERE is_archived = 0 GROUP BY domain
        """)

        return {
            "total_entries": total,
            "by_source": dict(by_source),
            "by_domain": dict(by_domain),
        }

    def health_check(self) -> Dict:
        """健康检查"""
        checks = {
            "backend": "sqlite",
            "db_exists": os.path.exists(self.db_path),
            "connected": self.conn is not None,
        }

        try:
            self.execute("SELECT 1")
            checks["queryable"] = True
        except Exception:
            checks["queryable"] = False

        try:
            self.execute("SELECT fts5()")
            checks["fts5_available"] = True
        except Exception:
            checks["fts5_available"] = False

        checks["healthy"] = all([
            checks["db_exists"],
            checks["connected"],
            checks["queryable"],
        ])

        return checks


class PostgreSQLBackend(StorageBackend):
    """PostgreSQL 存储后端"""

    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self.conn = None

        # 解析连接字符串
        parsed = urlparse(connection_string)
        self.host = parsed.hostname or 'localhost'
        self.port = parsed.port or 5432
        self.database = parsed.path.lstrip('/')
        self.user = parsed.username
        self.password = parsed.password

    def connect(self):
        """建立连接"""
        try:
            import psycopg2
            self.conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
            )
            self.conn.autocommit = False
        except ImportError:
            raise ImportError("psycopg2 is required for PostgreSQL backend. Install with: pip install psycopg2-binary")

    def close(self):
        """关闭连接"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def execute(self, query: str, params: Tuple = None) -> List[Tuple]:
        """执行查询"""
        cursor = self.conn.cursor()
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        return cursor.fetchall()

    def execute_many(self, query: str, params_list: List[Tuple]) -> int:
        """批量执行"""
        cursor = self.conn.cursor()
        cursor.executemany(query, params_list)
        self.conn.commit()
        return cursor.rowcount

    def commit(self):
        """提交事务"""
        self.conn.commit()

    def rollback(self):
        """回滚事务"""
        self.conn.rollback()

    def create_tables(self):
        """创建表结构"""
        self.execute("""
            CREATE TABLE IF NOT EXISTS knowledge (
                id SERIAL PRIMARY KEY,
                content TEXT NOT NULL,
                content_hash VARCHAR(64) NOT NULL,
                source VARCHAR(100) DEFAULT 'unknown',
                confidence REAL DEFAULT 0.5,
                domain VARCHAR(50) DEFAULT 'general',
                user_id VARCHAR(100) DEFAULT 'default',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 0,
                last_accessed TIMESTAMP,
                is_archived BOOLEAN DEFAULT FALSE
            )
        """)

        self.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_hash ON knowledge(content_hash)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_domain ON knowledge(domain)")
        self.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_user ON knowledge(user_id)")

        # 创建全文搜索索引
        self.execute("""
            CREATE INDEX IF NOT EXISTS idx_knowledge_fts ON knowledge
            USING gin(to_tsvector('english', content))
        """)

        self.commit()

    def insert_knowledge(self, content: str, content_hash: str, source: str,
                        confidence: float, domain: str, user_id: str) -> int:
        """插入知识"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO knowledge (content, content_hash, source, confidence, domain, user_id)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """, (content, content_hash, source, confidence, domain, user_id))
        result = cursor.fetchone()
        self.conn.commit()
        return result[0]

    def search_fts(self, query: str, limit: int, domain_filter: str = None,
                   user_id: str = None) -> List[Dict]:
        """全文搜索"""
        sql = """
            SELECT id, content, source, confidence, domain, user_id, created_at,
                   ts_rank(to_tsvector('english', content), plainto_tsquery('english', %s)) as score
            FROM knowledge
            WHERE to_tsvector('english', content) @@ plainto_tsquery('english', %s)
            AND is_archived = FALSE
        """
        params = [query, query]

        if domain_filter:
            sql += " AND domain = %s"
            params.append(domain_filter)

        if user_id:
            sql += " AND user_id = %s"
            params.append(user_id)

        sql += " ORDER BY score DESC LIMIT %s"
        params.append(limit)

        rows = self.execute(sql, tuple(params))
        columns = ['id', 'content', 'source', 'confidence', 'domain', 'user_id', 'created_at', 'score']
        return [dict(zip(columns, row)) for row in rows]

    def get_knowledge(self, knowledge_id: int) -> Optional[Dict]:
        """获取知识"""
        rows = self.execute("SELECT * FROM knowledge WHERE id = %s", (knowledge_id,))
        if rows:
            columns = ['id', 'content', 'content_hash', 'source', 'confidence', 'domain',
                      'user_id', 'created_at', 'updated_at', 'access_count', 'last_accessed', 'is_archived']
            return dict(zip(columns, rows[0]))
        return None

    def update_feedback(self, knowledge_id: int, feedback_type: str):
        """更新反馈"""
        if feedback_type == "positive":
            self.execute("""
                UPDATE knowledge SET confidence = LEAST(1.0, confidence + 0.1),
                access_count = access_count + 1, last_accessed = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (knowledge_id,))
        elif feedback_type == "negative":
            self.execute("""
                UPDATE knowledge SET confidence = GREATEST(0.0, confidence - 0.2)
                WHERE id = %s
            """, (knowledge_id,))
        self.commit()

    def get_stats(self, user_id: str = None) -> Dict:
        """获取统计"""
        sql = "SELECT COUNT(*) FROM knowledge WHERE is_archived = FALSE"
        params = []

        if user_id:
            sql += " AND user_id = %s"
            params.append(user_id)

        total = self.execute(sql, tuple(params))[0][0]

        by_source = self.execute("""
            SELECT source, COUNT(*) FROM knowledge
            WHERE is_archived = FALSE GROUP BY source
        """)

        by_domain = self.execute("""
            SELECT domain, COUNT(*) FROM knowledge
            WHERE is_archived = FALSE GROUP BY domain
        """)

        return {
            "total_entries": total,
            "by_source": dict(by_source),
            "by_domain": dict(by_domain),
        }

    def health_check(self) -> Dict:
        """健康检查"""
        checks = {
            "backend": "postgresql",
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "connected": self.conn is not None,
        }

        try:
            self.execute("SELECT 1")
            checks["queryable"] = True
        except Exception as e:
            checks["queryable"] = False
            checks["error"] = str(e)

        checks["healthy"] = all([
            checks["connected"],
            checks.get("queryable", False),
        ])

        return checks


def get_storage_backend(connection_string: str = None) -> StorageBackend:
    """获取存储后端

    Args:
        connection_string: 连接字符串
            - sqlite:///path/to/db (默认)
            - postgresql://user:pass@host:port/db

    Returns:
        StorageBackend 实例
    """
    if not connection_string:
        # 默认使用 SQLite
        db_path = os.path.expanduser("~/.hermes/data/nexus.db")
        connection_string = f"sqlite:///{db_path}"

    if connection_string.startswith("sqlite:///"):
        db_path = connection_string.replace("sqlite:///", "")
        backend = SQLiteBackend(db_path)
    elif connection_string.startswith("postgresql://"):
        backend = PostgreSQLBackend(connection_string)
    else:
        raise ValueError(f"Unsupported storage backend: {connection_string}")

    backend.connect()
    backend.create_tables()
    return backend
