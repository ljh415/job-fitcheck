"""임베딩 provider 구현체 하나를 받아 아직 그 provider로 임베딩 안 된 청크를 채우는
공통 파이프라인, PostgreSQL판. `rag/embed/pipeline.py`의 포팅 — pgvector는 파이썬
list[float]을 그대로 저장/조회하므로 BLOB pack/unpack이 필요 없다(`register_vector()`가
`rag/postgres/db.py`에서 등록됨).
"""
import psycopg

from rag.embed.base import EmbeddingProvider


def run_embedding_pipeline(conn: psycopg.Connection, provider: EmbeddingProvider, source_type: str = "posting_raw") -> int:
    rows = conn.execute(
        "SELECT dc.id, dc.text, dc.text_hash FROM document_chunk dc"
        " WHERE dc.source_type = %s"
        " AND NOT EXISTS ("
        "   SELECT 1 FROM chunk_embedding ce"
        "   WHERE ce.chunk_id = dc.id AND ce.provider = %s AND ce.model = %s AND ce.dimensions = %s"
        " )",
        (source_type, provider.provider_name, provider.model, provider.dimensions),
    ).fetchall()
    if not rows:
        return 0

    vectors = provider.embed_documents([text for _, text, _ in rows])
    if len(vectors) != len(rows):
        raise RuntimeError(
            f"{provider.provider_name}가 청크 {len(rows)}개를 요청받고 벡터 {len(vectors)}개만 반환함"
        )
    for i, vector in enumerate(vectors):
        if len(vector) != provider.dimensions:
            raise RuntimeError(
                f"{provider.provider_name} 벡터 차원 불일치: 기대 {provider.dimensions}, 실제 {len(vector)}"
            )
    for (chunk_id, _, text_hash), vector in zip(rows, vectors):
        conn.execute(
            "INSERT INTO chunk_embedding (chunk_id, provider, model, dimensions, vector, input_hash)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (chunk_id, provider.provider_name, provider.model, provider.dimensions, vector, text_hash),
        )
    conn.commit()
    return len(rows)
