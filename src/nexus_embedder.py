"""nexus_embedder.py — v2: 工厂模式嵌入引擎

支持多种嵌入后端，通过配置选择:
  embedder: fastembed  (默认, ONNX本地, 零依赖)
  embedder: openai    (text-embedding-3-small, 需 API key)
  embedder: ollama    (nomic-embed-text, 需本地 Ollama)

用法:
  from agent.nexus_embedder import EmbedderFactory
  embedder = EmbedderFactory.create("fastembed")
  vec = embedder.embed("文本")
"""

from __future__ import annotations

import logging
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 抽象基类 ────────────────────────────────────────────


class BaseEmbedder(ABC):
    """嵌入引擎抽象接口。所有后端必须实现这个。"""

    @abstractmethod
    def embed(self, text: str) -> Optional[List[float]]:
        ...

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        ...

    @property
    @abstractmethod
    def dim(self) -> int:
        ...

    @property
    @abstractmethod
    def available(self) -> bool:
        ...


# ── FastEmbed 后端 ──────────────────────────────────────

class FastEmbedEngine(BaseEmbedder):
    """fastembed ONNX 后端。默认，零依赖。"""

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        self.model_name = model_name
        self._model = None
        self._dim = 512
        self._load()

    def _load(self):
        try:
            from fastembed import TextEmbedding
            import os as _os
            # HF_HUB_OFFLINE: skip network, only use cached models
            old = _os.environ.get("HF_HUB_OFFLINE")
            _os.environ["HF_HUB_OFFLINE"] = "1"
            try:
                self._model = TextEmbedding(model_name=self.model_name)
                logger.info("Embedder: fastembed loaded %s (dim=%d)", self.model_name, self._dim)
            finally:
                if old is None:
                    _os.environ.pop("HF_HUB_OFFLINE", None)
                else:
                    _os.environ["HF_HUB_OFFLINE"] = old
        except Exception as e:
            logger.warning("Embedder: fastembed load failed: %s", e)
            self._model = None

    @property
    def available(self) -> bool:
        return self._model is not None

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> Optional[List[float]]:
        if not self.available or not text:
            return None
        try:
            results = list(self._model.embed([text]))
            return [float(v) for v in results[0]] if results else None
        except Exception as e:
            logger.debug("Embedder(fastembed): %s", e)
            return None

    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        if not self.available or not texts:
            return [None] * len(texts)
        try:
            results = {}
            for i, vec in enumerate(self._model.embed(texts)):
                results[i] = [float(v) for v in vec]
            return [results.get(i) for i in range(len(texts))]
        except Exception as e:
            logger.debug("Embedder(fastembed batch): %s", e)
            return [None] * len(texts)


# ── Ollama 后端 ─────────────────────────────────────────

class OllamaEmbedder(BaseEmbedder):
    """Ollama 嵌入后端。需本地 Ollama 服务。"""

    def __init__(self, model_name: str = "nomic-embed-text",
                 base_url: str = ""):
        self.model_name = model_name
        self.base_url = base_url or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self._dim = 768
        self._client = None
        self._load()

    def _load(self):
        try:
            from agent.nexus_local import OllamaClient
            self._client = OllamaClient(
                base_url=self.base_url,
                embed_model=self.model_name,
            )
            if self._client.ping():
                logger.info("Embedder: Ollama %s ready (dim=%d)", self.model_name, self._dim)
            else:
                self._client = None
        except Exception as e:
            logger.warning("Embedder: Ollama load failed: %s", e)
            self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> Optional[List[float]]:
        if not self.available or not text:
            return None
        try:
            return self._client.embed(text)
        except Exception as e:
            logger.debug("Embedder(ollama): %s", e)
            return None

    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        return [self.embed(t) for t in texts]


# ── OpenAI 后端 ─────────────────────────────────────────

class OpenAIEmbedder(BaseEmbedder):
    """OpenAI 嵌入后端。需 OPENAI_API_KEY。"""

    def __init__(self, model_name: str = "text-embedding-3-small",
                 api_key: str = "", base_url: str = ""):
        self.model_name = model_name
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL", "")
        self._dim = {"text-embedding-3-small": 512,
                     "text-embedding-3-large": 256,
                     "text-embedding-ada-002": 1536}.get(model_name, 1536)
        self._client = None
        self._load()

    def _load(self):
        if not self.api_key:
            logger.warning("Embedder: OpenAI no API key")
            return
        try:
            from openai import OpenAI
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
            logger.info("Embedder: OpenAI %s ready", self.model_name)
        except Exception as e:
            logger.warning("Embedder: OpenAI load failed: %s", e)

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> Optional[List[float]]:
        if not self.available or not text:
            return None
        try:
            r = self._client.embeddings.create(input=text, model=self.model_name)
            return r.data[0].embedding
        except Exception as e:
            logger.debug("Embedder(openai): %s", e)
            return None

    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        if not self.available or not texts:
            return [None] * len(texts)
        try:
            r = self._client.embeddings.create(input=texts, model=self.model_name)
            results = {i: e.embedding for i, e in enumerate(r.data)}
            return [results.get(i) for i in range(len(texts))]
        except Exception as e:
            logger.debug("Embedder(openai batch): %s", e)
            return [None] * len(texts)


# ── 工厂 ────────────────────────────────────────────────

