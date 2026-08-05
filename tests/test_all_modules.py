#!/usr/bin/env python3
"""test_all_modules.py — 综合测试所有 Nexus 模块"""

import os
import sys
import tempfile
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


# ============ nexus_utils.py ============

class TestNexusUtils:
    """测试 nexus_utils 模块"""
    
    def test_content_hash(self):
        from src.nexus_utils import content_hash
        h1 = content_hash("test content")
        h2 = content_hash("test content")
        h3 = content_hash("different content")
        
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 16
    
    def test_segment_fts(self):
        from src.nexus_utils import segment_fts
        
        # English
        result = segment_fts("hello world")
        assert "hello" in result
        assert "world" in result
        
        # Mixed
        result = segment_fts("Python programming")
        assert "Python" in result
    
    def test_empty_scores(self):
        from src.nexus_utils import empty_scores
        scores = empty_scores()
        
        assert isinstance(scores, dict)
        assert "identity" in scores
        assert all(v == 0 for v in scores.values())


# ============ nexus_drive.py ============

class TestNexusDrive:
    """测试 nexus_drive 模块"""
    
    def test_drive_init(self):
        from src.nexus_drive import NexusDrive

        drive = NexusDrive()
        assert drive is not None
        assert drive._event_log is not None


# ============ nexus_belief.py ============

class TestNexusBelief:
    """测试 nexus_belief 模块"""
    
    def test_belief_init(self):
        from src.nexus_belief import BeliefEngine
        
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            network = BeliefEngine(conn)
            assert network is not None
            conn.close()
        finally:
            os.unlink(db_path)


# ============ nexus_constitution.py ============

class TestNexusConstitution:
    """测试 nexus_constitution 模块"""
    
    def test_constitution_init(self):
        from src.nexus_constitution import Constitution
        
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            const = Constitution(conn)
            assert const is not None
            conn.close()
        finally:
            os.unlink(db_path)


# ============ nexus_evolve.py ============

class TestNexusEvolve:
    """测试 nexus_evolve 模块"""
    
    def test_evolve_init(self):
        from src.nexus_evolve import evolve_on_write
        
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            assert callable(evolve_on_write)
        finally:
            os.unlink(db_path)


# ============ nexus_extract.py ============

class TestNexusExtract:
    """测试 nexus_extract 模块"""
    
    def test_extract_init(self):
        from src.nexus_extract import extract_knowledge

        assert callable(extract_knowledge)


# ============ nexus_search.py ============

class TestNexusSearch:
    """测试 nexus_search 模块"""
    
    def test_search_init(self):
        from src.nexus_search import EnhancedSearch
        
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            import sqlite3
            from src.nexus_core import NexusCore
            conn = sqlite3.connect(db_path)
            nexus = NexusCore(db_path)
            search = EnhancedSearch(nexus)
            assert search is not None
            nexus.close()
            conn.close()
        finally:
            os.unlink(db_path)


# ============ nexus_graph.py ============

class TestNexusGraph:
    """测试 nexus_graph 模块"""
    
    def test_graph_init(self):
        from src.nexus_graph import EntityGraph

        assert EntityGraph is not None


# ============ nexus_miner.py ============

class TestNexusMiner:
    """测试 nexus_miner 模块"""
    
    def test_miner_init(self):
        from src.nexus_miner import NexusMiner
        
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            miner = NexusMiner()
            assert miner is not None
            conn.close()
        finally:
            os.unlink(db_path)


# ============ nexus_local.py ============

class TestNexusLocal:
    """测试 nexus_local 模块"""
    
    def test_local_init(self):
        from src.nexus_local import get_client

        assert callable(get_client)


# ============ nexus_embedder.py ============

class TestNexusEmbedder:
    """测试 nexus_embedder 模块"""
    
    def test_embedder_init(self):
        from src.nexus_embedder import EmbedderFactory

        assert EmbedderFactory is not None


# ============ nexus_hnsw.py ============

class TestNexusHNSW:
    """测试 nexus_hnsw 模块"""
    
    def test_hnsw_init(self):
        from src.nexus_hnsw import HNSWIndex

        import sqlite3
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        try:
            conn = sqlite3.connect(db_path)
            index = HNSWIndex(conn)
            assert index is not None
            conn.close()
        finally:
            os.unlink(db_path)


# ============ nexus_core.py ============

class TestNexusCore:
    """测试 nexus_core 模块"""
    
    def test_core_init(self):
        from src.nexus_core import NexusCore
        
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            nexus = NexusCore(db_path)
            assert nexus is not None
            nexus.close()
        finally:
            os.unlink(db_path)
    
    def test_core_write_search(self):
        from src.nexus_core import NexusCore
        
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            nexus = NexusCore(db_path)
            
            # Write
            result = nexus.write("Test knowledge")
            assert result["success"]
            
            # Search
            results = nexus.search("Test", limit=5)
            assert len(results) > 0
            
            nexus.close()
        finally:
            os.unlink(db_path)
    
    def test_core_system_prompt_block(self):
        from src.nexus_core import NexusCore
        
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            nexus = NexusCore(db_path)
            
            # Write some knowledge
            nexus.write("User prefers Python")
            
            # Get system prompt block
            block = nexus.system_prompt_block()
            assert isinstance(block, str)
            assert len(block) > 0
            
            nexus.close()
        finally:
            os.unlink(db_path)


# ============ Run tests ============

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
