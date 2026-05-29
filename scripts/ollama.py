#!/usr/bin/env python3
"""
Nexus ↔ Ollama Bridge — qwen2.5:3b 做后台摘要/分析/嵌入。

依赖:
  - Windows Ollama 运行中 (http://localhost:11434)
  - Python 标准库 (requests 可选, 优先用 urllib 零依赖)

调用:
  python3 nexus_ollama.py summarize <session_text>
  python3 nexus_ollama.py embed <text>
  python3 nexus_ollama.py analyze-session <session_text>
"""

import json
import os
import sys
import urllib.request
import urllib.error
from typing import Optional

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL_SUMMARIZE = "qwen2.5:3b"
MODEL_EMBED = "nomic-embed-text"


def _call_ollama(model: str, prompt: str, system: str = "",
                  temperature: float = 0.3, max_tokens: int = 512,
                  stream: bool = False) -> Optional[str]:
    """调用 Ollama API 生成文本。纯标准库，零依赖。"""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
    }
    if system:
        payload["system"] = system

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("response", "").strip()
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"[ERROR] Ollama call failed: {e}", file=sys.stderr)
        return None


def _call_ollama_embed(text: str) -> Optional[list]:
    """调用 Ollama embedding API。"""
    payload = {
        "model": MODEL_EMBED,
        "prompt": text,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/embeddings",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("embedding")
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"[ERROR] Ollama embed failed: {e}", file=sys.stderr)
        return None


def cmd_summarize(session_text: str) -> None:
    """把一段 session 文本压缩成 Nexus knowledge entry 格式。"""
    prompt = f"""把下面的对话压缩成一条知识笔记。
格式：主题是什么。决定是什么。偏好是什么（如果有）。
不要重复对话内容，只输出知识笔记。

对话：
{session_text[:2500]}
知识笔记："""
    result = _call_ollama(MODEL_SUMMARIZE, prompt,
                          temperature=0.15, max_tokens=250)
    if result:
        print(result)
    else:
        print("[FAIL] summary failed", file=sys.stderr)
        sys.exit(1)


def cmd_embed(text: str) -> None:
    """生成文本嵌入向量。"""
    vec = _call_ollama_embed(text)
    if vec:
        # 输出为 JSON 数组，方便 pipe
        print(json.dumps(vec))
    else:
        print("[FAIL] embed failed", file=sys.stderr)
        sys.exit(1)


def cmd_analyze_session(session_text: str) -> None:
    """深度分析 session：检测模式、矛盾、偏好。"""
    prompt = f"""分析一段对话，输出 JSON：
{{
  "topics": ["主要话题"],
  "decisions": ["做的决定"],
  "preferences": ["用户偏好"],
  "contradictions": ["前后矛盾的地方（如果没有就写无）"]
}}

对话：
{session_text[:2500]}
JSON："""
    result = _call_ollama(MODEL_SUMMARIZE, prompt,
                          temperature=0.1, max_tokens=400)
    if result:
        # 从输出中提取 JSON（可能有前后杂音）
        try:
            # 找第一个 { 和最后一个 }
            start = result.index("{")
            end = result.rindex("}") + 1
            parsed = json.loads(result[start:end])
            print(json.dumps(parsed, ensure_ascii=False, indent=2))
        except (json.JSONDecodeError, ValueError):
            print(result)  # fallback: 输出原始文本
    else:
        print("[FAIL] analyze failed", file=sys.stderr)
        sys.exit(1)


def cmd_test() -> None:
    """测试连通性 + 模型响应。"""
    # 测试连通
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            models = json.loads(resp.read().decode("utf-8"))
            names = [m["name"] for m in models.get("models", [])]
            print(f"[OK] Ollama reachable. Models: {names}")
    except Exception as e:
        print(f"[FAIL] Ollama unreachable: {e}")
        sys.exit(1)

    # 测试 3B 生成
    print("[TEST] Generating sample...", end=" ", flush=True)
    result = _call_ollama(MODEL_SUMMARIZE, "Hello in one word.",
                          temperature=0.1, max_tokens=10)
    if result:
        print(f"OK → '{result}'")
    else:
        print("FAIL")

    # 测试 embedding
    print("[TEST] Embedding test...", end=" ", flush=True)
    vec = _call_ollama_embed("test")
    if vec and len(vec) > 10:
        print(f"OK → dim={len(vec)}")
    else:
        print("FAIL")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    text = sys.argv[2] if len(sys.argv) > 2 else ""

    if cmd == "test":
        cmd_test()
    elif cmd == "summarize":
        cmd_summarize(text)
    elif cmd == "embed":
        cmd_embed(text)
    elif cmd == "analyze":
        cmd_analyze_session(text)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
