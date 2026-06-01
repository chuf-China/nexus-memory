#!/usr/bin/env python3
"""test_all_modules.py — 综合测试所有 Nexus 模块"""

import json
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
        
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            drive = NexusDrive(db_path)
            assert drive is not None
            drive.close()
        finally:
            os.unlink(db_path)
    
    def test_drive_create_tables(self):
        from src.nexus_drive import NexusDrive
        
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            drive = NexusDrive(db_path)
            # Should have knowledge table
            cursor = drive.conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            assert "knowledge" in tables
            drive.close()
        finally:
            os.unlink(db_path)


# ============ nexus_belief.py ============

class TestNexusBelief:
    """测试 nexus_belief 模块"""
    
    def test_belief_init(self):
        from src.nexus_belief import BeliefNetwork
        
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            network = BeliefNetwork(conn)
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
        from src.nexus_evolve import EvolutionEngine
        
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            engine = EvolutionEngine(conn)
            assert engine is not None
            conn.close()
        finally:
            os.unlink(db_path)


# ============ nexus_extract.py ============

class TestNexusExtract:
    """测试 nexus_extract 模块"""
    
    def test_extract_init(self):
        from src.nexus_extract import KnowledgeExtractor
        
        extractor = KnowledgeExtractor()
        assert extractor is not None


# ============ nexus_search.py ============

class TestNexusSearch:
    """测试 nexus_search 模块"""
    
    def test_search_init(self):
        from src.nexus_search import EnhancedSearch
        
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            search = EnhancedSearch(conn)
            assert search is not None
            conn.close()
        finally:
            os.unlink(db_path)


# ============ nexus_graph.py ============

class TestNexusGraph:
    """测试 nexus_graph 模块"""
    
    def test_graph_init(self):
        from src.nexus_graph import KnowledgeGraph
        
        graph = KnowledgeGraph()
        assert graph is not None


# ============ nexus_miner.py ============

class TestNexusMiner:
    """测试 nexus_miner 模块"""
    
    def test_miner_init(self):
        from src.nexus_miner import KnowledgeMiner
        
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            miner = KnowledgeMiner(conn)
            assert miner is not None
            conn.close()
        finally:
            os.unlink(db_path)


# ============ nexus_local.py ============

class TestNexusLocal:
    """测试 nexus_local 模块"""
    
    def test_local_init(self):
        from src.nexus_local import LocalStorage
        
        storage = LocalStorage()
        assert storage is not None


# ============ nexus_embedder.py ============

class TestNexusEmbedder:
    """测试 nexus_embedder 模块"""
    
    def test_embedder_init(self):
        from src.nexus_embedder import Embedder
        
        embedder = Embedder()
        assert embedder is not None


# ============ nexus_hnsw.py ============

class TestNexusHNSW:
    """测试 nexus_hnsw 模块"""
    
    def test_hnsw_init(self):
        from src.nexus_hnsw import HNSWIndex
        
        index = HNSWIndex()
        assert index is not None


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
            result = nexus.write("Test knowledge", source="test", confidence=0.9)
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
            nexus.write("User prefers Python", source="test")
            
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
