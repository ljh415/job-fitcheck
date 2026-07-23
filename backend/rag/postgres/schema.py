"""Plan B 2단계 — PostgreSQL+pgvector 스키마.

`rag/schema.py`(SQLite, Plan A)를 그대로 미러링한 구조 — 테이블·컬럼 이름은 동일하게
유지하고 방언만 Postgres로 바꿨다. `chunk_embedding.vector`는 고정 차원 없는 `vector`
타입으로 둔다(Google 1536차원/Local 1024차원이 같은 테이블에 섞여 들어가서) — HNSW 등
인덱싱은 고정 차원이 필요해 Stage 4에서 provider별 컬럼/테이블 분리를 다시 결정한다.
"""

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS posting (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    company_name TEXT,
    job_title TEXT,
    industry TEXT,
    experience_required TEXT,
    collected_at TEXT,
    raw_path TEXT NOT NULL,
    raw_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posting_skill (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    posting_id INTEGER NOT NULL REFERENCES posting(id),
    skill TEXT NOT NULL,
    matched_pattern TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posting_skill_skill ON posting_skill(skill);
CREATE INDEX IF NOT EXISTS idx_posting_skill_posting ON posting_skill(posting_id);

CREATE TABLE IF NOT EXISTS skill_alias (
    canonical TEXT NOT NULL,
    pattern TEXT NOT NULL,
    PRIMARY KEY (canonical, pattern)
);

CREATE TABLE IF NOT EXISTS candidate_evidence (
    skill TEXT PRIMARY KEY,
    evidence_level TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS document_chunk (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    section TEXT,
    text TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_document_chunk_source ON document_chunk(source_type, source_id);

CREATE TABLE IF NOT EXISTS chunk_embedding (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chunk_id INTEGER NOT NULL REFERENCES document_chunk(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector vector NOT NULL,
    input_hash TEXT NOT NULL,
    UNIQUE (chunk_id, provider, model, dimensions)
);
"""
