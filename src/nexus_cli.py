#!/usr/bin/env python3
"""nexus_cli.py — Nexus Memory CLI

Usage:
  nexus-memory status          # Show DB stats
  nexus-memory search "query"  # Search knowledge
  nexus-memory export          # Export to JSON
  nexus-memory benchmark       # Run performance test
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import click
except ImportError:
    print("Error: click is required for CLI. Install with: pip install click")
    sys.exit(1)

# Default paths
DB_PATH = Path.home() / ".hermes" / "data" / "nexus.db"
BACKUP_DIR = Path.home() / ".hermes" / "data" / "backups"


def get_db_path(db_path: Optional[str] = None) -> Path:
    """Get database path from argument or default."""
    if db_path:
        return Path(db_path)
    return DB_PATH


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Get database connection."""
    if not db_path.exists():
        click.echo(f"Error: Database not found at {db_path}", err=True)
        sys.exit(1)
    return sqlite3.connect(str(db_path))


@click.group()
@click.option("--db", default=None, help="Database path (default: ~/.hermes/data/nexus.db)")
@click.pass_context
def cli(ctx: click.Context, db: Optional[str]):
    """Nexus Memory - Cross-session persistent memory for AI Agents."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = get_db_path(db)


@cli.command()
@click.pass_context
def status(ctx: click.Context):
    """Show database statistics."""
    db_path = ctx.obj["db_path"]

    if not db_path.exists():
        click.echo(f"Database: {db_path}")
        click.echo("Status: NOT FOUND")
        click.echo("\nCreate a database by writing some knowledge:")
        click.echo('  nexus-memory write "Your fact here"')
        return

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()

        # Count knowledge entries
        cursor.execute("SELECT COUNT(*) FROM unified_knowledge WHERE status = 'active'")
        total_count = cursor.fetchone()[0]

        # Count by source session
        cursor.execute(
            "SELECT COALESCE(source_session_id, 'unknown'), COUNT(*) "
            "FROM unified_knowledge GROUP BY source_session_id"
        )
        source_counts = dict(cursor.fetchall())

        # Count by layer
        cursor.execute("SELECT layer, COUNT(*) FROM unified_knowledge GROUP BY layer")
        domain_counts = dict(cursor.fetchall())

        # Database size
        db_size = db_path.stat().st_size
        db_size_mb = db_size / (1024 * 1024)

        # Last write time
        cursor.execute("SELECT MAX(created_at) FROM unified_knowledge")
        last_write = cursor.fetchone()[0]

        click.echo(f"Database: {db_path}")
        click.echo(f"Size: {db_size_mb:.2f} MB")
        click.echo(f"Total entries: {total_count}")
        click.echo(f"Last write: {last_write}")

        if source_counts:
            click.echo("\nBy session:")
            for source, count in sorted(source_counts.items(), key=lambda x: -x[1]):
                click.echo(f"  {source}: {count}")

        if domain_counts:
            click.echo("\nBy layer:")
            for domain, count in sorted(domain_counts.items(), key=lambda x: -x[1]):
                click.echo(f"  {domain}: {count}")

    finally:
        conn.close()


@cli.command()
@click.argument("query")
@click.option("--limit", "-l", default=5, help="Number of results")
@click.option("--domain", "-d", default=None, help="Filter by domain")
@click.pass_context
def search(ctx: click.Context, query: str, limit: int, domain: Optional[str]):
    """Search knowledge base."""
    db_path = ctx.obj["db_path"]

    # Import NexusCore
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src.nexus_core import NexusCore

    nexus = NexusCore(str(db_path))
    if domain:
        results = nexus.search_by_domain(domain=domain, limit=limit)
    else:
        results = nexus.search(query, limit=limit)

    if not results:
        click.echo("No results found.")
        return

    click.echo(f"Found {len(results)} results:\n")
    for i, result in enumerate(results, 1):
        content = result.get("content", "")
        layer = result.get("layer", "instant")
        user = result.get("user_id") or "unknown"

        click.echo(f"{i}. [{layer}] {content[:100]}...")
        click.echo(f"   User: {user}")
        click.echo()


@cli.command()
@click.option("--output", "-o", default="nexus_export.json", help="Output file")
@click.pass_context
def export(ctx: click.Context, output: str):
    """Export knowledge to JSON."""
    db_path = ctx.obj["db_path"]
    conn = get_connection(db_path)

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM unified_knowledge")
        columns = [description[0] for description in cursor.description]
        rows = cursor.fetchall()

        data = []
        for row in rows:
            entry = dict(zip(columns, row))
            data.append(entry)

        with open(output, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

        click.echo(f"Exported {len(data)} entries to {output}")

    finally:
        conn.close()


@cli.command()
@click.argument("content")
@click.option("--confidence", "-c", default=0.8, help="Initial confidence score (0-1)")
@click.pass_context
def write(ctx: click.Context, content: str, confidence: float):
    """Write knowledge to the database."""
    db_path = ctx.obj["db_path"]

    # Import NexusCore
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src.nexus_core import NexusCore

    nexus = NexusCore(str(db_path))
    nexus.write(content, source_session_id="cli", initial_confidence=confidence)

    click.echo(f"✓ Written (confidence={confidence}): {content[:50]}...")


@cli.command()
@click.option("--iterations", "-n", default=100, help="Number of iterations")
@click.pass_context
def benchmark(ctx: click.Context, iterations: int):
    """Run performance benchmark."""
    db_path = ctx.obj["db_path"]

    # Import NexusCore
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src.nexus_core import NexusCore

    click.echo(f"Running benchmark with {iterations} iterations...")

    nexus = NexusCore(str(db_path))

    # Write benchmark
    write_times = []
    for i in range(iterations):
        start = time.time()
        nexus.write(f"Benchmark entry {i}: This is test content for performance measurement.",
                   source_session_id="benchmark", initial_confidence=0.5)
        write_times.append(time.time() - start)

    # Search benchmark
    search_times = []
    for i in range(iterations):
        start = time.time()
        nexus.search("benchmark test", limit=5)
        search_times.append(time.time() - start)

    # Calculate statistics
    avg_write = sum(write_times) / len(write_times) * 1000
    avg_search = sum(search_times) / len(search_times) * 1000
    p95_write = sorted(write_times)[int(len(write_times) * 0.95)] * 1000
    p95_search = sorted(search_times)[int(len(search_times) * 0.95)] * 1000

    click.echo("\n=== Benchmark Results ===")
    click.echo(f"Write: avg={avg_write:.2f}ms, p95={p95_write:.2f}ms")
    click.echo(f"Search: avg={avg_search:.2f}ms, p95={p95_search:.2f}ms")
    click.echo(f"Total entries: {iterations}")


@cli.command()
@click.pass_context
def health(ctx: click.Context):
    """Run health checks."""
    db_path = ctx.obj["db_path"]

    checks = {}

    # 1. Database file exists
    checks["db_exists"] = db_path.exists()

    # 2. Database readable
    if checks["db_exists"]:
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("SELECT 1")
            conn.close()
            checks["db_readable"] = True
        except Exception:
            checks["db_readable"] = False
    else:
        checks["db_readable"] = False

    # 3. FTS5 available
    if checks["db_readable"]:
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("SELECT fts5()")
            conn.close()
            checks["fts5_available"] = True
        except Exception:
            checks["fts5_available"] = False
    else:
        checks["fts5_available"] = False

    # 4. Write permission
    try:
        test_file = db_path.parent / ".test_write"
        test_file.write_text("test")
        test_file.unlink()
        checks["write_permission"] = True
    except Exception:
        checks["write_permission"] = False

    # 5. Dependencies
    try:
        import numpy
        checks["numpy_installed"] = True
    except ImportError:
        checks["numpy_installed"] = False

    # Print results
    click.echo("=== Health Check ===")
    all_ok = True
    for check, status in checks.items():
        icon = "✓" if status else "✗"
        click.echo(f"  {icon} {check}: {status}")
        if not status:
            all_ok = False

    if all_ok:
        click.echo("\n✓ All checks passed!")
    else:
        click.echo("\n✗ Some checks failed.")


def main():
    """Entry point for the CLI."""
    cli(obj={})


if __name__ == "__main__":
    main()
