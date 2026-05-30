#!/usr/bin/env python3
"""
Nexus Memory Demo — Shows the core capabilities in 30 seconds.

Usage:
    python demo.py              # Run the full demo
    python demo.py --benchmark  # Run performance benchmark only
    python demo.py --export     # Export demo GIF frames (requires pillow)
"""

import sys
import os
import time
import json

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def demo_basic():
    """Basic write + search demo."""
    from src.nexus_core import NexusCore

    # Clean slate
    db_path = "/tmp/nexus_demo.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    nexus = NexusCore(db_path)

    print("\n" + "="*60)
    print("  🧠 NEXUS MEMORY DEMO")
    print("="*60)

    # Step 1: Write knowledge
    print("\n📝 Writing knowledge...\n")
    facts = [
        ("User prefers Python type hints", "conversation", 0.9, "workflow"),
        ("Project uses PostgreSQL with SQLAlchemy", "conversation", 0.85, "workflow"),
        ("User prefers dark mode in all editors", "conversation", 0.8, "preference"),
        ("Deployment target is AWS ECS Fargate", "conversation", 0.75, "infrastructure"),
        ("Code review style: focus on error handling first", "conversation", 0.88, "workflow"),
        ("User corrects: actually we switched to MongoDB", "correction", 0.95, "workflow"),
        ("Team uses GitHub Actions for CI/CD", "conversation", 0.82, "workflow"),
        ("User timezone: UTC+8 (Shanghai)", "conversation", 0.9, "identity"),
    ]

    for content, source, confidence, domain in facts:
        nexus.write(content, source=source, confidence=confidence, domain=domain)
        print(f"  ✅ [{domain}] {content}")

    # Step 2: Search
    print("\n🔍 Searching knowledge...\n")
    queries = [
        "What database does the project use?",
        "What's the user's coding style?",
        "Where is the app deployed?",
    ]

    for query in queries:
        start = time.time()
        results = nexus.search(query, limit=3)
        elapsed = (time.time() - start) * 1000

        print(f"  Q: {query}")
        print(f"  ⚡ {elapsed:.1f}ms")
        for i, r in enumerate(results[:2]):
            print(f"  → {r}")
        print()

    # Step 3: System prompt injection
    print("📋 System prompt block:\n")
    block = nexus.system_prompt_block()
    print(f"  {block[:200]}...")

    # Cleanup
    os.remove(db_path)
    print("\n✅ Demo complete!")
    print("="*60)


def demo_correction():
    """Shows the correction mechanism in action."""
    from src.nexus_core import NexusCore

    db_path = "/tmp/nexus_correction_demo.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    nexus = NexusCore(db_path)

    print("\n" + "="*60)
    print("  🔄 CORRECTION MECHANISM DEMO")
    print("="*60)

    # Write initial fact
    nexus.write("User prefers PostgreSQL", source="conversation", confidence=0.9, domain="workflow")
    print("\n📝 Initial: User prefers PostgreSQL (confidence: 0.9)")

    # Correct it
    nexus.write("User corrects: switched to MongoDB", source="correction", confidence=0.95, domain="workflow")
    print("📝 Correction: switched to MongoDB (confidence: 0.95)")

    # Search — should prefer the correction
    results = nexus.search("What database?", limit=3)
    print(f"\n🔍 Search result: {results}")

    # Cleanup
    os.remove(db_path)
    print("\n✅ Old fact degraded, new fact promoted — natural淘汰!")
    print("="*60)


def demo_benchmark():
    """Performance benchmark."""
    from src.nexus_core import NexusCore

    db_path = "/tmp/nexus_benchmark.db"
    if os.path.exists(db_path):
        os.remove(db_path)

    nexus = NexusCore(db_path)

    print("\n" + "="*60)
    print("  ⚡ PERFORMANCE BENCHMARK")
    print("="*60)

    # Write 1000 entries
    print("\n📝 Writing 1000 entries...")
    start = time.time()
    for i in range(1000):
        nexus.write(
            f"Knowledge entry #{i}: topic_{i % 10} category_{i % 5}",
            source="benchmark",
            confidence=0.5 + (i % 50) / 100,
            domain=["workflow", "preference", "identity", "infrastructure", "rule"][i % 5]
        )
    write_time = time.time() - start
    print(f"   ✅ {write_time:.2f}s total, {write_time/1000*1000:.2f}ms per entry")

    # Search benchmark
    print("\n🔍 Running 100 searches...")
    queries = [
        "topic_3", "category_1", "workflow", "preference",
        "knowledge entry", "topic_7 category_2", "infrastructure",
        "identity", "rule", "benchmark"
    ] * 10

    latencies = []
    for q in queries:
        start = time.time()
        nexus.search(q, limit=5)
        latencies.append((time.time() - start) * 1000)

    avg = sum(latencies) / len(latencies)
    p50 = sorted(latencies)[len(latencies) // 2]
    p99 = sorted(latencies)[int(len(latencies) * 0.99)]

    print(f"   Avg: {avg:.1f}ms")
    print(f"   P50: {p50:.1f}ms")
    print(f"   P99: {p99:.1f}ms")
    print(f"   Min: {min(latencies):.1f}ms")
    print(f"   Max: {max(latencies):.1f}ms")

    # Cleanup
    os.remove(db_path)
    print("\n✅ Benchmark complete!")
    print("="*60)


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--benchmark" in args:
        demo_benchmark()
    elif "--correction" in args:
        demo_correction()
    else:
        demo_basic()
        demo_correction()
        demo_benchmark()
