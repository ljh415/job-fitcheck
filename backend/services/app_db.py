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


if __name__ == "__main__":
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = __import__("pathlib").Path(tmp)
        DB_PATH = settings.data_dir / "app.db"

        init_db()
        assert os.path.exists(DB_PATH)

        version_id = create_profile_version("테스트 프로필 내용")
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM profile_versions WHERE id = ?", (version_id,)
            ).fetchone()
            assert row["content"] == "테스트 프로필 내용"

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
