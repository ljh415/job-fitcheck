"""청크 단위 벡터 검색 공통 유틸 — evaluate.py(5단계, posting 단위로 집계)와
gap.py(6단계, 청크 텍스트 자체가 필요)가 같이 쓴다.
"""
import sqlite3
import struct

from rag.embed.base import EmbeddingProvider


def _vector_from_blob(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def search_chunks(
    conn: sqlite3.Connection,
    provider: EmbeddingProvider,
    query: str,
    source_type: str | None = None,
    top_k: int = 10,
) -> list[tuple[float, int, str]]:
    """질의를 provider로 임베딩해 청크 단위 코사인 유사도 상위 top_k를 반환한다.
    (score, chunk_id, chunk_text) 리스트, 점수 내림차순. source_type을 주면 그 종류만 검색
    (예: 'candidate_profile'만 검색해서 공고 문장이 섞여 나오는 걸 막음)."""
    qvec = provider.embed_query(query)
    if source_type:
        rows = conn.execute(
            "SELECT ce.vector, ce.chunk_id, dc.text FROM chunk_embedding ce"
            " JOIN document_chunk dc ON dc.id = ce.chunk_id"
            " WHERE ce.provider = ? AND ce.model = ? AND ce.dimensions = ? AND dc.source_type = ?",
            (provider.provider_name, provider.model, provider.dimensions, source_type),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT ce.vector, ce.chunk_id, dc.text FROM chunk_embedding ce"
            " JOIN document_chunk dc ON dc.id = ce.chunk_id"
            " WHERE ce.provider = ? AND ce.model = ? AND ce.dimensions = ?",
            (provider.provider_name, provider.model, provider.dimensions),
        ).fetchall()
    scored = [(_cosine(qvec, _vector_from_blob(blob)), chunk_id, text) for blob, chunk_id, text in rows]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_k]


def ensure_fts5(conn: sqlite3.Connection) -> None:
    """document_chunk_fts(독립형 FTS5 가상 테이블)를 준비한다. 없으면 만들고 채운다.
    외부 콘텐츠(content='document_chunk') 방식은 MATCH가 항상 빈 결과를 반환하는 문제가 있어서
    (원인 미확인, count(*)/LIKE는 되는데 MATCH만 안 됨) 독립형 테이블로 우회한다."""
    (exists,) = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE name = 'document_chunk_fts'"
    ).fetchone()
    if not exists:
        conn.execute("CREATE VIRTUAL TABLE document_chunk_fts USING fts5(text, tokenize='unicode61')")
        conn.execute("INSERT INTO document_chunk_fts(rowid, text) SELECT id, text FROM document_chunk")
        conn.commit()


def search_fts5(conn: sqlite3.Connection, keyword: str, top_k: int = 60) -> list[int]:
    """키워드 하나로 FTS5 검색해 posting_id 순위 리스트를 반환한다. `ensure_fts5()`를 먼저
    호출해서 테이블이 있는지 확인해야 한다."""
    rows = conn.execute(
        "SELECT bm25(document_chunk_fts), rowid FROM document_chunk_fts WHERE document_chunk_fts MATCH ?"
        " ORDER BY bm25(document_chunk_fts) LIMIT ?",
        (keyword, top_k),
    ).fetchall()
    # bm25()는 낮을수록 관련도가 높음 — score를 음수로 뒤집어 기존 "높을수록 좋음" 정렬과 맞춘다.
    scored = [(-score, chunk_id) for score, chunk_id in rows]
    return ranked_postings_by_score(conn, scored)


def ranked_postings_by_score(conn: sqlite3.Connection, scored_chunks: list[tuple[float, int]]) -> list[int]:
    """(score, chunk_id) 목록을 점수 내림차순으로 받아, posting_id 중복을 제거하며 순위 리스트를 만든다."""
    scored_chunks = sorted(scored_chunks, key=lambda x: x[0], reverse=True)
    seen: set[int] = set()
    ranked: list[int] = []
    for _, chunk_id in scored_chunks:
        row = conn.execute(
            "SELECT po.id FROM document_chunk dc JOIN posting po ON po.slug = dc.source_id WHERE dc.id = ?",
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
