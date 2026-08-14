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
    """document_chunk_fts(독립형 FTS5 가상 테이블)가 없으면 만든다. 외부 콘텐츠
    (content='document_chunk') 방식은 MATCH가 항상 빈 결과를 반환하는 문제가 있어서(원인 미확인,
    count(*)/LIKE는 되는데 MATCH만 안 됨) 독립형 테이블로 우회한다.

    검색 경로(읽기)에서 호출하므로 여기서는 "없으면 생성"만 한다 — 한때 매 호출마다 무조건
    재생성했더니 동시 요청에서 SQLite가 `database is locked`를 던지는 문제가 있었다(Codex
    재리뷰로 발견, 2026-07-23, 재현 스크립트로 확인). 청크가 실제로 바뀌었을 때 테이블을
    최신화하는 책임은 `rebuild_fts5()`로 옮기고, 그건 청크를 실제로 바꾸는 `chunks.py`의
    `populate_posting_chunks()`/`populate_candidate_profile_chunks()`에서만 호출한다."""
    (exists,) = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE name = 'document_chunk_fts'"
    ).fetchone()
    if not exists:
        rebuild_fts5(conn)


def rebuild_fts5(conn: sqlite3.Connection) -> None:
    """document_chunk_fts를 document_chunk의 현재 내용으로 무조건 다시 만든다. 청크를 실제로
    추가·삭제·수정하는 코드(`chunks.py`)에서만 호출해야 한다 — 검색 경로에서 매번 부르면 동시
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
    흔한 자유 입력도 그대로 넘기면 SQLite가 문법 에러를 던진다(Codex 리뷰로 발견, 2026-07-23).
    큰따옴표로 감싸면 그 안은 순수 문자열로 취급되고, 내부의 큰따옴표는 `""`로 이스케이프하면
    리터럴 문자 그 자체로 인식된다(FTS5 공식 문법)."""
    return '"' + text.replace('"', '""') + '"'


def search_fts5(conn: sqlite3.Connection, keyword: str, top_k: int = 60, source_type: str = "posting_raw") -> list[int]:
    """키워드 하나로 FTS5 검색해 posting_id 순위 리스트를 반환한다. `ensure_fts5()`를 먼저
    호출해서 테이블이 있는지 확인해야 한다.

    source_type으로 필터링하는 이유: 이 테이블엔 공고(posting_raw)뿐 아니라 후보자 프로필
    (candidate_profile) 청크도 같이 들어있다. 필터 없이 LIMIT부터 걸면 프로필 청크가 상위권을
    차지해 진짜 찾아야 할 공고 후보가 LIMIT 예산 밖으로 밀려날 수 있다(Codex 재리뷰로 발견,
    2026-07-23 — 실측: 흔한 기술 키워드가 프로필 청크 11개 중 5~6개와도 매치됨)."""
    rows = conn.execute(
        "SELECT bm25(document_chunk_fts), rowid FROM document_chunk_fts"
        " WHERE document_chunk_fts MATCH ? AND source_type = ?"
        " ORDER BY bm25(document_chunk_fts) LIMIT ?",
        (fts5_literal(keyword), source_type, top_k),
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
