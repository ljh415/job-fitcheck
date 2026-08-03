"""임베딩 provider 구현체 하나를 받아 아직 그 provider로 임베딩 안 된 청크를 채우는
공통 파이프라인, PostgreSQL판. `rag/embed/pipeline.py`의 포팅 — pgvector는 파이썬
list[float]을 그대로 저장/조회하므로 BLOB pack/unpack이 필요 없다(`register_vector()`가
`rag/postgres/db.py`에서 등록됨).

`chunk_embedding`은 차원별로 컬럼이 나뉜다(`vector_1536`/`vector_1024`, HNSW 인덱싱을 위해
고정 차원이 필요해서, Stage 4) — 이 provider가 어느 컬럼에 써야 하는지 `_VECTOR_COLUMN`으로
결정한다.
"""
import psycopg

from rag.embed.base import EmbeddingProvider

_VECTOR_COLUMN = {1536: "vector_1536", 1024: "vector_1024"}


def run_embedding_pipeline(conn: psycopg.Connection, provider: EmbeddingProvider, source_type: str = "posting_raw") -> int:
    column = _VECTOR_COLUMN.get(provider.dimensions)
    if column is None:
        raise ValueError(
            f"{provider.provider_name}({provider.dimensions}차원)용 컬럼이 없음 — "
            f"schema.py에 vector_{provider.dimensions} 컬럼과 HNSW 인덱스를 추가해야 함"
        )

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
            f"INSERT INTO chunk_embedding (chunk_id, provider, model, dimensions, {column}, input_hash)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (chunk_id, provider.provider_name, provider.model, provider.dimensions, vector, text_hash),
        )
    # commit은 여기서 하지 않는다 — 유일한 호출부인 reindex.py의 _run_with_conn()이 공고
    # 임베딩과 (옵션인) 프로필 임베딩을 전부 성공한 뒤 한 번만 commit한다. 여기서 조기
    # commit하면 프로필 단계가 나중에 실패했을 때 이미 확정된 공고 임베딩까지 롤백해야
    # 하는 상황을 못 만든다 — provider 전환 중 이전 provider 색인이 훼손되는 문제가
    # 부분적으로만 해결된 채 남아있었다(Codex 4차 리뷰로 발견, 2026-08-03 — 3차 리뷰 때
    # chunks.py의 조기 commit만 지우고 이 함수의 commit은 놓쳤었다).
    return len(rows)
