"""Plan B 6단계 — Postgres 전문검색(`document_chunk.text_tsv`, `rag/retrieval.py`의 FTS5 대응).

SQLite FTS5는 별도 가상 테이블을 만들고 청크 변경 시 수동으로 `rebuild_fts5()`를 불러야 했지만,
`text_tsv`는 generated column이라 DB가 INSERT/UPDATE마다 알아서 갱신한다 — 그래서 이 파일엔
검색 함수 하나만 있다(rebuild/ensure 함수 없음).

`websearch_to_tsquery()`는 자유 텍스트를 안전하게 tsquery로 변환하도록 설계돼 있어(사용자가
검색창에 입력할 법한 문자열을 그대로 받는 용도), SQLite판의 `fts5_literal()` 같은 별도 이스케이프
헬퍼가 필요 없다.
"""
import psycopg


def search_fts(
    conn: psycopg.Connection,
    keyword: str,
    top_k: int = 60,
    source_type: str = "posting_raw",
) -> list[int]:
    """키워드로 전문검색해 chunk_id 순위 리스트를 반환한다(관련도 내림차순)."""
    rows = conn.execute(
        "SELECT dc.id FROM document_chunk dc"
        " WHERE dc.text_tsv @@ websearch_to_tsquery('simple', %s) AND dc.source_type = %s"
        " ORDER BY ts_rank_cd(dc.text_tsv, websearch_to_tsquery('simple', %s)) DESC"
        " LIMIT %s",
        (keyword, source_type, keyword, top_k),
    ).fetchall()
    return [r[0] for r in rows]
