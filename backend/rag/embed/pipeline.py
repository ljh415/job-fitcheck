"""임베딩 provider 구현체 하나를 받아 아직 그 provider로 임베딩 안 된 청크를 채우는
공통 파이프라인. provider별 API 호출·배치·재시도는 각 구현체(`google.py` 등) 안에 있고,
여기서는 "어떤 청크가 아직 없는지 찾기 → embed_documents() 호출 → 저장"만 담당한다.
"""
import struct
import sqlite3

from rag.embed.base import EmbeddingProvider


def _vector_to_blob(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def run_embedding_pipeline(conn: sqlite3.Connection, provider: EmbeddingProvider, source_type: str = "posting_raw") -> int:
    rows = conn.execute(
        "SELECT dc.id, dc.text, dc.text_hash FROM document_chunk dc"
        " WHERE dc.source_type = ?"
        " AND NOT EXISTS ("
        "   SELECT 1 FROM chunk_embedding ce"
        "   WHERE ce.chunk_id = dc.id AND ce.provider = ? AND ce.model = ? AND ce.dimensions = ?"
        " )",
        (source_type, provider.provider_name, provider.model, provider.dimensions),
    ).fetchall()
    if not rows:
        return 0

    vectors = provider.embed_documents([text for _, text, _ in rows])
    # zip()은 vectors가 rows보다 짧아도 조용히 짧은 쪽에 맞춰 자르고, 그 상태로 아래
    # return len(rows)가 "전부 성공"이라고 거짓 보고하게 된다(Codex 리뷰로 발견, 2026-07-23) —
    # provider가 응답 개수·차원을 어겼으면 여기서 명확히 실패시킨다.
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
            " VALUES (?,?,?,?,?,?)",
            (chunk_id, provider.provider_name, provider.model, provider.dimensions, _vector_to_blob(vector), text_hash),
        )
    conn.commit()
    return len(rows)
