"""Plan A SQLite 최소 스키마 (`01_lean_evidence_first_rag.md`의 "최소 데이터 모델" 참고).

requirement_level(필수/우대 구분)은 2단계 종료 조건에 필요하지 않아 이번 스키마에서 뺐다 —
skipped: 필수/우대 파싱, 검색·집계에서 실제로 그 구분이 필요해지는 단계(5단계 이후)에 추가.
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

-- 3~5단계(임베딩·검색)에서 채울 예정. 청킹 규칙이 아직 안 정해져서 2단계에서는 스키마만 만든다.
CREATE TABLE IF NOT EXISTS document_chunk (
    id INTEGER PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    section TEXT,
    text TEXT NOT NULL
);
"""
