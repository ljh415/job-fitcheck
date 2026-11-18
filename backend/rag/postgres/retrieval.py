"""청크 단위 벡터 검색, PostgreSQL+pgvector판. `rag/retrieval.py`(SQLite)의 포팅.

SQLite판은 벡터를 BLOB으로 저장해두고 파이썬에서 직접 코사인 유사도를 계산했지만,
pgvector는 `<=>`(코사인 거리) 연산자로 정렬·상위 top_k 추출까지 SQL 안에서 끝낼 수
있어 `_vector_from_blob`/`_cosine` 같은 헬퍼가 필요 없다.

`chunk_embedding`은 차원별 컬럼(`vector_1536`/`vector_1024`)으로 나뉘어 있다(HNSW
인덱싱을 위해 고정 차원이 필요해서) — `_VECTOR_COLUMN`으로 provider에 맞는 컬럼을 고른다.
"""
import psycopg

from rag.embed.base import EmbeddingProvider
from rag.postgres.pipeline import _VECTOR_COLUMN


def search_chunks(
    conn: psycopg.Connection,
    provider: EmbeddingProvider,
    query: str,
    source_type: str | None = None,
    top_k: int | None = 10,
) -> list[tuple[float, int, str]]:
    """질의를 provider로 임베딩해 청크 단위 코사인 유사도 상위 top_k를 반환한다.
    (score, chunk_id, chunk_text) 리스트, 점수 내림차순.

    `top_k=None`이면 LIMIT 없이 전체를 반환한다 — 이 corpus는 청크가 수백 개 수준이라
    "전체 순위"가 필요한 호출부(예: judge_topic_postings의 method="local" 비교 경로)에서
    임의의 상한(과거 60)으로 진짜 정답을 놓치는 것보다 전체를 도는 게 낫다."""
    column = _VECTOR_COLUMN[provider.dimensions]
    qvec = provider.embed_query(query)
    source_filter = " AND dc.source_type = %s" if source_type else ""
    params = (qvec, provider.provider_name, provider.model, provider.dimensions)
    if source_type:
        params += (source_type,)
    params += (qvec,)
    limit_clause = ""
    if top_k is not None:
        limit_clause = " LIMIT %s"
        params += (top_k,)
    rows = conn.execute(
        f"SELECT 1 - (ce.{column} <=> %s::vector) AS score, ce.chunk_id, dc.text"
        " FROM chunk_embedding ce JOIN document_chunk dc ON dc.id = ce.chunk_id"
        f" WHERE ce.provider = %s AND ce.model = %s AND ce.dimensions = %s{source_filter}"
        f" ORDER BY ce.{column} <=> %s::vector{limit_clause}",
        params,
    ).fetchall()
    return rows
