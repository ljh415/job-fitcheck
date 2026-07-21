"""공고 원문을 청킹해 `document_chunk`에 채운다. provider와 무관 — 어떤 임베딩
provider를 쓰든 한 번만 실행하면 된다(3~4단계에서 Google/로컬 모듈 양쪽이 공유).
"""
import hashlib
import sqlite3

from config import settings
from rag.chunking import chunk_text


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def populate_posting_chunks(conn: sqlite3.Connection) -> int:
    """document_chunk에 공고 원문(source_type='posting_raw') 청크를 채운다.
    재실행 가능하도록 기존 posting_raw 청크(및 연결된 임베딩)를 지우고 다시 만든다."""
    conn.execute(
        "DELETE FROM chunk_embedding WHERE chunk_id IN"
        " (SELECT id FROM document_chunk WHERE source_type = 'posting_raw')"
    )
    conn.execute("DELETE FROM document_chunk WHERE source_type = 'posting_raw'")

    total = 0
    for slug, raw_path in conn.execute("SELECT slug, raw_path FROM posting").fetchall():
        raw_text = open(raw_path, encoding="utf-8").read()
        for chunk in chunk_text(raw_text):
            conn.execute(
                "INSERT INTO document_chunk"
                " (source_type, source_id, section, text, chunk_index, start_line, end_line, text_hash)"
                " VALUES ('posting_raw', ?, ?, ?, ?, ?, ?, ?)",
                (slug, None, chunk.text, chunk.chunk_index, chunk.start_line, chunk.end_line, _text_hash(chunk.text)),
            )
            total += 1
    conn.commit()
    return total
