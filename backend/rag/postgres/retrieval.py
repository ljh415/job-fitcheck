"""청크 단위 벡터 검색, PostgreSQL+pgvector판. `rag/retrieval.py`(SQLite)의 포팅.

SQLite판은 벡터를 BLOB으로 저장해두고 파이썬에서 직접 코사인 유사도를 계산했지만,
pgvector는 `<=>`(코사인 거리) 연산자로 정렬·상위 top_k 추출까지 SQL 안에서 끝낼 수
있어 `_vector_from_blob`/`_cosine` 같은 헬퍼가 필요 없다.
"""
import psycopg

from rag.embed.base import EmbeddingProvider


def search_chunks(
    conn: psycopg.Connection,
    provider: EmbeddingProvider,
    query: str,
    source_type: str | None = None,
    top_k: int = 10,
) -> list[tuple[float, int, str]]:
    """질의를 provider로 임베딩해 청크 단위 코사인 유사도 상위 top_k를 반환한다.
    (score, chunk_id, chunk_text) 리스트, 점수 내림차순."""
    qvec = provider.embed_query(query)
    if source_type:
        rows = conn.execute(
            "SELECT 1 - (ce.vector <=> %s::vector) AS score, ce.chunk_id, dc.text"
            " FROM chunk_embedding ce JOIN document_chunk dc ON dc.id = ce.chunk_id"
            " WHERE ce.provider = %s AND ce.model = %s AND ce.dimensions = %s AND dc.source_type = %s"
            " ORDER BY ce.vector <=> %s::vector LIMIT %s",
            (qvec, provider.provider_name, provider.model, provider.dimensions, source_type, qvec, top_k),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT 1 - (ce.vector <=> %s::vector) AS score, ce.chunk_id, dc.text"
            " FROM chunk_embedding ce JOIN document_chunk dc ON dc.id = ce.chunk_id"
            " WHERE ce.provider = %s AND ce.model = %s AND ce.dimensions = %s"
            " ORDER BY ce.vector <=> %s::vector LIMIT %s",
            (qvec, provider.provider_name, provider.model, provider.dimensions, qvec, top_k),
        ).fetchall()
    return rows


def ranked_postings_by_score(conn: psycopg.Connection, scored_chunks: list[tuple[float, int]]) -> list[int]:
    """(score, chunk_id) 목록을 점수 내림차순으로 받아, posting_id 중복을 제거하며 순위 리스트를 만든다."""
    scored_chunks = sorted(scored_chunks, key=lambda x: x[0], reverse=True)
    seen: set[int] = set()
    ranked: list[int] = []
    for _, chunk_id in scored_chunks:
        row = conn.execute(
            "SELECT po.id FROM document_chunk dc JOIN posting po ON po.slug = dc.source_id WHERE dc.id = %s",
            (chunk_id,),
        ).fetchone()
        if not row:
            continue
        posting_id = row[0]
        if posting_id in seen:
            continue
        seen.add(posting_id)
        ranked.append(posting_id)
    return ranked
