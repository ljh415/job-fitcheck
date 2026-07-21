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
    for (chunk_id, _, text_hash), vector in zip(rows, vectors):
        conn.execute(
            "INSERT INTO chunk_embedding (chunk_id, provider, model, dimensions, vector, input_hash)"
            " VALUES (?,?,?,?,?,?)",
            (chunk_id, provider.provider_name, provider.model, provider.dimensions, _vector_to_blob(vector), text_hash),
        )
    conn.commit()
    return len(rows)
