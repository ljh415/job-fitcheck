"""Plan A SQLite 최소 스키마 (`01_lean_evidence_first_rag.md`의 "최소 데이터 모델" 참고).

requirement_level(필수/우대 구분)은 2단계 종료 조건에 필요하지 않아 이번 스키마에서 뺐다 —
skipped: 필수/우대 파싱, 검색·집계에서 실제로 그 구분이 필요해지는 단계(5단계 이후)에 추가.

document_chunk/chunk_embedding은 3단계(2026-07-22, Codex 결정 반영)에서 채운다.
청킹 규칙: 빈 줄 기준 문단 분리 후 순서대로 묶어 청크당 최대 1,200자, 초기 overlap 없음.
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS posting (
    id INTEGER PRIMARY KEY,
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
    id INTEGER PRIMARY KEY,
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
    id INTEGER PRIMARY KEY,
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
    id INTEGER PRIMARY KEY,
    chunk_id INTEGER NOT NULL REFERENCES document_chunk(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector BLOB NOT NULL,
    input_hash TEXT NOT NULL,
    UNIQUE (chunk_id, provider, model, dimensions)
);
"""
