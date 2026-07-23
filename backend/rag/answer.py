"""Plan A 7단계 — 답변 생성·검증.

6단계(`gap.py`)의 판정 결과를 사람이 읽는 답변으로 만든다:
  - 근거 설명: `assess_gap()`의 reasoning을 그대로 인용(추가 LLM 호출 없음 — 이미 자연어).
  - 행동 계획: evidence_level이 "직접 근거"가 아닌 실제 gap에 대해서만 생성.
    `01_lean_evidence_first_rag.md`/`01b_evaluation_set.md` AC 카테고리 기준을 그대로 프롬프트에
    반영 — 막연한 "토이 프로젝트 만들기"가 아니라 gap 유형에 맞는 구체적 활동 + 완료 조건.

5~6단계와 달리 "정답"이 숫자로 고정되지 않는 열린 문제라, 정량 검증 대신 01b가 정의한
실패 조건(완료 조건 없음, 근거 없는 성과 생성 등)에 걸리는지를 사람이 확인한다.

위 함수들은 기술 하나짜리 질문(AC-01~03 스타일)만 다룬다. 01b의 GP-01(우선순위 gap 랭킹)·
GP-06(전체 강점 요약)·AC-06(여러 gap 순서 정하기)처럼 여러 기술을 종합해야 하는 질문은
아래 `rank_priority_gaps()`/`summarize_strengths()`/`generate_sequenced_plan()`이 담당한다
(2026-07-23 추가 — 기존엔 이 집계 로직 자체가 없어서 그런 질문에 답을 낼 수 없었다).

실행: backend/ 에서 `python3 -m rag.answer` — CANDIDATE_EVIDENCE 중 "직접 근거"가 아닌
실제 gap(GCP/CI-CD/IaC)에 대해 전체 리포트를 생성한다.
`python3 -m rag.answer --aggregate` — TRACKED_SKILLS 전체를 종합해 GP-01/GP-06/AC-06 스타일
집계 리포트를 생성한다.
"""
import argparse
import asyncio
import sqlite3

from llm.router import capture_snapshot, high_from_snapshot
from rag.embed.local import LocalEmbeddingProvider
from rag.gap import assess_all_gaps, assess_gap
from rag.ingest import DB_PATH
from rag.skills import CANDIDATE_EVIDENCE

ACTION_PLAN_SYSTEM = """당신은 후보자의 기술 gap을 보완할 구체적인 행동 계획을 세우는 커리어 코치입니다.

아래 원칙을 반드시 지키세요:
- "새 토이 프로젝트를 만들어라" 같은 막연한 제안을 하지 마세요. gap 유형에 맞게 학습, 기존 프로젝트
  개선, 운영 실험, 포트폴리오 정리, 오픈소스 기여, 작은 샌드박스 실습 중 가장 적합한 형태를 고르세요.
- 후보자가 이미 보유한 인접 경험(있다면)을 활용하는 가장 작은 단위의 활동을 제안하세요.
- 이 활동을 완료했을 때 무엇을 "증거"로 남길 수 있는지 구체적으로 제시하세요(로그, 문서, 커밋,
  대시보드, 실패·복구 기록 등 — 자격증 공부나 강의 수료만으로는 실무 근거가 되지 않습니다).
- 완료 조건을 모호하지 않게, 확인 가능한 형태로 제시하세요.
- 후보자가 갖지 않은 성과나 경험을 만들어내지 마세요."""

ACTION_PLAN_TOOL_NAME = "propose_action_plan"
ACTION_PLAN_TOOL_DESCRIPTION = "기술 gap을 보완할 구체적인 행동 계획을 제출합니다."
ACTION_PLAN_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "activity": {
            "type": "string",
            "description": "구체적인 실행 활동. 어떤 인접 경험을 활용하는지 명시",
        },
        "evidence_to_produce": {
            "type": "string",
            "description": "완료 시 남길 증거(로그·문서·커밋·대시보드 등)",
        },
        "completion_criteria": {
            "type": "string",
            "description": "언제 완료로 볼지 확인 가능한 기준",
        },
    },
    "required": ["activity", "evidence_to_produce", "completion_criteria"],
}


