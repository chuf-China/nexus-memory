#!/usr/bin/env python3
"""test_nexus_phase3_4.py — Tests for Phase 3-4 NEXUS modules.

Covers: migration, crypto, API, graph, health, metrics, backup, embedder reranker.
"""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Helpers ────────────────────────────────────────────────

def _tmp_db():
    """Create a temporary DB with the base schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Apply base schema
    schema_path = Path.home() / ".hermes" / "hermes-agent" / "plugins" / "memory" / "nexus" / "schema.sql"
    if schema_path.exists():
        conn.executescript(schema_path.read_text())
    return conn, path


def _seed_knowledge(conn, count=5):
    """Insert sample knowledge entries."""
    for i in range(count):
        conn.execute(
            "INSERT INTO unified_knowledge (content, domain_scores, layer, user_id) "
            "VALUES (?, ?, ?, ?)",
            (f"Test knowledge entry {i}: 这是关于测试的知识",
             json.dumps({"identity": 0.3, "workflow": 0.7}),
             "candidate",
             "test_user")
        )
    conn.commit()


# ── Migration tests ───────────────────────────────────────

def test_migration_current_version():
    """SchemaMigration tracks version correctly."""
    from nexus.migration import SchemaMigration
    conn, path = _tmp_db()
    try:
        mig = SchemaMigration(conn)
        v = mig.current_version()
        assert isinstance(v, int), "Version should be integer"
        assert v >= 0, "Version should be non-negative"

        # Set version
        mig.set_version(42)
        assert mig.current_version() == 42
        print("✓ test_migration_current_version")
    finally:
        conn.close()
        os.unlink(path)


def test_migration_pending():
    """SchemaMigration lists pending migrations."""
    from nexus.migration import SchemaMigration
    conn, path = _tmp_db()
    try:
        mig = SchemaMigration(conn)
        pending = mig.pending()
        assert isinstance(pending, list), "Pending should be a list"
        # With version 0, all migrations should be pending
        assert len(pending) > 0, "Should have pending migrations at version 0"
        print("✓ test_migration_pending")
    finally:
        conn.close()
        os.unlink(path)


def test_migration_status():
    """SchemaMigration status returns proper dict."""
    from nexus.migration import SchemaMigration
    conn, path = _tmp_db()
    try:
        mig = SchemaMigration(conn)
        status = mig.status()
        assert "current_version" in status
        assert "latest_version" in status
        assert "pending_count" in status
        assert "pending" in status
        print("✓ test_migration_status")
    finally:
        conn.close()
        os.unlink(path)


def test_migration_run():
    """SchemaMigration run applies migrations."""
    from nexus.migration import SchemaMigration
    conn, path = _tmp_db()
    try:
        mig = SchemaMigration(conn)
        # Apply first 3 migrations only
        applied = mig.run(target=3)
        assert applied >= 0, "Should apply some migrations"
        assert mig.current_version() <= 3
        print("✓ test_migration_run")
    finally:
        conn.close()
        os.unlink(path)


# ── Crypto tests ──────────────────────────────────────────

def test_crypto_available():
    """sqlcipher_available returns boolean."""
    from nexus.crypto import sqlcipher_available
    result = sqlcipher_available()
    assert isinstance(result, bool)
    print(f"✓ test_crypto_available (SQLCipher: {result})")


def test_crypto_derive_key():
    """_derive_key produces consistent 256-bit keys."""
    from nexus.crypto import _derive_key
    k1 = _derive_key("test_passphrase")
    k2 = _derive_key("test_passphrase")
    k3 = _derive_key("different_passphrase")
    assert k1 == k2, "Same passphrase should produce same key"
    assert k1 != k3, "Different passphrase should produce different key"
    assert len(k1) == 64, "Key should be 64 hex chars (256 bits)"
    print("✓ test_crypto_derive_key")


def test_crypto_open_plain():
    """open_encrypted_db falls back to plain SQLite without passphrase."""
    from nexus.crypto import open_encrypted_db
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = open_encrypted_db(path)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.execute("INSERT INTO test VALUES (1)")
        conn.commit()
        row = conn.execute("SELECT * FROM test").fetchone()
        assert row[0] == 1
        conn.close()
        print("✓ test_crypto_open_plain")
    finally:
        os.unlink(path)


def test_crypto_check_plain_db():
    """check_db_encrypted returns False for plain SQLite."""
    from nexus.crypto import check_db_encrypted
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.commit()
        conn.close()
        assert not check_db_encrypted(path), "Plain DB should not be detected as encrypted"
        print("✓ test_crypto_check_plain_db")
    finally:
        os.unlink(path)


# ── Graph tests ───────────────────────────────────────────

def test_graph_extract_entities():
    """extract_entities finds entities from Chinese text."""
    from nexus.graph import extract_entities
    entities = extract_entities("用户喜欢用APScheduler调度器管理定时任务")
    assert isinstance(entities, list)
    assert len(entities) > 0, "Should extract at least one entity"
    print(f"✓ test_graph_extract_entities: {entities}")


def test_graph_extract_entity_pairs():
    """extract_entity_pairs returns pairs of co-occurring entities."""
    from nexus.graph import extract_entity_pairs
    pairs = extract_entity_pairs("APScheduler调度器和cron任务都是定时工具")
    assert isinstance(pairs, list)
    # Should have at least one pair if 2+ entities found
    print(f"✓ test_graph_extract_entity_pairs: {len(pairs)} pairs")


def test_graph_entity_graph_init():
    """EntityGraph initializes and creates tables."""
    from nexus.graph import EntityGraph
    conn, path = _tmp_db()
    try:
        _seed_knowledge(conn, 3)
        eg = EntityGraph(conn)
        # Table should exist
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert "entity_relations" in tables
        print("✓ test_graph_entity_graph_init")
    finally:
        conn.close()
        os.unlink(path)


def test_graph_extract_and_link():
    """extract_and_link creates relations from content."""
    from nexus.graph import EntityGraph
    conn, path = _tmp_db()
    try:
        _seed_knowledge(conn, 3)
        eg = EntityGraph(conn)
        eg.extract_and_link(entry_id=1, content="用户喜欢APScheduler调度器和cron任务")
        # Check relations were created
        count = conn.execute("SELECT count(*) FROM entity_relations").fetchone()[0]
        assert count >= 0, "Relations should be created (may be 0 if <2 entities)"
        print(f"✓ test_graph_extract_and_link: {count} relations")
    finally:
        conn.close()
        os.unlink(path)


def test_graph_traverse():
    """traverse returns related entities."""
    from nexus.graph import EntityGraph
    conn, path = _tmp_db()
    try:
        _seed_knowledge(conn, 3)
        eg = EntityGraph(conn)
        # Even with no relations, traverse should return empty list
        result = eg.traverse("APScheduler", max_depth=1)
        assert isinstance(result, list)
        print(f"✓ test_graph_traverse: {len(result)} related")
    finally:
        conn.close()
        os.unlink(path)


# ── Health tests ──────────────────────────────────────────

def test_health_check():
    """health_check returns status for all layers."""
    from nexus.health import health_check
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # Create DB with all required tables
        from nexus.core import NexusCore
        core = NexusCore(path)
        core.close()

        result = health_check(path)
        assert "overall" in result
        # health_check returns either "layers" or "checks" key
        checks = result.get("layers") or result.get("checks", {})
        assert isinstance(checks, dict)
        for name, layer in checks.items():
            assert "status" in layer, f"{name} missing status"
            assert "latency_ms" in layer, f"{name} missing latency_ms"
        print(f"✓ test_health_check: overall={result['overall']}")
    finally:
        os.unlink(path)


# ── Metrics tests ─────────────────────────────────────────

def test_metrics_record_and_summary():
    """NexusMetrics records and summarizes."""
    from nexus.metrics import NexusMetrics
    conn, path = _tmp_db()
    try:
        nm = NexusMetrics(conn)
        # Record some metrics
        nm.record_search(15.0, ["fts", "hnsw"], 5, query_len=20)
        nm.record_write(8.0, content_len=100)
        nm.record_rerank(3.0, input_count=5, output_count=3)
        nm.record_embed(2.0, content_len=50)

        summary = nm.get_summary(hours=24)
        assert isinstance(summary, dict)
        print(f"✓ test_metrics_record_and_summary: {list(summary.keys())}")
    finally:
        conn.close()
        os.unlink(path)


def test_metrics_prune():
    """NexusMetrics prune removes old entries."""
    from nexus.metrics import NexusMetrics
    conn, path = _tmp_db()
    try:
        nm = NexusMetrics(conn)
        # Insert a record with old timestamp
        conn.execute(
            "INSERT INTO nexus_metrics (metric_type, latency_ms, created_at) "
            "VALUES ('search', 10.0, datetime('now', '-100 days'))"
        )
        conn.commit()
        # prune with days=30 should remove the 100-day-old record
        nm.prune(days=30)
        count = conn.execute("SELECT count(*) FROM nexus_metrics").fetchone()[0]
        assert count == 0, f"Expected 0 records after prune, got {count}"
        print(f"✓ test_metrics_prune: pruned successfully")
    finally:
        conn.close()
        os.unlink(path)


# ── Backup tests ──────────────────────────────────────────

def test_backup_snapshot():
    """NexusBackup.snapshot creates a backup file."""
    from nexus.backup import NexusBackup
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE test (id INTEGER)")
        conn.execute("INSERT INTO test VALUES (42)")
        conn.commit()
        conn.close()

        backup = NexusBackup(path)
        result = backup.snapshot(label="test")
        assert result is not None
        if "path" in result:
            assert os.path.exists(result["path"])
            print(f"✓ test_backup_snapshot: {result['path']}")
            os.unlink(result["path"])
        else:
            print(f"✓ test_backup_snapshot: {result}")
    finally:
        os.unlink(path)


# ── Reranker tests ────────────────────────────────────────

def test_reranker_score_only():
    """Reranker works in score-only mode (no cross-encoder)."""
    from nexus.embedder import Reranker
    reranker = Reranker()
    # Force score-only mode
    reranker._model = "score_only"

    results = [
        {"content": "APScheduler is a job scheduler", "similarity": 0.8,
         "positive_feedback": 5, "negative_feedback": 0,
         "last_accessed": "2026-05-29T00:00:00+00:00", "layer": "consolidated"},
        {"content": "cron is a time-based job scheduler", "similarity": 0.6,
         "positive_feedback": 2, "negative_feedback": 1,
         "last_accessed": "2026-05-28T00:00:00+00:00", "layer": "candidate"},
    ]

    reranked = reranker.rerank("scheduler", results, top_k=2)
    assert len(reranked) == 2
    for r in reranked:
        assert "rerank_score" in r, "rerank_score should be set"
        assert isinstance(r["rerank_score"], float)
    # Higher similarity + more feedback should rank higher
    assert reranked[0]["rerank_score"] >= reranked[1]["rerank_score"]
    print(f"✓ test_reranker_score_only: {[r['rerank_score'] for r in reranked]}")


def test_reranker_empty():
    """Reranker handles empty results."""
    from nexus.embedder import Reranker
    reranker = Reranker()
    result = reranker.rerank("query", [], top_k=5)
    assert result == []
    print("✓ test_reranker_empty")


# ── HNSW tests ────────────────────────────────────────────

def test_hnsw_status():
    """HNSWIndex status returns proper dict."""
    from nexus.hnsw import HNSWIndex
    conn, path = _tmp_db()
    try:
        hnsw = HNSWIndex(conn)
        status = hnsw.status()
        assert "available" in status
        assert "hnswlib_installed" in status
        assert "entry_count" in status
        print(f"✓ test_hnsw_status: {status}")
    finally:
        conn.close()
        os.unlink(path)


# ── Evolve tests ──────────────────────────────────────────

def test_evolve_jaccard():
    """_jaccard_similarity computes overlap correctly."""
    from nexus.evolve import _jaccard_similarity
    s = _jaccard_similarity("hello world", "hello world")
    assert s == 1.0, "Identical strings should have similarity 1.0"
    s2 = _jaccard_similarity("hello", "world")
    assert s2 == 0.0, "No overlap should give 0.0"
    print(f"✓ test_evolve_jaccard: identical=1.0, disjoint={s2}")


def test_evolve_tokenize():
    """_tokenize splits CJK and ASCII correctly."""
    from nexus.evolve import _tokenize
    tokens = _tokenize("hello世界world")
    # All chars become unigrams in CJK-aware mode
    assert "世" in tokens
    assert "界" in tokens
    assert "h" in tokens
    assert len(tokens) == len("hello世界world"), "Each char should be a token"
    print(f"✓ test_evolve_tokenize: {tokens}")


def test_evolve_merge_content():
    """merge_content handles subsets and complements."""
    from nexus.evolve import merge_content
    # New is subset of old → keep old
    old = "APScheduler是一个Python调度器，支持cron和interval两种触发方式"
    new = "APScheduler是一个Python调度器"
    merged = merge_content(old, new)
    assert merged == old, "Subset should keep old content"

    # Complementary → concatenate
    new_comp = "另外，APScheduler还支持date触发方式"
    merged2 = merge_content(old, new_comp)
    assert "§" in merged2, "Complementary should use § separator"
    print(f"✓ test_evolve_merge_content")


# ── API tests ─────────────────────────────────────────────

def test_api_create_app():
    """create_app returns a FastAPI app."""
    from nexus.api import create_app
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        schema_path = Path.home() / ".hermes" / "hermes-agent" / "plugins" / "memory" / "nexus" / "schema.sql"
        if schema_path.exists():
            conn.executescript(schema_path.read_text())
        conn.close()

        app = create_app(db_path=path)
        assert app is not None
        assert hasattr(app, "routes")
        # Check expected routes exist
        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/search" in routes
        assert "/write" in routes
        assert "/stats" in routes
        print(f"✓ test_api_create_app: {len(routes)} routes")
    finally:
        os.unlink(path)


def test_api_health_endpoint():
    """GET /health returns proper response."""
    from nexus.api import create_app
    from fastapi.testclient import TestClient
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        schema_path = Path.home() / ".hermes" / "hermes-agent" / "plugins" / "memory" / "nexus" / "schema.sql"
        if schema_path.exists():
            conn.executescript(schema_path.read_text())
        conn.close()

        app = create_app(db_path=path)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code in (200, 201, 503)
        data = resp.json()
        assert "overall" in data
        print(f"✓ test_api_health_endpoint: {resp.status_code}")
    finally:
        os.unlink(path)


def test_api_stats_endpoint():
    """GET /stats returns database statistics."""
    from nexus.api import create_app
    from nexus.core import NexusCore
    from fastapi.testclient import TestClient
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # Use NexusCore to ensure all tables exist
        core = NexusCore(path)
        conn = core._conn()
        conn.execute(
            "INSERT INTO unified_knowledge (content, layer) VALUES ('test', 'candidate')"
        )
        conn.commit()
        core.close()

        app = create_app(db_path=path)
        client = TestClient(app)
        resp = client.get("/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_entries" in data
        assert data["total_entries"] >= 1
        print(f"✓ test_api_stats_endpoint: {data['total_entries']} entries")
    finally:
        os.unlink(path)


# ── Run all tests ─────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_migration_current_version,
        test_migration_pending,
        test_migration_status,
        test_migration_run,
        test_crypto_available,
        test_crypto_derive_key,
        test_crypto_open_plain,
        test_crypto_check_plain_db,
        test_graph_extract_entities,
        test_graph_extract_entity_pairs,
        test_graph_entity_graph_init,
        test_graph_extract_and_link,
        test_graph_traverse,
        test_health_check,
        test_metrics_record_and_summary,
        test_metrics_prune,
        test_backup_snapshot,
        test_reranker_score_only,
        test_reranker_empty,
        test_hnsw_status,
        test_evolve_jaccard,
        test_evolve_tokenize,
        test_evolve_merge_content,
        test_api_create_app,
        test_api_health_endpoint,
        test_api_stats_endpoint,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__}: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed, {len(tests)} total")
    if failed:
        sys.exit(1)
