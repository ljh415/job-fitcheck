"""공고 원문을 청킹해 `document_chunk`에 채운다. provider와 무관 — 어떤 임베딩
provider를 쓰든 한 번만 실행하면 된다(3~4단계에서 Google/로컬 모듈 양쪽이 공유).

공고별로 청크 해시 집합이 이전과 동일하면(=원문이 안 바뀌었으면) 건드리지 않는다 —
그래야 재실행할 때 안 바뀐 공고의 기존 임베딩까지 지워지고 재생성되는 낭비가 없다.
"""
import hashlib
import sqlite3

from config import settings
from rag.chunking import chunk_text


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


PROFILE_SOURCE_ID = "profile"  # 후보자 프로필은 파일 하나뿐이라 고정 id 사용


def populate_candidate_profile_chunks(conn: sqlite3.Connection) -> tuple[bool, int]:
    """document_chunk에 후보자 프로필(source_type='candidate_profile') 청크를 채운다.
    공고와 로직은 동일(해시 비교로 안 바뀌었으면 스킵)하되 대상이 파일 1개뿐이다.
    반환값: (내용이 바뀌어 다시 만들었는지, 전체 청크 수)."""
    raw_text = settings.candidate_profile_path.read_text(encoding="utf-8")
    new_chunks = chunk_text(raw_text)
    new_hashes = [_text_hash(c.text) for c in new_chunks]

    existing_hashes = [
        row[0]
        for row in conn.execute(
            "SELECT text_hash FROM document_chunk"
            " WHERE source_type = 'candidate_profile' AND source_id = ?"
            " ORDER BY chunk_index",
            (PROFILE_SOURCE_ID,),
        ).fetchall()
    ]

    if existing_hashes == new_hashes:
        return False, len(new_chunks)

    conn.execute(
        "DELETE FROM chunk_embedding WHERE chunk_id IN"
        " (SELECT id FROM document_chunk WHERE source_type = 'candidate_profile' AND source_id = ?)",
        (PROFILE_SOURCE_ID,),
    )
    conn.execute(
        "DELETE FROM document_chunk WHERE source_type = 'candidate_profile' AND source_id = ?",
        (PROFILE_SOURCE_ID,),
    )
    for chunk in new_chunks:
        conn.execute(
            "INSERT INTO document_chunk"
            " (source_type, source_id, section, text, chunk_index, start_line, end_line, text_hash)"
            " VALUES ('candidate_profile', ?, ?, ?, ?, ?, ?, ?)",
            (
                PROFILE_SOURCE_ID, None, chunk.text, chunk.chunk_index,
                chunk.start_line, chunk.end_line, _text_hash(chunk.text),
            ),
        )
    conn.commit()
    return True, len(new_chunks)


def populate_posting_chunks(conn: sqlite3.Connection) -> tuple[int, int]:
    """document_chunk에 공고 원문(source_type='posting_raw') 청크를 채운다.
    반환값: (내용이 바뀌어 다시 만든 공고 수, 전체 청크 수)."""
    touched_postings = 0
    total_chunks = 0

    for slug, raw_path in conn.execute("SELECT slug, raw_path FROM posting").fetchall():
        raw_text = open(raw_path, encoding="utf-8").read()
        new_chunks = chunk_text(raw_text)
        new_hashes = [_text_hash(c.text) for c in new_chunks]

        existing_hashes = [
            row[0]
            for row in conn.execute(
                "SELECT text_hash FROM document_chunk"
                " WHERE source_type = 'posting_raw' AND source_id = ?"
                " ORDER BY chunk_index",
                (slug,),
            ).fetchall()
        ]

        if existing_hashes == new_hashes:
            total_chunks += len(new_chunks)
            continue  # 원문이 안 바뀜 — 기존 청크·임베딩을 그대로 둔다

        touched_postings += 1
        conn.execute(
            "DELETE FROM chunk_embedding WHERE chunk_id IN"
            " (SELECT id FROM document_chunk WHERE source_type = 'posting_raw' AND source_id = ?)",
            (slug,),
        )
        conn.execute(
            "DELETE FROM document_chunk WHERE source_type = 'posting_raw' AND source_id = ?", (slug,)
        )
        for chunk in new_chunks:
            conn.execute(
                "INSERT INTO document_chunk"
                " (source_type, source_id, section, text, chunk_index, start_line, end_line, text_hash)"
                " VALUES ('posting_raw', ?, ?, ?, ?, ?, ?, ?)",
                (slug, None, chunk.text, chunk.chunk_index, chunk.start_line, chunk.end_line, _text_hash(chunk.text)),
            )
        total_chunks += len(new_chunks)

    conn.commit()
    return touched_postings, total_chunks
