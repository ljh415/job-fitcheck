"""Plan B 2단계 — 공고/프로필 청킹, PostgreSQL판.

`rag/chunks.py`(SQLite, Plan A)의 포팅. FTS5에 해당하는 전문검색 재구축 호출은 이번
범위(저장까지)에서 뺐다 — Postgres tsvector 기반 전문검색은 Plan B 4단계(하이브리드 검색)에서
다시 설계한다.
"""
import hashlib
import tempfile
from pathlib import Path

import frontmatter
import psycopg

from config import settings
from rag.chunking import chunk_text

PROFILE_SOURCE_ID = "profile"  # 후보자 프로필은 파일 하나뿐이라 고정 id 사용


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def populate_candidate_profile_chunks(conn: psycopg.Connection) -> tuple[bool, int]:
    """document_chunk에 후보자 프로필(source_type='candidate_profile') 청크를 채운다.
    반환값: (내용이 바뀌어 다시 만들었는지, 전체 청크 수)."""
    # frontmatter(education/skills 등 구조화 메타데이터)는 서술형 근거가 아니라 그대로 청킹하면
    # "발췌문"에 필드 나열만 노출되는 노이즈가 되므로, body만 청킹 대상으로 삼는다.
    body_text = frontmatter.load(str(settings.candidate_profile_path)).content
    new_chunks = chunk_text(body_text)
    new_hashes = [_text_hash(c.text) for c in new_chunks]

    existing_hashes = [
        row[0]
        for row in conn.execute(
            "SELECT text_hash FROM document_chunk"
            " WHERE source_type = 'candidate_profile' AND source_id = %s"
            " ORDER BY chunk_index",
            (PROFILE_SOURCE_ID,),
        ).fetchall()
    ]

    if existing_hashes == new_hashes:
        return False, len(new_chunks)

    conn.execute(
        "DELETE FROM chunk_embedding WHERE chunk_id IN"
        " (SELECT id FROM document_chunk WHERE source_type = 'candidate_profile' AND source_id = %s)",
        (PROFILE_SOURCE_ID,),
    )
    conn.execute(
        "DELETE FROM document_chunk WHERE source_type = 'candidate_profile' AND source_id = %s",
        (PROFILE_SOURCE_ID,),
    )
    for chunk in new_chunks:
        conn.execute(
            "INSERT INTO document_chunk"
            " (source_type, source_id, section, text, chunk_index, start_line, end_line, text_hash)"
            " VALUES ('candidate_profile', %s, %s, %s, %s, %s, %s, %s)",
            (
                PROFILE_SOURCE_ID, None, chunk.text, chunk.chunk_index,
                chunk.start_line, chunk.end_line, _text_hash(chunk.text),
            ),
        )
    # commit은 여기서 하지 않는다 — 호출부(reindex.py의 _run_with_conn())가 이 함수의
    # 삭제+재생성과 뒤이은 provider 임베딩을 하나의 트랜잭션으로 묶어서, 임베딩이 실패하면
    # 방금 지운 옛 임베딩까지 롤백으로 되돌린다(Codex 3차 리뷰 발견 3번, 2026-08-02 —
    # 예전엔 여기서 바로 commit해서 임베딩 실패 시 이전 provider 색인만 사라진 채 복구
    # 안 되는 창이 있었다).
    return True, len(new_chunks)


def populate_posting_chunks(conn: psycopg.Connection) -> tuple[int, int]:
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
                " WHERE source_type = 'posting_raw' AND source_id = %s"
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
            " (SELECT id FROM document_chunk WHERE source_type = 'posting_raw' AND source_id = %s)",
            (slug,),
        )
        conn.execute(
            "DELETE FROM document_chunk WHERE source_type = 'posting_raw' AND source_id = %s", (slug,)
        )
        for chunk in new_chunks:
            conn.execute(
                "INSERT INTO document_chunk"
                " (source_type, source_id, section, text, chunk_index, start_line, end_line, text_hash)"
                " VALUES ('posting_raw', %s, %s, %s, %s, %s, %s, %s)",
                (slug, None, chunk.text, chunk.chunk_index, chunk.start_line, chunk.end_line, _text_hash(chunk.text)),
            )
        total_chunks += len(new_chunks)

    # commit은 여기서 하지 않는다 — populate_candidate_profile_chunks()와 같은 이유
    # (호출부가 임베딩까지 성공한 뒤 한 번에 묶어서 commit/rollback한다).
    return touched_postings, total_chunks


def prune_deleted_postings(conn: psycopg.Connection) -> int:
    """공고 원문(.raw.txt)이 삭제돼 `posting`에서 없어진 slug의 document_chunk/chunk_embedding을
    지운다. `ingest_postings()`는 posting/posting_skill을 매번 전체 삭제 후 재적재하지만, 청크는
    이 posting을 더 이상 순회 대상에 넣지 않아 고아로 남는 문제(Plan B 5단계에서 발견)를 해결한다.
    `posting` 테이블이 최신 상태로 재적재된 뒤에 호출해야 한다."""
    rows = conn.execute(
        "SELECT DISTINCT source_id FROM document_chunk"
        " WHERE source_type = 'posting_raw' AND source_id NOT IN (SELECT slug FROM posting)"
    ).fetchall()
    stale_slugs = [r[0] for r in rows]
    if not stale_slugs:
        return 0

    conn.execute(
        "DELETE FROM chunk_embedding WHERE chunk_id IN"
        " (SELECT id FROM document_chunk WHERE source_type = 'posting_raw' AND source_id = ANY(%s))",
        (stale_slugs,),
    )
    conn.execute(
        "DELETE FROM document_chunk WHERE source_type = 'posting_raw' AND source_id = ANY(%s)",
        (stale_slugs,),
    )
    conn.commit()
    return len(stale_slugs)


if __name__ == "__main__":
    # DB 연결 없이, populate_candidate_profile_chunks()가 frontmatter를 실제로 빼고
    # body만 청킹 대상으로 넘기는지만 검증한다.
    sample = frontmatter.Post(
        content="실무 경험 문단입니다. " * 20,
        skills=["Python", "PyTorch"],
        experience_years=5,
        summary="요약 필드입니다",
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "profile.md"
        frontmatter.dump(sample, str(path))

        loaded = frontmatter.load(str(path))
        assert "skills:" not in loaded.content
        assert "experience_years:" not in loaded.content
        assert "summary:" not in loaded.content
        assert "실무 경험 문단입니다" in loaded.content

        chunks = chunk_text(loaded.content)
        joined = "\n".join(c.text for c in chunks)
        assert "skills:" not in joined
        assert "Python" not in joined  # frontmatter 필드값이라 body에 없어야 함
        assert "실무 경험 문단입니다" in joined

    print("OK: frontmatter가 청킹 대상에서 제외됨")