_instances = {}
_lock = threading.Lock()


class EmbedderFactory:
    """嵌入引擎工厂。按配置返回对应后端。"""

    ENGINES = {
        "fastembed": FastEmbedEngine,
        "ollama": OllamaEmbedder,
        "openai": OpenAIEmbedder,
    }

    @classmethod
    def create(cls, engine: str = "fastembed", **kwargs) -> BaseEmbedder:
        """创建或返回缓存的嵌入引擎实例。

        参数:
          engine: "fastembed" | "ollama" | "openai"
          **kwargs: 传递给具体后端的参数 (model_name, api_key 等)
        """
        global _instances
        cache_key = f"{engine}:{hash(frozenset(kwargs.items()))}"
        if cache_key in _instances:
            return _instances[cache_key]

        with _lock:
            if cache_key in _instances:
                return _instances[cache_key]

            engine_cls = cls.ENGINES.get(engine)
            if not engine_cls:
                logger.warning("Embedder: unknown engine '%s', falling back to fastembed", engine)
                engine_cls = FastEmbedEngine

            instance = engine_cls(**kwargs)
            _instances[cache_key] = instance
            return instance

    @classmethod
    def list_engines(cls) -> List[str]:
        return list(cls.ENGINES.keys())


def get_embedder() -> BaseEmbedder:
    """Convenience: get singleton fastembed engine."""
    return EmbedderFactory.create("fastembed")


# ═══════════════════════════════════════════════════════════
# Reranker
# ═══════════════════════════════════════════════════════════


class Reranker:
    """检索结果重排序器。

    策略:
      1. 基于分数的快速重排 (总是可用)
      2. Cross-encoder 精确重排 (若模型可用)

    用法:
      reranker = Reranker()
      reranked = reranker.rerank(query, results)
    """

    def __init__(self):
        self._model = None

    def _load_cross_encoder(self):
        """懒加载 cross-encoder 模型。"""
        if self._model is not None:
            return
        # 优先使用 LoCoMo 微调后的模型
        finetuned_path = Path.home() / ".hermes" / "models" / "locomo-reranker"
        if finetuned_path.exists():
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(
                    str(finetuned_path),
                    max_length=512,
                    device="cpu",
                )
                logger.info("Reranker: LoCoMo-fine-tuned cross-encoder loaded")
                return
            except Exception as e:
                logger.debug("Reranker: fine-tuned model failed: %s", e)

        # 降级: 通用英文 cross-encoder
        try:
            from sentence_transformers import CrossEncoder
            # cross-encoder/ms-marco-MiniLM-L-6-v2: 英文 cross-encoder
            # ~80MB, 快速, 适合英文为主的 LoCoMo 评测
            self._model = CrossEncoder(
                "cross-encoder/ms-marco-MiniLM-L-6-v2",
                max_length=512,
                device="cpu",
            )
            logger.info("Reranker: MiniLM cross-encoder loaded")
        except Exception as e:
            logger.debug("Reranker: MiniLM failed, trying multilingual: %s", e)
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(
                    "BAAI/bge-reranker-v2-m3",
                    max_length=512,
                    device="cpu",
                )
                logger.info("Reranker: bge-reranker-v2-m3 loaded")
            except Exception as e2:
                logger.debug("Reranker: all cross-encoders failed: %s", e2)
                self._model = "score_only"

    def rerank(self, query: str,
               results: List[Dict[str, Any]],
               top_k: int = 5) -> List[Dict[str, Any]]:
        """对搜索结果重排序。

        参数:
          query: 原查询文本
          results: 多路召回合并后的结果列表
          top_k: 返回 top-K

        返回: 重排后的结果列表 (每个 item 追加 rerank_score)
        """
        if not results:
            return []

        # 1. 基于分数的快速排序 (所有模式可用)
        # 合并各策略分数: FTS5 rank / 向量 similarity / 图 score / 时序 rank
        for r in results:
            score = (
                r.get("similarity") or  # 向量
                r.get("score") or       # 图
                0.5                      # 默认 (FTS5/时序)
            )
            # 加分: 正反馈多、负反馈少
            pf = r.get("positive_feedback", 0) or 0
            nf = r.get("negative_feedback", 0) or 0
            feedback_boost = (pf - nf * 2) * 0.05

            # 加分: consolidated > candidate > instant
            layer = r.get("layer", "")
            layer_boost = {"consolidated": 0.1, "candidate": 0.05, "instant": 0.0}.get(layer, 0)

            r["rerank_score"] = round(score + feedback_boost + layer_boost, 4)

        # 2. Cross-encoder 精确重排 (若可用)
        self._load_cross_encoder()
        if self._model and self._model != "score_only":
            try:
                pairs = [(query, r.get("content", "") or r.get("summary", "") or "")
                         for r in results]
                # sentence_transformers CrossEncoder → predict()
                cross_scores = self._model.predict(pairs)
                for i, cs in enumerate(cross_scores):
                    if i < len(results):
                        results[i]["rerank_score"] = round(
                            results[i]["rerank_score"] * 0.3 + float(cs) * 0.7, 4
                        )
            except Exception as e:
                logger.debug("Reranker: cross-encoder scoring failed: %s", e)

        # 按 rerank_score 降序
        results.sort(key=lambda x: -x["rerank_score"])
        return results[:top_k]
