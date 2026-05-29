"""nexus_hnsw.py — HNSW 向量索引加速器

多后端支持:
  - hnswlib (默认): C++ HNSW 实现, 成熟稳定
  - usearch: 高性能向量搜索, 支持百万级

用法:
  from .hnsw import HNSWIndex
  idx = HNSWIndex(conn, dim=512)
  idx.build()           # 从 knowledge_embeddings 表重建
  idx.search(query_vec, k=10)  # 返回 [(entry_id, score), ...]

降级策略:
  - hnswlib/usearch 都不可用 → build() 返回 False, search() 返回 None
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

_USEARCH_AVAILABLE = False
try:
    import usearch
    _USEARCH_AVAILABLE = True
except ImportError:
    pass

# HNSW 索引文件路径
_HNSW_INDEX_DIR = Path.home() / ".hermes" / "hnsw_index"

# Global instance cache: (db_path, dim) → HNSWIndex
_instance_cache: Dict[str, "HNSWIndex"] = {}
_cache_lock = threading.Lock()


def get_hnsw_index(conn, dim: int = 512, **kwargs) -> "HNSWIndex":
    """Get or create a cached HNSWIndex for the given connection."""
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    key = f"{db_path}:{dim}"
    if key in _instance_cache:
        return _instance_cache[key]
    with _cache_lock:
        if key in _instance_cache:
            return _instance_cache[key]
        idx = HNSWIndex(conn, dim=dim, **kwargs)
        _instance_cache[key] = idx
        return idx


class HNSWIndex:
    """HNSW 近似最近邻索引。线程安全。支持 hnswlib 和 usearch 后端。"""

    def __init__(self, conn, dim: int = 512,
                 space: str = "cosine",
                 ef_construction: int = 100,
                 M: int = 16,
                 backend: str = "auto"):
        """
        Args:
            backend: "hnswlib" | "usearch" | "auto" (prefer usearch if available)
        """
        self.conn = conn
        self.dim = dim
        self.space = space
        self.ef_construction = ef_construction
        self.M = M
        self._index: Any = None
        self._entry_ids: List[int] = []  # index position → SQLite entry_id
        self._lock = threading.Lock()
        self._built = False

        # Select backend
        if backend == "auto":
            if _USEARCH_AVAILABLE:
                self._backend = "usearch"
            elif _HNSW_AVAILABLE:
                self._backend = "hnswlib"
            else:
                self._backend = "none"
        else:
            self._backend = backend

        self._available = self._backend != "none"

        # 确保索引目录存在
        _HNSW_INDEX_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def available(self) -> bool:
        return self._available and self._built

    def _index_path(self) -> Path:
        db_path = self.conn.execute("PRAGMA database_list").fetchone()[2]
        db_hash = hashlib.md5(db_path.encode()).hexdigest()[:12]
        suffix = "usearch" if self._backend == "usearch" else "bin"
        return _HNSW_INDEX_DIR / f"nexus_{db_hash}.{suffix}"

    def _ids_path(self) -> Path:
        db_path = self.conn.execute("PRAGMA database_list").fetchone()[2]
        db_hash = hashlib.md5(db_path.encode()).hexdigest()[:12]
        return _HNSW_INDEX_DIR / f"nexus_{db_hash}_ids.json"

    def build(self, force: bool = False, max_elements: int = 200000) -> bool:
        """从 knowledge_embeddings 重建索引。

        Args:
            force: 如果 True，即使索引文件存在也强制重建
            max_elements: 索引最大容量（预分配）

        Returns: True 如果索引构建成功
        """
        if self._backend == "none":
            logger.warning("HNSW: no backend available, falling back to linear scan")
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
                logger.info("HNSW: building %s index for %d vectors (dim=%d)",
                           self._backend, num_elements, self.dim)

                if self._backend == "usearch":
                    self._build_usearch(vectors, entry_ids, max_elements)
                else:
                    self._build_hnswlib(vectors, entry_ids, max_elements)

                self._built = True
                self._save()

                logger.info("HNSW: index built with %d entries (%.1fMB)",
                           num_elements,
                           (num_elements * self.dim * 4) / (1024 * 1024))
                return True

            except Exception as e:
                logger.warning("HNSW: build failed: %s", e)
                self._built = False
                return False

    def _build_hnswlib(self, vectors, entry_ids, max_elements):
        """Build index using hnswlib backend."""
        import numpy as np
        num_elements = len(vectors)

        self._index = hnswlib.Index(space=self.space, dim=self.dim)
        self._index.init_index(
            max_elements=max(max_elements, num_elements * 2),
            ef_construction=self.ef_construction,
            M=self.M,
        )

        data = np.array(vectors, dtype=np.float32)
        self._index.add_items(data, list(range(num_elements)))
        self._entry_ids = entry_ids
        self._index.set_ef(50)

    def _build_usearch(self, vectors, entry_ids, max_elements):
        """Build index using usearch backend."""
        import numpy as np
        num_elements = len(vectors)

        metric = usearch.MetricKind.Cos if self.space == "cosine" else usearch.MetricKind.L2
        self._index = usearch.Index(ndim=self.dim, metric=metric)

        data = np.array(vectors, dtype=np.float32)
        self._index.add(np.arange(num_elements, dtype=np.uint64), data)
        self._entry_ids = entry_ids

    def search(self, query_vec, k: int = 10) -> Optional[List[Tuple[int, float]]]:
        """近似最近邻搜索。

        Args:
            query_vec: float list, query embedding
            k: 返回结果数

        Returns: [(entry_id, cosine_similarity), ...] 或 None
        """
        if not self.available or self._index is None:
            return None

        with self._lock:
            try:
                import numpy as np
                q = np.array([query_vec], dtype=np.float32)
                actual_k = min(k, len(self._entry_ids))

                if self._backend == "usearch":
                    return self._search_usearch(q, actual_k)
                else:
                    return self._search_hnswlib(q, actual_k)

            except Exception as e:
                logger.debug("HNSW search failed: %s", e)
                return None

    def _search_hnswlib(self, q, k):
        """Search using hnswlib."""
        labels, distances = self._index.knn_query(q, k=k)
        results = []
        for label, dist in zip(labels[0], distances[0]):
            entry_id = self._entry_ids[label]
            similarity = 1.0 - float(dist)
            results.append((entry_id, similarity))
        return results

    def _search_usearch(self, q, k):
        """Search using usearch."""
        matches = self._index.search(q, k)
        results = []
        for i in range(len(matches)):
            label = int(matches.keys[i])
            distance = float(matches.distances[i])
            if label < len(self._entry_ids):
                entry_id = self._entry_ids[label]
                # usearch cosine: 0=identical, 2=opposite
                similarity = 1.0 - distance / 2.0
                results.append((entry_id, similarity))
        return results

    def add_entries(self, entry_ids: List[int]) -> bool:
        """增量添加新 embedding 到索引。"""
        if not self._built:
            return self.build()

        if self._backend == "none" or self._index is None:
            return self.build()

        if len(entry_ids) > 5000 or len(self._entry_ids) > 200000:
            return self.build(force=True)

        with self._lock:
            try:
                import numpy as np

                existing_set = set(self._entry_ids)
                new_ids = [eid for eid in entry_ids if eid not in existing_set]
                if not new_ids:
                    return True

                placeholders = ",".join("?" for _ in range(len(new_ids)))
                rows = self.conn.execute(
                    f"SELECT entry_id, embedding, embed_dim "
                    f"FROM knowledge_embeddings WHERE entry_id IN ({placeholders})",
                    new_ids
                ).fetchall()

                vectors = []
                ids_to_add = []
                for r in rows:
                    dim = r["embed_dim"] or self.dim
                    blob = r["embedding"]
                    if not blob or len(blob) < dim * 4:
                        continue
                    try:
                        vec = struct.unpack(f"{dim}f", blob[:dim * 4])
                        vectors.append(vec)
                        ids_to_add.append(r["entry_id"])
                    except Exception:
                        continue

                if not vectors:
                    return True

                if self._backend == "usearch":
                    data = np.array(vectors, dtype=np.float32)
                    start_pos = len(self._entry_ids)
                    self._index.add(
                        np.arange(start_pos, start_pos + len(vectors), dtype=np.uint64),
                        data
                    )
                else:
                    # hnswlib: check capacity
                    needed = len(self._entry_ids) + len(vectors)
                    if needed > self._index.get_max_elements():
                        logger.info("HNSW: capacity exceeded, rebuilding")
                        return self.build(force=True)
                    data = np.array(vectors, dtype=np.float32)
                    start_pos = len(self._entry_ids)
                    self._index.add_items(data, list(range(start_pos, start_pos + len(vectors))))

                self._entry_ids.extend(ids_to_add)
                self._built = True
                self._save()
                logger.info("HNSW: added %d entries incrementally (total=%d)", len(vectors), len(self._entry_ids))
                return True

            except Exception as e:
                logger.warning("HNSW: incremental add failed (%s), falling back to rebuild", e)
                return self.build(force=True)

    def _save(self) -> bool:
        """持久化索引到磁盘"""
        try:
            if self._index is not None:
                if self._backend == "usearch":
                    self._index.save(str(self._index_path()))
                else:
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

            if self._backend == "usearch":
                metric = usearch.MetricKind.Cos if self.space == "cosine" else usearch.MetricKind.L2
                self._index = usearch.Index(ndim=self.dim, metric=metric)
                self._index.load(str(idx_path))
            else:
                self._index = hnswlib.Index(space=self.space, dim=self.dim)
                self._index.load_index(str(idx_path), max_elements=max(num_elements * 2, 1000))
                self._index.set_ef(50)

            self._built = True
            logger.debug("HNSW: index loaded (%d entries, %s)", num_elements, self._backend)
            return True

        except Exception as e:
            logger.debug("HNSW load failed: %s", e)
            self._built = False
            return False

    def status(self) -> Dict[str, Any]:
        """索引状态"""
        return {
            "available": self.available,
            "backend": self._backend,
            "hnswlib_installed": _HNSW_AVAILABLE,
            "usearch_installed": _USEARCH_AVAILABLE,
            "entry_count": len(self._entry_ids) if self._built else 0,
            "dim": self.dim,
        }
