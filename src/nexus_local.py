"""
Nexus Local LLM — Connects Nexus to Windows Ollama for:
- Semantic search embeddings (nomic-embed-text, 768-dim)
- AI-powered summarization (qwen3.5:35b-a3b)
- Pattern recognition / entity extraction
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default Ollama endpoint (Windows Ollama accessible from WSL)
OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Models
EMBED_MODEL = os.environ.get("NEXUS_EMBED_MODEL", "nomic-embed-text")
SUMMARY_MODEL = os.environ.get("NEXUS_SUMMARY_MODEL", "qwen2.5-coder:7b")

# Timeout (seconds)
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "60"))

# Embedding dimension
EMBED_DIM = 768


class OllamaClient:
    """Client for Windows Ollama instance, reachable via WSL."""

    def __init__(
        self,
        base_url: str = OLLAMA_BASE,
        embed_model: str = EMBED_MODEL,
        summary_model: str = SUMMARY_MODEL,
        timeout: int = OLLAMA_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.embed_model = embed_model
        self.summary_model = summary_model
        self.timeout = timeout
        self._available: Optional[Dict[str, bool]] = None

    # ------------------------------------------------------------------
    # Health / availability
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """Check if Ollama server is reachable."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def check_models(self) -> Dict[str, bool]:
        """Return {model_name: is_available} for configured models."""
        result = {self.embed_model: False, self.summary_model: False}
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                local_models = {m["name"].split(":")[0] for m in data.get("models", [])}
                for model_tag in [self.embed_model, self.summary_model]:
                    name = model_tag.split(":")[0]
                    result[model_tag] = name in local_models or any(
                        model_tag in m["name"] for m in data.get("models", [])
                    )
        except Exception as e:
            logger.warning(f"check_models failed: {e}")
        self._available = result
        return result

    def is_available(self, model: Optional[str] = None) -> bool:
        """Check if a specific model is available."""
        if self._available is None:
            self.check_models()
        if model is None:
            target = self.summary_model
        else:
            target = model
        return self._available.get(target, False) if self._available else False

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    def embed(self, text: str) -> Optional[List[float]]:
        """Generate embedding vector for a single text string.

        Returns a list of 768 floats on success, or None on failure.
        """
        if not text or not text.strip():
            return None

        try:
            payload = json.dumps({"model": self.embed_model, "input": text}).encode()
            req = urllib.request.Request(
                f"{self.base_url}/api/embed",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read())
                embeddings = result.get("embeddings", [])
                if embeddings:
                    return embeddings[0]
                return None
        except Exception as e:
            logger.warning(f"embed failed: {e}")
            return None

    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """Batch embedding — more efficient for multiple texts."""
        if not texts:
            return []

        try:
            payload = json.dumps({"model": self.embed_model, "input": texts}).encode()
            req = urllib.request.Request(
                f"{self.base_url}/api/embed",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout * 3) as resp:
                result = json.loads(resp.read())
                return result.get("embeddings", [])
        except Exception as e:
            logger.warning(f"embed_batch failed: {e}")
            return [None] * len(texts)

    # ------------------------------------------------------------------
    # Summarization
    # ------------------------------------------------------------------

    def summarize(self, content: str, max_length: int = 300) -> Optional[str]:
        """Generate an AI-powered summary using the local LLM.

        Args:
            content: Text to summarize
            max_length: Maximum summary length in tokens

        Returns:
            Summary string, or None if summarization fails
        """
        if not content or not content.strip():
            return None

        if not self.is_available(self.summary_model):
            logger.info(f"Summary model {self.summary_model} not available")
            return None

        # Truncate input to avoid hitting context limits
        truncated = content[:8000] if len(content) > 8000 else content

        prompt = (
            "Summarize the following knowledge entry concisely. "
            "Extract the key fact, decision, or insight in 1-2 sentences.\n\n"
            f"Content:\n{truncated}\n\n"
            "Summary:"
        )

        try:
            payload = json.dumps({
                "model": self.summary_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "max_tokens": max_length,
                },
            }).encode()

            req = urllib.request.Request(
                f"{self.base_url}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read())
                summary = result.get("message", {}).get("content", "").strip()
                if summary:
                    # Clean up common prefixes from local LLMs
                    for prefix in [
                        "Summary:", "summary:", "Key insight:", "Key fact:",
                        "Here's a summary:", "The knowledge entry describes",
                    ]:
                        if summary.startswith(prefix):
                            summary = summary[len(prefix) :].strip()
                    return summary[:500]
                return None
        except Exception as e:
            logger.warning(f"summarize failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Pattern detection
    # ------------------------------------------------------------------

    def detect_patterns(self, entries: List[Dict[str, Any]]) -> Optional[str]:
        """Analyze multiple knowledge entries for patterns / insights.

        Args:
            entries: List of nexus entries, each with at least 'content' key.

        Returns:
            Pattern analysis text, or None if analysis fails.
        """
        if not entries:
            return None

        if not self.is_available(self.summary_model):
            return None

        # Prepare input — concatenate entry summaries
        entry_texts = []
        for i, entry in enumerate(entries[:10]):  # Max 10 entries
            content = entry.get("content", "")[:500]
            if content:
                entry_texts.append(f"Entry {i+1}: {content}")

        if not entry_texts:
            return None

        context = "\n\n".join(entry_texts)

        prompt = (
            "Analyze the following knowledge entries and identify:\n"
            "1. Recurring themes or patterns\n"
            "2. Contradictions or conflicts between entries\n"
            "3. Actionable insights\n\n"
            f"Entries:\n{context}\n\n"
            "Pattern Analysis:"
        )

        try:
            payload = json.dumps({
                "model": self.summary_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {
                    "temperature": 0.2,
                    "max_tokens": 500,
                },
            }).encode()

            req = urllib.request.Request(
                f"{self.base_url}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout * 2) as resp:
                result = json.loads(resp.read())
                return result.get("message", {}).get("content", "").strip()[:1000]
        except Exception as e:
            logger.warning(f"detect_patterns failed: {e}")
            return None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_client: Optional[OllamaClient] = None


def get_client() -> OllamaClient:
    """Get or create the global OllamaClient singleton."""
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client


def reset_client():
    """Reset the global client (useful for testing / config change)."""
    global _client
    _client = None