async def generate_action_plan(gap_result: dict) -> dict:
    snap = capture_snapshot()
    high, high_model = high_from_snapshot(snap)

    user = (
        f"기술/개념: {gap_result['skill']}\n"
        f"현재 근거 판정: {gap_result['evidence_level']}\n"
        f"판정 근거: {gap_result['reasoning']}\n"
        f"시장 수요: 전체 공고 {gap_result['market_demand']['total']}건 중"
        f" {gap_result['market_demand']['matched']}건 요구"
        f"({gap_result['market_demand']['ratio']*100:.1f}%)\n\n"
        "이 gap을 보완할 가장 작은 단위의 구체적 활동을 제안하세요."
    )
    return await high.extract_structured(
        system=ACTION_PLAN_SYSTEM,
        user=user,
        tool_name=ACTION_PLAN_TOOL_NAME,
        tool_description=ACTION_PLAN_TOOL_DESCRIPTION,
        tool_schema=ACTION_PLAN_TOOL_SCHEMA,
        model=high_model,
        operation="행동 계획 생성",
        reasoning_effort=snap.reasoning_effort,
    )


def format_report(gap_result: dict, action_plan: dict | None) -> str:
    demand = gap_result["market_demand"]
    # 13개 고정 기술은 posting_skill 정확 매칭(method=exact), 그 외 자유 키워드는 후보를 LLM이
    # 개별 판정한 추정치(method=estimated) — 정밀도가 다르므로 표시도 구분한다.
    demand_label = (
        f"전체 {demand['total']}건 중 {demand['matched']}건({demand['ratio']*100:.1f}%) 요구"
        if demand.get("method") == "exact"
        else f"약 {demand['matched']}건/{demand['total']}건({demand['ratio']*100:.1f}%) 추정"
        f"(임베딩+키워드 검색으로 모은 후보 {demand.get('candidate_count', '?')}건 중 LLM이 판정한 결과 — 참고용, 정확한 전수 집계 아님)"
    )
    lines = [
        f"## {gap_result['skill']}",
        f"- 시장 수요: {demand_label}",
        f"- 근거 판정: **{gap_result['evidence_level']}**",
        f"- 판정 근거: {gap_result['reasoning']}",
    ]
    if action_plan:
        lines += [
            "",
            f"**행동 계획**: {action_plan['activity']}",
            f"**남길 증거**: {action_plan['evidence_to_produce']}",
            f"**완료 조건**: {action_plan['completion_criteria']}",
        ]
    return "\n".join(lines)


async def full_report(conn: sqlite3.Connection, skill: str, embed_provider) -> str:
    gap_result = await assess_gap(conn, skill, embed_provider)
    action_plan = None
    if gap_result["evidence_level"] != "직접 근거":
        action_plan = await generate_action_plan(gap_result)
    return format_report(gap_result, action_plan)


async def run_known_gaps() -> None:
    """CANDIDATE_EVIDENCE 중 '직접 근거'가 아닌 실제 gap에 대해 전체 리포트를 생성한다."""
    conn = sqlite3.connect(DB_PATH)
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



# "부분 근거"는 gap.py의 validate()에서도 이미 "직접 근거"와 같은 방향(증거 있음)으로 취급했다
# (LLM이 같은 근거를 두고도 직접/부분을 오갈 만큼 그 경계가 미세해서). 여기서도 같은 관용도를
# 적용 — 안 그러면 실제로는 강점인 기술(예: Kubernetes)이 LLM의 그날그날 판정 편차로 "gap"에
# 잘못 끼어드는 문제가 생긴다(2026-07-23 첫 실행에서 실제로 발견됨).
_HAS_EVIDENCE = ("직접 근거", "부분 근거")
_TRUE_GAP = ("인접 경험", "근거 없음")


def rank_priority_gaps(results: list[dict], top_n: int = 3) -> list[dict]:
    """01b GP-01 스타일 — 실질적 증거가 없는(인접 경험/근거 없음) 진짜 gap만 추려 시장 수요 순으로
    정렬한다. 순위 계산은 코드가 하고 LLM은 관여하지 않는다(01_lean_evidence_first_rag.md 원칙)."""
    gaps = [r for r in results if r["evidence_level"] in _TRUE_GAP]
    gaps.sort(key=lambda r: r["market_demand"]["ratio"], reverse=True)
    return gaps[:top_n]


