"""Plan B 6단계 — 답변 생성·검증, PostgreSQL판. `rag/answer.py`(SQLite, Plan A)의 포팅.

DB를 안 건드리는 순수 함수(`generate_action_plan`/`format_report`/`rank_priority_gaps`/
`summarize_strengths`/`generate_sequenced_plan` — 전부 dict/문자열만 다루고 `conn` 파라미터가 없음)는
새로 만들지 않고 `rag.answer`에서 그대로 재사용한다. `conn`을 받는 함수만 새로 작성한다.

실행: backend/ 에서 `python3 -m rag.postgres.answer` — CANDIDATE_EVIDENCE 중 실제 gap에 대해
전체 리포트를 생성한다. `python3 -m rag.postgres.answer --aggregate` — 집계 리포트 생성.
"""
import argparse
import asyncio

from rag.answer import (
    format_report,
    generate_action_plan,
    generate_sequenced_plan,
    rank_priority_gaps,
    summarize_strengths,
)
from rag.embed.local import LocalEmbeddingProvider
from rag.postgres.db import get_connection
from rag.postgres.gap import assess_all_gaps, assess_gap
from rag.skills import CANDIDATE_EVIDENCE


async def full_report(conn, skill: str, embed_provider) -> str:
    gap_result = await assess_gap(conn, skill, embed_provider)
    action_plan = None
    if gap_result["evidence_level"] != "직접 근거":
        action_plan = await generate_action_plan(gap_result)
    return format_report(gap_result, action_plan)


async def run_known_gaps() -> None:
    """CANDIDATE_EVIDENCE 중 '직접 근거'가 아닌 실제 gap에 대해 전체 리포트를 생성한다."""
    conn = get_connection()
    provider = LocalEmbeddingProvider()
    try:
        gaps = [s for s, v in CANDIDATE_EVIDENCE.items() if v["level"] != "직접 근거"]
        print(f"실제 gap으로 분류된 기술: {gaps}\n")
        for skill in gaps:
            report = await full_report(conn, skill, provider)
            print(report)
            print()
    finally:
        provider.close()


async def run_aggregate_report() -> None:
    """01b GP-01/GP-06/AC-06처럼 여러 기술을 종합하는 집계형 질문에 대한 리포트를 생성한다."""
    conn = get_connection()
    provider = LocalEmbeddingProvider()
    try:
        results = await assess_all_gaps(conn, provider)

        print("=" * 60)
        print("GP-01 스타일 — 우선순위 gap 상위 3개 (시장 수요 순)")
        print("01b 기대값: CI/CD 20건, GCP 13건, IaC 7건을 우선 검토")
        print("=" * 60)
        priority_gaps = rank_priority_gaps(results, top_n=3)
        for g in priority_gaps:
            d = g["market_demand"]
            print(f"- {g['skill']}: {d['matched']}/{d['total']}건({d['ratio']*100:.1f}%), 판정={g['evidence_level']}")

        print()
        print("=" * 60)
        print("GP-06 스타일 — 입증된 강점(직접 근거) 전체")
        print("01b 기대값: Python, AWS, Docker, Kubernetes, Airflow, FastAPI와 관측성 근거를 구분해 제시")
        print("=" * 60)
        strengths = summarize_strengths(results)
        for s in strengths:
            print(f"- {s['skill']}: {s['reasoning'][:80]}")

        print()
        print("=" * 60)
        print("AC-06 스타일 — 우선순위 gap 순서·완료 기준 계획")
        print("01b 기대값: CI/CD를 먼저 끝내고 GCP와 IaC를 하나의 제한된 운영 실험으로 연결")
        print("=" * 60)
        plan = await generate_sequenced_plan(priority_gaps)
        for phase in plan["phases"]:
            print(f"[{phase['order']}단계] {', '.join(phase['skills'])}")
            print(f"  근거: {phase['rationale']}")
            print(f"  활동: {phase['activity']}")
            print(f"  완료 기준: {phase['completion_criteria']}")
        print(f"\n중단 조건: {plan['overall_stop_condition']}")
    finally:
        provider.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--aggregate", action="store_true", help="GP-01/GP-06/AC-06 스타일 집계 리포트 생성")
    args = parser.parse_args()
    if args.aggregate:
        asyncio.run(run_aggregate_report())
    else:
        asyncio.run(run_known_gaps())
