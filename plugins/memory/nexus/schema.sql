-- Nexus — Unified Knowledge Store
-- Schema v0.1 (plugin prototype)
-- Date: 2026-05-23

-- ============================================================
-- 核心表：所有知识统一存储
-- ============================================================
CREATE TABLE IF NOT EXISTS unified_knowledge (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL,
    domain_scores   TEXT NOT NULL DEFAULT '{}',
    layer           TEXT NOT NULL DEFAULT 'instant'
                    CHECK(layer IN ('instant','candidate','consolidated')),
    positive_feedback   INTEGER DEFAULT 0,
    negative_feedback   INTEGER DEFAULT 0,
    match_hash          TEXT,
    first_seen          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_query_domain   TEXT,
    active_summary      TEXT,
    total_corrections   INTEGER DEFAULT 0,
    last_corrected_at   TIMESTAMP,
    sleep_time_processed INTEGER DEFAULT 0,
    consolidated_from    TEXT,
    source_session_id   TEXT,
    source_snippet      TEXT,
    replaced_by         INTEGER REFERENCES unified_knowledge(id),
    replaces            INTEGER REFERENCES unified_knowledge(id),
    status          TEXT DEFAULT 'active'
                    CHECK(status IN ('active','superseded','archived')),
    user_id         TEXT DEFAULT 'default',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_uk_layer_status ON unified_knowledge(layer, status);
CREATE INDEX IF NOT EXISTS idx_uk_match_hash ON unified_knowledge(match_hash);
CREATE INDEX IF NOT EXISTS idx_uk_last_accessed ON unified_knowledge(last_accessed);
CREATE INDEX IF NOT EXISTS idx_uk_created_at ON unified_knowledge(created_at);
CREATE INDEX IF NOT EXISTS idx_uk_source_session ON unified_knowledge(source_session_id);
CREATE INDEX IF NOT EXISTS idx_uk_domain_identity ON unified_knowledge(
    json_extract(domain_scores, '$.identity')
);
CREATE INDEX IF NOT EXISTS idx_uk_domain_workflow ON unified_knowledge(
    json_extract(domain_scores, '$.workflow')
);
CREATE INDEX IF NOT EXISTS idx_uk_domain_strategy ON unified_knowledge(
    json_extract(domain_scores, '$.strategy')
);
CREATE INDEX IF NOT EXISTS idx_uk_user_status ON unified_knowledge(user_id, status);

-- FTS5 全文索引
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    content,
    content='unified_knowledge', content_rowid='id'
);

-- FTS 更新触发器（仅内容变更时触发，INSERT由代码层处理）
-- DELETE: 清理 FTS 索引
CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON unified_knowledge BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, content) VALUES ('delete', old.id, '');
END;
CREATE TRIGGER IF NOT EXISTS knowledge_au AFTER UPDATE ON unified_knowledge
    WHEN old.content != new.content
BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, content) VALUES ('delete', old.id, '');
    INSERT INTO knowledge_fts(rowid, content) VALUES (new.id, '');
END;

-- ============================================================
-- 版本全量表
-- ============================================================
CREATE TABLE IF NOT EXISTS knowledge_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_id    INTEGER REFERENCES unified_knowledge(id),
    content         TEXT NOT NULL,
    active_summary  TEXT,
    status          TEXT,
    change_reason   TEXT,
    source_session_id TEXT,
    source_snippet  TEXT,
    user_id         TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_kv_knowledge_id ON knowledge_versions(knowledge_id);
CREATE INDEX IF NOT EXISTS idx_kv_created_at ON knowledge_versions(created_at);

-- ============================================================
-- 反馈日志表
-- ============================================================
CREATE TABLE IF NOT EXISTS feedback_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_id    INTEGER REFERENCES unified_knowledge(id),
    feedback_type   TEXT CHECK(feedback_type IN (
                        'explicit_positive','explicit_negative',
                        'correction','system_conflict'
                    )),
    source          TEXT,
    session_id       TEXT,
    user_id         TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fl_knowledge_id ON feedback_log(knowledge_id);
CREATE INDEX IF NOT EXISTS idx_fl_type_created ON feedback_log(feedback_type, created_at);

-- ============================================================
-- 系统元数据
-- ============================================================
CREATE TABLE IF NOT EXISTS nexus_meta (
    key             TEXT PRIMARY KEY,
    value           TEXT
);
