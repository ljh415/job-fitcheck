"""Plan B 3단계 종료 조건 중 "SQL 집계가 원본 검증값과 일치" 검증 — PostgreSQL판.
`rag/verify_step2.py`(SQLite)의 포팅. 기대값은 새로 정의하지 않고 원본(`rag.verify_step2`)에서
그대로 가져온다 — 정답은 하나뿐이고 저장소가 바뀐다고 달라지지 않는다.

실행: backend/ 에서 `python3 -m rag.postgres.verify`
"""
import sys

import psycopg

from rag.postgres.db import get_connection
from rag.postgres.ingest import skill_counts
from rag.verify_step2 import EXPECTED_INTERSECTIONS, EXPECTED_SKILL_COUNTS, EXPECTED_TOTAL_POSTINGS


def _intersect(conn: psycopg.Connection, a: str, b: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT posting_id FROM posting_skill WHERE skill = %s"
        "  INTERSECT"
        "  SELECT posting_id FROM posting_skill WHERE skill = %s"
        ") AS t",
        (a, b),
    ).fetchone()
    return row[0]


def main() -> bool:
    conn = get_connection()
    ok = True

    total = conn.execute("SELECT COUNT(*) FROM posting").fetchone()[0]
    status = "OK" if total == EXPECTED_TOTAL_POSTINGS else "FAIL"
    ok &= total == EXPECTED_TOTAL_POSTINGS
    print(f"{status}: 전체 공고 수 = {total} (기대 {EXPECTED_TOTAL_POSTINGS})")

    counts = skill_counts(conn)
    for skill, expected in EXPECTED_SKILL_COUNTS.items():
        actual = counts.get(skill, 0)
        status = "OK" if actual == expected else "FAIL"
        ok &= actual == expected
        print(f"{status}: {skill} = {actual} (기대 {expected})")

    for a, b, expected in EXPECTED_INTERSECTIONS:
        actual = _intersect(conn, a, b)
        status = "OK" if actual == expected else "FAIL"
        ok &= actual == expected
        print(f"{status}: {a} ∩ {b} = {actual} (기대 {expected})")

    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
