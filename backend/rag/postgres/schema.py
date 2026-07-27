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
    raw_hash TEXT NOT NULL,
    -- 공고 비교(대화형 근거 기반 RAG Phase 1)용 구조화 필드. data/companies/{slug}.md의
    -- frontmatter(CompanyFrontmatter)에 이미 있는 값을 ingest_postings()가 그대로 복제해온다.
    tech_stack TEXT[],
    benefits TEXT[],
    stability TEXT,
    employee_count TEXT,
    investment_stage TEXT,
    jobplanet_score REAL,
    fit_score INTEGER,
    strengths TEXT[],
    gaps TEXT[]
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
    UNIQUE (chunk_id, provider, model, dimensions),
    -- dimensions와 실제 채워진 컬럼이 어긋나는 행(둘 다 NULL, 둘 다 채워짐, 차원 불일치)을
    -- 막는다 — 지금 파이프라인은 항상 올바르게 쓰지만 복구·수동 적재 실수를 막는 안전장치
    -- (Codex 리뷰로 발견, 2026-07-23).
    CONSTRAINT chunk_embedding_dimension_check CHECK (
        (dimensions = 1536 AND vector_1536 IS NOT NULL AND vector_1024 IS NULL) OR
        (dimensions = 1024 AND vector_1024 IS NOT NULL AND vector_1536 IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_chunk_embedding_hnsw_1536 ON chunk_embedding
    USING hnsw (vector_1536 vector_cosine_ops) WHERE vector_1536 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chunk_embedding_hnsw_1024 ON chunk_embedding
    USING hnsw (vector_1024 vector_cosine_ops) WHERE vector_1024 IS NOT NULL;
"""

# `CREATE TABLE IF NOT EXISTS`는 기존 테이블에 새 컬럼을 추가해주지 않는다 — Stage 2→4→6
# 마다 chunk_embedding/document_chunk 구조가 바뀔 때 매번 수동으로 테이블을 드롭해야 했다
# (Codex 리뷰로 지적, 2026-07-23). 원본(.raw.txt 등)에서 언제든 재색인 가능한 파생 저장소이므로
# migration 대신 이 함수로 전부 지우고 SCHEMA_SQL을 다시 실행하는 쪽이 더 단순하다.
DROP_ALL_SQL = """
DROP TABLE IF EXISTS chunk_embedding;
DROP TABLE IF EXISTS document_chunk;
DROP TABLE IF EXISTS candidate_evidence;
DROP TABLE IF EXISTS skill_alias;
DROP TABLE IF EXISTS posting_skill;
DROP TABLE IF EXISTS posting;
"""


def rebuild_schema(conn) -> None:
    """RAG 관련 테이블을 전부 지우고 SCHEMA_SQL로 새로 만든다 — `--rebuild-schema`에서만 호출."""
    conn.execute(DROP_ALL_SQL)
    conn.execute(SCHEMA_SQL)
    conn.commit()
