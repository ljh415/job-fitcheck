"""프로필 스냅샷/회사별 적합도 평가 이력을 저장하는 SQLite 연결.

단일 파일(data/app.db)로 관리한다. RAG의 Postgres(opt-in)와는 무관 — 프로필 히스토리는
RAG를 안 쓰는 사용자도 써야 하는 핵심 기능이라 선택 기능의 DB에 의존하지 않는다.
세부 설계는 docs/planning/profile_history_plan.md 참고.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from config import settings

DB_PATH = settings.data_dir / "app.db"


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """앱 시작 시 호출 — 테이블이 없으면 생성한다."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS profile_versions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                content    TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fit_history (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                company_slug       TEXT NOT NULL,
                created_at         TEXT NOT NULL,
                profile_version_id INTEGER,
                fit_score          INTEGER,
                fit_label          TEXT,
                content            TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_fit_history_slug
            ON fit_history(company_slug, created_at)
        """)
        conn.commit()


def create_profile_version(content: str) -> int:
    """프로필 스냅샷을 저장하고 새로 생성된 id를 반환한다."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO profile_versions (created_at, content) VALUES (?, ?)",
            (datetime.now().isoformat(timespec="seconds"), content),
        )
        conn.commit()
        return cur.lastrowid


def list_profile_versions() -> list[dict]:
    """최신순 (id, created_at, content) 목록. content에서 summary 등을 뽑는 건 호출부 몫."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, created_at, content FROM profile_versions ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_profile_version(version_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, created_at, content FROM profile_versions WHERE id = ?",
            (version_id,),
        ).fetchone()
        return dict(row) if row else None


def delete_profile_version(version_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM profile_versions WHERE id = ?", (version_id,))
        conn.commit()
        return cur.rowcount > 0


def latest_profile_version_id() -> int | None:
    """가장 최근 프로필 스냅샷 id. 평가 이력을 남길 때 "현재 프로필 버전"으로 참조한다."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM profile_versions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["id"] if row else None


def create_fit_history_entry(
    company_slug: str,
    profile_version_id: int | None,
    fit_score: int | None,
    fit_label: str | None,
    content: str,
) -> int:
    """평가 결과를 이력에 추가한다(덮어쓰기 아님) — 최초 평가도 포함해서 매번 호출."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO fit_history (company_slug, created_at, profile_version_id, "
            "fit_score, fit_label, content) VALUES (?, ?, ?, ?, ?, ?)",
            (
                company_slug,
                datetime.now().isoformat(timespec="seconds"),
                profile_version_id,
                fit_score,
                fit_label,
                content,
            ),
        )
        conn.commit()
        return cur.lastrowid


def list_fit_history(company_slug: str) -> list[dict]:
    """표시용 — content(무거운 원문)는 제외, 프로필 버전의 존재 여부까지 같이 반환."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT fh.id, fh.created_at, fh.fit_score, fh.fit_label,
                   fh.profile_version_id, pv.created_at AS profile_version_created_at
            FROM fit_history fh
            LEFT JOIN profile_versions pv ON pv.id = fh.profile_version_id
            WHERE fh.company_slug = ?
            ORDER BY fh.id DESC
            """,
            (company_slug,),
        ).fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = __import__("pathlib").Path(tmp)
        DB_PATH = settings.data_dir / "app.db"

        init_db()
        assert os.path.exists(DB_PATH)

        version_id = create_profile_version("테스트 프로필 내용")
        version_id2 = create_profile_version("두번째 프로필 내용")
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM profile_versions WHERE id = ?", (version_id,)
            ).fetchone()
            assert row["content"] == "테스트 프로필 내용"

        versions = list_profile_versions()
        assert [v["id"] for v in versions] == [version_id2, version_id]  # 최신순

        fetched = get_profile_version(version_id)
        assert fetched["content"] == "테스트 프로필 내용"
        assert get_profile_version(999999) is None

        assert delete_profile_version(version_id) is True
        assert get_profile_version(version_id) is None
        assert delete_profile_version(version_id) is False  # 이미 삭제됨

        # fit_history는 FK를 강제하지 않으므로, 삭제된 스냅샷을 참조해도 insert는 성공해야
        # 한다("삭제됨" 표시는 조회 시점에 판단, 위 profile_history_plan.md 참고)
        assert latest_profile_version_id() == version_id2
        create_fit_history_entry("테스트회사__직무", version_id, 72, "추천", "리포트 원문(삭제된 버전 참조)")
        create_fit_history_entry("테스트회사__직무", version_id2, 62, "조건부추천", "리포트 원문(정상 참조)")

        hist_list = list_fit_history("테스트회사__직무")
        assert len(hist_list) == 2
        assert hist_list[0]["fit_score"] == 62  # 최신순(나중에 넣은 것)
        assert hist_list[0]["profile_version_created_at"] is not None  # 정상 참조
        assert hist_list[1]["fit_score"] == 72
        assert hist_list[1]["profile_version_created_at"] is None  # 삭제된 버전 참조 → "삭제됨" 판단용

        # 초기화 재호출(idempotent) 확인
        init_db()

    print("app_db self-check 통과")
