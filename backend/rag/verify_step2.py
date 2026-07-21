"""Plan A 2단계 종료 조건 검증 — "공고 수와 기술 집계가 원본 대조 결과와 일치한다".

`docs/rag-project-plans/01b_evaluation_set.md`의 EX/SY/AG/GP 기대값과 실제 DB 집계를 대조한다.
실행: backend/ 에서 `python3 -m rag.ingest && python3 -m rag.verify_step2`
"""
import sqlite3
import sys

from rag.ingest import DB_PATH, skill_counts

EXPECTED_SKILL_COUNTS = {
    "FastAPI": 12, "Python": 40, "Docker": 18, "Airflow": 15, "Terraform": 6, "Redis": 3,
    "Kubernetes": 21, "PostgreSQL": 4, "AWS": 25, "GCP": 13, "CI/CD": 20, "Observability": 28,
    "IaC": 7,
}

EXPECTED_INTERSECTIONS = [
    ("Docker", "Kubernetes", 13),
    ("Airflow", "Python", 12),
    ("CI/CD", "Observability", 11),
]

EXPECTED_TOTAL_POSTINGS = 70


def _intersect(conn: sqlite3.Connection, a: str, b: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM ("
        "  SELECT posting_id FROM posting_skill WHERE skill = ?"
        "  INTERSECT"
        "  SELECT posting_id FROM posting_skill WHERE skill = ?"
        ")",
        (a, b),
    ).fetchone()
    return row[0]


def main() -> bool:
    conn = sqlite3.connect(DB_PATH)
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
