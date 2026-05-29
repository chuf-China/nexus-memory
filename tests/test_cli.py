#!/usr/bin/env python3
"""test_nexus_cli.py — NexusCLI单元测试"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import nexus.cli as cli
from nexus.core import NexusCore


def _setup_db():
    f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    f.close()
    nc = NexusCore(f.name)
    nc.write("CLI测试知识", user_id="cli_test")
    nc.close()
    return f.name


def test_health():
    path = _setup_db()
    try:
        with patch.object(cli, 'DB_PATH', Path(path)):
            h = cli.health()
        assert h["db_exists"] is True
        assert h["tables_exist"] is True
        assert h["fts_index_exists"] is True
    finally:
        os.unlink(path)
        for s in ["-wal", "-shm"]:
            try: os.unlink(path + s)
            except: pass


def test_list_knowledge():
    path = _setup_db()
    try:
        with patch.object(cli, 'DB_PATH', Path(path)):
            items = cli.list_knowledge(limit=10)
        assert len(items) >= 1
        assert any("CLI测试知识" in i.get("content", "") for i in items)
    finally:
        os.unlink(path)
        for s in ["-wal", "-shm"]:
            try: os.unlink(path + s)
            except: pass


def test_stats_cmd():
    path = _setup_db()
    try:
        with patch.object(cli, 'DB_PATH', Path(path)):
            s = cli.stats()
        assert s["total"] >= 1
    finally:
        os.unlink(path)
        for s in ["-wal", "-shm"]:
            try: os.unlink(path + s)
            except: pass


def test_export_import():
    path = _setup_db()
    try:
        out = path + ".export.json"
        with patch.object(cli, 'DB_PATH', Path(path)):
            cli.export_knowledge(output_path=out)
        assert os.path.exists(out)
        with open(out) as f:
            data = json.load(f)
        assert len(data) >= 1

        path2 = path + ".import.db"
        nc2 = NexusCore(path2)
        nc2.close()
        with patch.object(cli, 'DB_PATH', Path(path2)):
            count = cli.import_knowledge(input_path=out)
        assert count >= 1
        os.unlink(out)
        os.unlink(path2)
    finally:
        os.unlink(path)
        for s in ["-wal", "-shm"]:
            try: os.unlink(path + s)
            except: pass


def test_edit_knowledge():
    path = _setup_db()
    try:
        nc = NexusCore(path)
        rows = nc._conn().execute(
            "SELECT id FROM unified_knowledge LIMIT 1"
        ).fetchall()
        kid = rows[0]["id"]
        nc.close()
        with patch.object(cli, 'DB_PATH', Path(path)):
            cli.edit_knowledge(kid, "更新后的内容")
        nc2 = NexusCore(path)
        row = nc2._conn().execute(
            "SELECT content FROM unified_knowledge WHERE id=?", (kid,)
        ).fetchone()
        nc2.close()
        assert row["content"] == "更新后的内容"
    finally:
        os.unlink(path)
        for s in ["-wal", "-shm"]:
            try: os.unlink(path + s)
            except: pass


def test_delete_knowledge():
    path = _setup_db()
    try:
        nc = NexusCore(path)
        rows = nc._conn().execute(
            "SELECT id FROM unified_knowledge LIMIT 1"
        ).fetchall()
        kid = rows[0]["id"]
        nc.close()
        with patch.object(cli, 'DB_PATH', Path(path)):
            cli.delete_knowledge(kid)
        nc2 = NexusCore(path)
        row = nc2._conn().execute(
            "SELECT status FROM unified_knowledge WHERE id=?", (kid,)
        ).fetchone()
        nc2.close()
        assert row["status"] == "archived"
    finally:
        os.unlink(path)
        for s in ["-wal", "-shm"]:
            try: os.unlink(path + s)
            except: pass


if __name__ == "__main__":
    import traceback
    tests = [
        test_health, test_list_knowledge, test_stats_cmd,
        test_export_import, test_edit_knowledge, test_delete_knowledge,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n结果: {passed}/{passed+failed} 通过")
    sys.exit(0 if failed == 0 else 1)
