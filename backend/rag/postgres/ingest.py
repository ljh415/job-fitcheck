"""Plan B 2단계 — 공고 데이터 정규화, PostgreSQL판.

`rag/ingest.py`(SQLite, Plan A)의 포팅 — 로직은 동일하고 SQL 방언만 바꿨다(`?`→`%s`,
`INSERT OR IGNORE`→`ON CONFLICT DO NOTHING`). `rag/ingest.py`는 gap.py/answer.py가 계속
쓰므로 건드리지 않는다.

실행: backend/ 에서 `python3 -m rag.postgres.ingest`
"""
import hashlib
import re

import frontmatter
import psycopg

from config import settings
from rag.postgres.db import get_connection
from rag.postgres.schema import SCHEMA_SQL
from rag.skills import CANDIDATE_EVIDENCE, TRACKED_SKILLS


def _raw_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_db() -> psycopg.Connection:
    conn = get_connection()
    conn.execute(SCHEMA_SQL)
    conn.commit()
    return conn


def ingest_postings(conn: psycopg.Connection) -> int:
    """`.raw.txt`를 원본으로 순회하며 posting + posting_skill을 채운다.
    재실행 가능하도록 매번 전체 삭제 후 다시 적재한다(증분 색인은 Plan B 5단계 범위)."""
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

        row = conn.execute(
            "INSERT INTO posting (slug, company_name, job_title, industry, experience_required, collected_at, raw_path, raw_hash)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
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
        ).fetchone()
        posting_id = row[0]

        for skill, patterns in TRACKED_SKILLS.items():
            for pattern in patterns:
                if re.search(pattern, raw_text, re.IGNORECASE):
                    conn.execute(
                        "INSERT INTO posting_skill (posting_id, skill, matched_pattern) VALUES (%s,%s,%s)",
                        (posting_id, skill, pattern),
                    )
                    break  # 이 공고에서 skill 하나는 패턴 중 하나만 매칭되면 충분 (중복 행 방지)
        count += 1
    conn.commit()
    return count


def ingest_skill_alias(conn: psycopg.Connection) -> None:
    conn.execute("DELETE FROM skill_alias")
    for skill, patterns in TRACKED_SKILLS.items():
        for pattern in patterns:
            conn.execute(
                "INSERT INTO skill_alias (canonical, pattern) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (skill, pattern),
            )
    conn.commit()


def ingest_candidate_evidence(conn: psycopg.Connection) -> None:
    conn.execute("DELETE FROM candidate_evidence")
    for skill, info in CANDIDATE_EVIDENCE.items():
        conn.execute(
            "INSERT INTO candidate_evidence (skill, evidence_level, note) VALUES (%s,%s,%s)",
            (skill, info["level"], info["note"]),
        )
    conn.commit()


def skill_counts(conn: psycopg.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT skill, COUNT(DISTINCT posting_id) FROM posting_skill GROUP BY skill"
    ).fetchall()
    return dict(rows)


def run() -> psycopg.Connection:
    conn = build_db()
    n = ingest_postings(conn)
    ingest_skill_alias(conn)
    ingest_candidate_evidence(conn)
    print(f"적재 완료: 공고 {n}건 (PostgreSQL)")
    return conn


if __name__ == "__main__":
    conn = run()
    print("기술별 집계:", skill_counts(conn))
