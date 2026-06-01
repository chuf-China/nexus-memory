#!/usr/bin/env python3
"""benchmark.py — 标准性能基准测试

测试项目:
1. 写入性能 (100/1000/10000 条)
2. 搜索性能 (FTS5)
3. 内存使用
4. 并发性能

用法:
  python benchmark.py
  python benchmark.py --size 1000 --iterations 50
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.nexus_core import NexusCore


def measure_write_performance(db_path: str, num_entries: int) -> Dict:
    """测量写入性能"""
    nexus = NexusCore(db_path)
    
    times = []
    for i in range(num_entries):
        content = f"Knowledge entry {i}: This is a test entry for benchmark purposes. "                   f"It contains some text to simulate real knowledge storage. "                   f"Entry number {i} with various keywords like Python, AI, memory, agent."
        
        start = time.perf_counter()
        nexus.write(content, source="benchmark", confidence=0.7, domain="test")
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    
    nexus.close()
    
    return {
        "total_entries": num_entries,
        "total_time": sum(times),
        "avg_ms": statistics.mean(times) * 1000,
        "median_ms": statistics.median(times) * 1000,
        "p95_ms": sorted(times)[int(len(times) * 0.95)] * 1000,
        "p99_ms": sorted(times)[int(len(times) * 0.99)] * 1000,
        "min_ms": min(times) * 1000,
        "max_ms": max(times) * 1000,
        "entries_per_second": num_entries / sum(times) if sum(times) > 0 else 0,
    }


def measure_search_performance(db_path: str, num_queries: int) -> Dict:
    """测量搜索性能"""
    nexus = NexusCore(db_path)
    
    queries = [
        "Python programming",
        "AI agent memory",
        "knowledge base",
        "test entry benchmark",
        "various keywords",
        "performance testing",
        "database search",
        "SQLite FTS5",
        "machine learning",
        "natural language",
    ]
    
    times = []
    for i in range(num_queries):
        query = queries[i % len(queries)]
        
        start = time.perf_counter()
        results = nexus.search(query, limit=10)
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    
    nexus.close()
    
    return {
        "total_queries": num_queries,
        "total_time": sum(times),
        "avg_ms": statistics.mean(times) * 1000,
        "median_ms": statistics.median(times) * 1000,
        "p95_ms": sorted(times)[int(len(times) * 0.95)] * 1000,
        "p99_ms": sorted(times)[int(len(times) * 0.99)] * 1000,
        "min_ms": min(times) * 1000,
        "max_ms": max(times) * 1000,
        "queries_per_second": num_queries / sum(times) if sum(times) > 0 else 0,
    }


def measure_memory_usage(db_path: str) -> Dict:
    """测量内存使用"""
    import psutil
    
    process = psutil.Process()
    
    # 基线内存
    baseline_mb = process.memory_info().rss / 1024 / 1024
    
    # 打开数据库后的内存
    nexus = NexusCore(db_path)
    after_open_mb = process.memory_info().rss / 1024 / 1024
    
    # 搜索后的内存
    for i in range(100):
        nexus.search(f"test query {i}", limit=5)
    after_search_mb = process.memory_info().rss / 1024 / 1024
    
    nexus.close()
    
    return {
        "baseline_mb": round(baseline_mb, 2),
        "after_open_mb": round(after_open_mb, 2),
        "after_search_mb": round(after_search_mb, 2),
        "open_overhead_mb": round(after_open_mb - baseline_mb, 2),
        "search_overhead_mb": round(after_search_mb - after_open_mb, 2),
    }


def measure_concurrent_performance(db_path: str, num_threads: int, ops_per_thread: int) -> Dict:
    """测量并发性能"""
    import concurrent.futures
    import threading
    
    nexus = NexusCore(db_path)
    
    # 预先写入一些数据
    for i in range(100):
        nexus.write(f"Pre-populated entry {i}", source="setup")
    nexus.close()
    
    results = []
    lock = threading.Lock()
    
    def worker(thread_id: int):
        local_nexus = NexusCore(db_path)
        times = []
        
        for i in range(ops_per_thread):
            start = time.perf_counter()
            local_nexus.search(f"query {thread_id} {i}", limit=5)
            elapsed = time.perf_counter() - start
            times.append(elapsed)
        
        local_nexus.close()
        
        with lock:
            results.extend(times)
    
    start_time = time.perf_counter()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        concurrent.futures.wait(futures)
    
    total_time = time.perf_counter() - start_time
    
    return {
        "num_threads": num_threads,
        "ops_per_thread": ops_per_thread,
        "total_ops": len(results),
        "total_time": total_time,
        "avg_ms": statistics.mean(results) * 1000 if results else 0,
        "p95_ms": sorted(results)[int(len(results) * 0.95)] * 1000 if results else 0,
        "ops_per_second": len(results) / total_time if total_time > 0 else 0,
    }


def run_benchmark(size: int = 1000, iterations: int = 100, threads: int = 4):
    """运行完整基准测试"""
    print("=" * 60)
    print("  Nexus Memory 性能基准测试")
    print("=" * 60)
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    try:
        # 1. 写入性能
        print(f"
[1/4] 写入性能测试 ({size} 条)...")
        write_results = measure_write_performance(db_path, size)
        print(f"  平均延迟: {write_results['avg_ms']:.2f} ms")
        print(f"  P95 延迟: {write_results['p95_ms']:.2f} ms")
        print(f"  吞吐量: {write_results['entries_per_second']:.0f} entries/s")
        
        # 2. 搜索性能
        print(f"
[2/4] 搜索性能测试 ({iterations} 次)...")
        search_results = measure_search_performance(db_path, iterations)
        print(f"  平均延迟: {search_results['avg_ms']:.2f} ms")
        print(f"  P95 延迟: {search_results['p95_ms']:.2f} ms")
        print(f"  吞吐量: {search_results['queries_per_second']:.0f} queries/s")
        
        # 3. 内存使用
        print("
[3/4] 内存使用测试...")
        try:
            memory_results = measure_memory_usage(db_path)
            print(f"  基线内存: {memory_results['baseline_mb']:.2f} MB")
            print(f"  打开后内存: {memory_results['after_open_mb']:.2f} MB")
            print(f"  搜索后内存: {memory_results['after_search_mb']:.2f} MB")
        except ImportError:
            print("  ⚠️  psutil 未安装，跳过内存测试")
            memory_results = None
        
        # 4. 并发性能
        print(f"
[4/4] 并发性能测试 ({threads} 线程)...")
        concurrent_results = measure_concurrent_performance(db_path, threads, iterations // threads)
        print(f"  平均延迟: {concurrent_results['avg_ms']:.2f} ms")
        print(f"  P95 延迟: {concurrent_results['p95_ms']:.2f} ms")
        print(f"  吞吐量: {concurrent_results['ops_per_second']:.0f} ops/s")
        
        # 汇总
        print("
" + "=" * 60)
        print("  测试结果汇总")
        print("=" * 60)
        
        summary = {
            "write": write_results,
            "search": search_results,
            "memory": memory_results,
            "concurrent": concurrent_results,
            "config": {
                "size": size,
                "iterations": iterations,
                "threads": threads,
            }
        }
        
        print(f"
写入: {write_results['avg_ms']:.2f}ms avg, {write_results['entries_per_second']:.0f} entries/s")
        print(f"搜索: {search_results['avg_ms']:.2f}ms avg, {search_results['queries_per_second']:.0f} queries/s")
        print(f"并发: {concurrent_results['avg_ms']:.2f}ms avg, {concurrent_results['ops_per_second']:.0f} ops/s")
        
        # 保存结果
        output_file = "benchmark_results.json"
        with open(output_file, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"
详细结果已保存到: {output_file}")
        
        return summary
        
    finally:
        # 清理
        os.unlink(db_path)
        for suffix in ["-wal", "-shm"]:
            try:
                os.unlink(db_path + suffix)
            except FileNotFoundError:
                pass


def main():
    parser = argparse.ArgumentParser(description="Nexus Memory 性能基准测试")
    parser.add_argument("--size", type=int, default=1000, help="写入条目数 (默认: 1000)")
    parser.add_argument("--iterations", type=int, default=100, help="搜索迭代次数 (默认: 100)")
    parser.add_argument("--threads", type=int, default=4, help="并发线程数 (默认: 4)")
    
    args = parser.parse_args()
    
    run_benchmark(args.size, args.iterations, args.threads)


if __name__ == "__main__":
    main()
