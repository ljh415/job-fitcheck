"""청크 단위 벡터 검색 공통 유틸 — gap.py(청크 텍스트 자체가 필요)가 쓴다.
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
    """document_chunk_fts(독립형 FTS5 가상 테이블)가 없으면 만든다. 외부 콘텐츠
    (content='document_chunk') 방식은 MATCH가 항상 빈 결과를 반환하는 문제가 있어서(원인 미확인,
    count(*)/LIKE는 되는데 MATCH만 안 됨) 독립형 테이블로 우회한다.

    검색 경로(읽기)에서 호출하므로 여기서는 "없으면 생성"만 한다 — 매 호출마다 무조건
    재생성하면 동시 요청에서 SQLite가 `database is locked`를 던진다. 청크가 바뀐 뒤 이미 만들어진
    테이블을 최신화하려면 `rebuild_fts5()`를 직접 호출해야 한다."""
    (exists,) = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE name = 'document_chunk_fts'"
    ).fetchone()
    if not exists:
        rebuild_fts5(conn)


def rebuild_fts5(conn: sqlite3.Connection) -> None:
    """document_chunk_fts를 document_chunk의 현재 내용으로 무조건 다시 만든다. 청크를
    바꾼 코드가 직접 호출해야 한다 — 검색 경로에서 매번 부르면 동시
    요청 간 락이 걸린다(위 `ensure_fts5()` 설명 참고)."""
    conn.execute("DROP TABLE IF EXISTS document_chunk_fts")
    conn.execute(
        "CREATE VIRTUAL TABLE document_chunk_fts USING fts5(text, source_type UNINDEXED, tokenize='unicode61')"
    )
    conn.execute(
        "INSERT INTO document_chunk_fts(rowid, text, source_type) SELECT id, text, source_type FROM document_chunk"
    )
    conn.commit()


def fts5_literal(text: str) -> str:
    """자유 텍스트를 FTS5 MATCH 구문에 안전하게 넘길 수 있는 순수 문자열 리터럴로 감싼다.
    FTS5는 `"`, `:`, `*`, `AND`/`OR`/`NOT` 등을 쿼리 연산자로 해석하므로, "C++"·"foo:bar"처럼
    흔한 자유 입력도 그대로 넘기면 SQLite가 문법 에러를 던진다.
    큰따옴표로 감싸면 그 안은 순수 문자열로 취급되고, 내부의 큰따옴표는 `""`로 이스케이프하면
    리터럴 문자 그 자체로 인식된다(FTS5 공식 문법)."""
    return '"' + text.replace('"', '""') + '"'
