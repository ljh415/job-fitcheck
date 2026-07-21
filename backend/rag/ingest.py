"""Plan A 2단계 — 공고 데이터 정규화.

`data/companies/*.raw.txt`(사실 원본)와 대응하는 `.md` frontmatter(구조화 추출값)를 읽어
`data/rag.db`(SQLite)에 최소 스키마로 적재한다.

실행: backend/ 에서 `python3 -m rag.ingest`
"""
import hashlib
import re
import sqlite3

import frontmatter

from config import settings
from rag.schema import SCHEMA_SQL
from rag.skills import CANDIDATE_EVIDENCE, TRACKED_SKILLS

DB_PATH = settings.data_dir / "rag.db"


def _raw_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_SQL)
    return conn


def ingest_postings(conn: sqlite3.Connection) -> int:
    """`.raw.txt`를 원본으로 순회하며 posting + posting_skill을 채운다.
    재실행 가능하도록 매번 전체 삭제 후 다시 적재한다(증분 색인은 Plan B 이후 범위)."""
    conn.execute("DELETE FROM posting_skill")
    conn.execute("DELETE FROM posting")
    count = 0
    for raw_path in sorted(settings.companies_dir.glob("*.raw.txt")):
        slug = raw_path.name.removesuffix(".raw.txt")
        md_path = settings.companies_dir / f"{slug}.md"
        raw_text = raw_path.read_text(encoding="utf-8")
        fm: dict = {}
        if md_path.exists():
            post = frontmatter.load(str(md_path))
            fm = post.metadata

        conn.execute(
            "INSERT INTO posting (slug, company_name, job_title, industry, experience_required, collected_at, raw_path, raw_hash)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                slug,
                fm.get("company_name", ""),
                fm.get("job_title", ""),
                fm.get("industry", ""),
                fm.get("experience_required", ""),
                fm.get("created_at", ""),
                str(raw_path),
                _raw_hash(raw_text),
            ),
        )
        posting_id = conn.execute("SELECT id FROM posting WHERE slug = ?", (slug,)).fetchone()[0]

        for skill, patterns in TRACKED_SKILLS.items():
            for pattern in patterns:
                if re.search(pattern, raw_text, re.IGNORECASE):
                    conn.execute(
                        "INSERT INTO posting_skill (posting_id, skill, matched_pattern) VALUES (?,?,?)",
                        (posting_id, skill, pattern),
                    )
                    break  # 이 공고에서 skill 하나는 패턴 중 하나만 매칭되면 충분 (중복 행 방지)
        count += 1
    conn.commit()
    return count


def ingest_skill_alias(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM skill_alias")
    for skill, patterns in TRACKED_SKILLS.items():
        for pattern in patterns:
            conn.execute("INSERT OR IGNORE INTO skill_alias (canonical, pattern) VALUES (?,?)", (skill, pattern))
    conn.commit()


def ingest_candidate_evidence(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM candidate_evidence")
    for skill, info in CANDIDATE_EVIDENCE.items():
        conn.execute(
            "INSERT INTO candidate_evidence (skill, evidence_level, note) VALUES (?,?,?)",
            (skill, info["level"], info["note"]),
        )
    conn.commit()


def skill_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT skill, COUNT(DISTINCT posting_id) FROM posting_skill GROUP BY skill"
    ).fetchall()
    return dict(rows)


def run() -> sqlite3.Connection:
    conn = build_db()
    n = ingest_postings(conn)
    ingest_skill_alias(conn)
    ingest_candidate_evidence(conn)
    print(f"적재 완료: 공고 {n}건, DB={DB_PATH}")
    return conn


if __name__ == "__main__":
    conn = run()
    print("기술별 집계:", skill_counts(conn))
