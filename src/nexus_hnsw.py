"""nexus_hnsw.py — HNSW 向量索引加速器

将 nexus_core._search_semantic() 中的 O(n) 线性扫描替换为 O(log n) 近似最近邻搜索。

用法:
  from agent.nexus_hnsw import HNSWIndex
  idx = HNSWIndex(conn, dim=512)
  idx.build()           # 从 knowledge_embeddings 表重建
  idx.search(query_vec, k=10)  # 返回 [(entry_id, score), ...]

降级策略:
  - hnswlib 不可用 → build() 返回 False, search() 返回 None
  - 调用方感知降级 → 回退到线性扫描
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import struct
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_HNSW_AVAILABLE = False
try:
    import hnswlib
    _HNSW_AVAILABLE = True
except ImportError:
    pass

# HNSW 索引文件路径
_HNSW_INDEX_DIR = Path.home() / ".hermes" / "hnsw_index"


class HNSWIndex:
    """HNSW 近似最近邻索引。线程安全。"""

    def __init__(self, conn, dim: int = 512,
                 space: str = "cosine",
                 ef_construction: int = 100,
                 M: int = 16):
        self.conn = conn
        self.dim = dim
        self.space = space
        self.ef_construction = ef_construction
        self.M = M
        self._index: Any = None
        self._entry_ids: List[int] = []  # index position → SQLite entry_id
        self._lock = threading.Lock()
        self._built = False
        self._available = _HNSW_AVAILABLE

        # 确保索引目录存在
        _HNSW_INDEX_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def available(self) -> bool:
        return self._available and self._built

    def _index_path(self) -> Path:
        db_path = self.conn.execute("PRAGMA database_list").fetchone()[2]
        db_hash = hashlib.md5(db_path.encode()).hexdigest()[:12]
        return _HNSW_INDEX_DIR / f"nexus_{db_hash}.bin"

    def _ids_path(self) -> Path:
        db_path = self.conn.execute("PRAGMA database_list").fetchone()[2]
        db_hash = hashlib.md5(db_path.encode()).hexdigest()[:12]
        return _HNSW_INDEX_DIR / f"nexus_{db_hash}_ids.json"

    def build(self, force: bool = False, max_elements: int = 200000) -> bool:
        """从 knowledge_embeddings 重建 HNSW 索引。

        Args:
            force: 如果 True，即使索引文件存在也强制重建
            max_elements: 索引最大容量（预分配）

        Returns: True 如果索引构建成功
        """
        if not _HNSW_AVAILABLE:
            logger.warning("HNSW: hnswlib not installed, falling back to linear scan")
            return False

        # 如果索引已存在且有效，直接加载
        if not force and self._index_path().exists() and self._ids_path().exists():
            if self._load():
                return True

        with self._lock:
            try:
                # 从 DB 读取所有 embedding
                rows = self.conn.execute(
                    "SELECT entry_id, embedding, embed_dim "
                    "FROM knowledge_embeddings"
                ).fetchall()

                if not rows:
                    logger.debug("HNSW: no embeddings to index")
                    return False

                # 收集向量
                vectors = []
                entry_ids = []
                for r in rows:
                    dim = r["embed_dim"] or self.dim
                    blob = r["embedding"]
                    if not blob or len(blob) < dim * 4:
                        continue
                    try:
                        vec = struct.unpack(f"{dim}f", blob[:dim * 4])
                        vectors.append(vec)
                        entry_ids.append(r["entry_id"])
                    except Exception:
                        continue

                if not vectors:
                    logger.warning("HNSW: no valid vectors found")
                    return False

                num_elements = len(vectors)
                logger.info("HNSW: building index for %d vectors (dim=%d)", num_elements, self.dim)

                # 初始化 HNSW 索引
                self._index = hnswlib.Index(space=self.space, dim=self.dim)
                self._index.init_index(
                    max_elements=max(max_elements, num_elements * 2),
                    ef_construction=self.ef_construction,
                    M=self.M,
                )

                # 批量添加
                import numpy as np
                data = np.array(vectors, dtype=np.float32)
                self._index.add_items(data, list(range(num_elements)))
                self._entry_ids = entry_ids
                self._built = True

                # 设置查询参数
                self._index.set_ef(50)

                # 持久化
                self._save()

                logger.info("HNSW: index built with %d entries (%.1fMB)",
                           num_elements,
                           (num_elements * self.dim * 4) / (1024 * 1024))
                return True

            except Exception as e:
                logger.warning("HNSW: build failed: %s", e)
                self._built = False
                return False

    def search(self, query_vec, k: int = 10) -> Optional[List[Tuple[int, float]]]:
        """HNSW 近似最近邻搜索。

        Args:
            query_vec: float list, query embedding
            k: 返回结果数 (clamped to index size)

        Returns: [(entry_id, cosine_similarity), ...] 或 None (索引未就绪)
        """
        if not self.available or self._index is None:
            return None

        with self._lock:
            try:
                import numpy as np
                q = np.array([query_vec], dtype=np.float32)
                actual_k = min(k, len(self._entry_ids))
                labels, distances = self._index.knn_query(q, k=actual_k)

                # HNSW cosine 距离 ≈ 0 表示最相似，1 表示最不相似
                # 转为 cosine similarity: sim = 1 - distance
                results = []
                for label, dist in zip(labels[0], distances[0]):
                    entry_id = self._entry_ids[label]
                    similarity = 1.0 - float(dist)
                    results.append((entry_id, similarity))

                return results

            except Exception as e:
                logger.debug("HNSW search failed: %s", e)
                return None

    def add_entries(self, entry_ids: List[int]) -> bool:
        """增量添加新 embedding 到索引。

        当新的 knowledge_embeddings 写入后调用。
        简单实现: 因为 HNSW 支持动态添加，但为了一致性，
        这里直接重建索引。
        """
        if not self._built:
            return self.build()
        return self.build(force=True)

    def _save(self) -> bool:
        """持久化索引到磁盘"""
        try:
            if self._index is not None:
                self._index.save_index(str(self._index_path()))
                with open(self._ids_path(), "w") as f:
                    json.dump(self._entry_ids, f)
                logger.debug("HNSW: index saved (%d entries)", len(self._entry_ids))
                return True
        except Exception as e:
            logger.debug("HNSW save failed: %s", e)
        return False

    def _load(self) -> bool:
        """从磁盘加载索引"""
        try:
            idx_path = self._index_path()
            ids_path = self._ids_path()

            if not idx_path.exists() or not ids_path.exists():
                return False

            with open(ids_path) as f:
                self._entry_ids = json.load(f)

            num_elements = len(self._entry_ids)
            if num_elements == 0:
                return False

            self._index = hnswlib.Index(space=self.space, dim=self.dim)
            self._index.load_index(str(idx_path), max_elements=max(num_elements * 2, 1000))
            self._index.set_ef(50)
            self._built = True

            logger.debug("HNSW: index loaded (%d entries)", num_elements)
            return True

        except Exception as e:
            logger.debug("HNSW load failed: %s", e)
            self._built = False
            return False

    def status(self) -> Dict[str, Any]:
        """索引状态"""
        return {
            "available": self.available,
            "hnswlib_installed": _HNSW_AVAILABLE,
            "entry_count": len(self._entry_ids) if self._built else 0,
            "dim": self.dim,
        }
