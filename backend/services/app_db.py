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
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO fit_history (company_slug, created_at, profile_version_id, "
                "fit_score, fit_label, content) VALUES (?, ?, ?, ?, ?, ?)",
                ("테스트회사__직무", "2026-08-16T00:00:00", version_id, 72, "추천", "리포트 원문"),
            )
            conn.commit()
            hist = conn.execute(
                "SELECT * FROM fit_history WHERE company_slug = ?", ("테스트회사__직무",)
            ).fetchone()
            assert hist["fit_score"] == 72 and hist["profile_version_id"] == version_id

        # 초기화 재호출(idempotent) 확인
        init_db()

    print("app_db self-check 통과")