def summarize_strengths(results: list[dict]) -> list[dict]:
    """01b GP-06 스타일 — 실질적 증거가 있는(직접 근거/부분 근거) 기술만 강점 목록으로 추린다.
    필터링만 하고 LLM이 근거 없는 강점을 새로 만들어내지 않도록 순수 코드로만 처리한다."""
    return [r for r in results if r["evidence_level"] in _HAS_EVIDENCE]


SEQUENCE_PLAN_SYSTEM = """당신은 여러 기술 gap을 어떤 순서로, 어떤 완료 기준으로 보완할지 설계하는 커리어 코치입니다.

아래 원칙을 반드시 지키세요:
- 시장 수요가 높은 gap을 우선순위 앞쪽에 두되, 근거 수준(부분 근거/인접 경험/근거 없음)도 함께 고려하세요.
- 모든 gap을 각각 별도 프로젝트로 벌이지 마세요. 서로 연관된 gap은 하나의 제한된 실습/운영 실험으로
  묶어서 동시에 해결할 수 있는지 검토하세요(예: 여러 클라우드/인프라 gap을 하나의 샌드박스 실습으로 연결).
- 각 단계에 구체적인 활동과, 그 단계를 완료로 볼 수 있는 확인 가능한 기준을 제시하세요.
- 전체 계획을 중단하거나 재검토해야 할 조건(예: 시간·리소스 초과, 실험 결과 부정적 등)도 명시하세요."""

SEQUENCE_PLAN_TOOL_NAME = "propose_sequenced_plan"
SEQUENCE_PLAN_TOOL_DESCRIPTION = "여러 기술 gap을 보완할 순서와 완료 기준을 제출합니다."
SEQUENCE_PLAN_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "phases": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "order": {"type": "integer", "description": "진행 순서(1부터)"},
                    "skills": {"type": "array", "items": {"type": "string"}, "description": "이 단계에서 다루는 기술(들)"},
                    "rationale": {"type": "string", "description": "왜 이 순서/조합인지"},
                    "activity": {"type": "string", "description": "구체적인 실행 활동"},
                    "completion_criteria": {"type": "string", "description": "이 단계의 완료 확인 기준"},
                },
                "required": ["order", "skills", "rationale", "activity", "completion_criteria"],
            },
        },
        "overall_stop_condition": {
            "type": "string",
            "description": "전체 계획을 중단하거나 재검토해야 할 조건",
        },
    },
    "required": ["phases", "overall_stop_condition"],
}


async def generate_sequenced_plan(prioritized_gaps: list[dict]) -> dict:
    """01b AC-06 스타일 — 여러 gap을 어떤 순서·조합으로 보완할지 하나의 계획으로 만든다."""
    snap = capture_snapshot()
    high, high_model = high_from_snapshot(snap)

    gap_block = "\n\n".join(
        f"- {g['skill']} (근거 판정: {g['evidence_level']}, 시장 수요:"
        f" {g['market_demand']['matched']}/{g['market_demand']['total']}건"
        f" {g['market_demand']['ratio']*100:.1f}%)\n  판정 근거: {g['reasoning']}"
        for g in prioritized_gaps
    )
    user = f"다음 gap들을 보완할 순서와 완료 기준을 설계하세요:\n\n{gap_block}"

    return await high.extract_structured(
        system=SEQUENCE_PLAN_SYSTEM,
        user=user,
        tool_name=SEQUENCE_PLAN_TOOL_NAME,
        tool_description=SEQUENCE_PLAN_TOOL_DESCRIPTION,
        tool_schema=SEQUENCE_PLAN_TOOL_SCHEMA,
        model=high_model,
        operation="Gap 순서 계획 생성",
        reasoning_effort=snap.reasoning_effort,
    )


async def run_aggregate_report() -> None:
    """01b GP-01/GP-06/AC-06처럼 여러 기술을 종합하는 집계형 질문에 대한 리포트를 생성한다.
    TRACKED_SKILLS 전체를 판정한 뒤 우선순위 gap 랭킹, 강점 요약, 순서 계획을 만든다."""
    conn = sqlite3.connect(DB_PATH)
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
