"""Plan B 2단계 — PostgreSQL+pgvector 스키마.

`rag/schema.py`(SQLite, Plan A)를 그대로 미러링한 구조 — 테이블·컬럼 이름은 동일하게
유지하고 방언만 Postgres로 바꿨다.

`chunk_embedding`은 Stage 2에선 고정 차원 없는 `vector` 컬럼 하나였으나(HNSW는 고정 차원이
필요해 인덱싱을 미뤄뒀음), Stage 4에서 `vector_1536`(Google)/`vector_1024`(Local) 두 컬럼으로
분리했다 — provider가 2개뿐이라 테이블을 나누기보다 컬럼을 나누는 쪽이 diff가 작다. 각 행은
자기 provider에 해당하는 컬럼만 채우고 나머지는 NULL, HNSW는 그 컬럼에 대한 partial index로 건다.

`document_chunk.text_tsv`는 Stage 6에서 추가(`rag/gap.py`의 FTS5 후보 검색 채널을 포팅하는 데
필요). SQLite FTS5는 별도 가상 테이블을 만들고 청크가 바뀔 때마다 수동으로 재생성해야 했지만,
Postgres의 `GENERATED ALWAYS AS ... STORED` 컬럼은 INSERT/UPDATE마다 DB가 알아서 갱신해줘서
그런 재구축 로직이 필요 없다.
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
    text_hash TEXT NOT NULL,
    text_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED
);
CREATE INDEX IF NOT EXISTS idx_document_chunk_source ON document_chunk(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_document_chunk_tsv ON document_chunk USING gin(text_tsv);

CREATE TABLE IF NOT EXISTS chunk_embedding (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chunk_id INTEGER NOT NULL REFERENCES document_chunk(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector_1536 vector(1536),
    vector_1024 vector(1024),
    input_hash TEXT NOT NULL,
    UNIQUE (chunk_id, provider, model, dimensions)
);
CREATE INDEX IF NOT EXISTS idx_chunk_embedding_hnsw_1536 ON chunk_embedding
    USING hnsw (vector_1536 vector_cosine_ops) WHERE vector_1536 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chunk_embedding_hnsw_1024 ON chunk_embedding
    USING hnsw (vector_1024 vector_cosine_ops) WHERE vector_1024 IS NOT NULL;
"""
