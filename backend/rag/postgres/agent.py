"""대화형 근거 기반 RAG — Agent(tool-use) 구조.

Phase 1~4의 "질문을 7종 중 하나로 분류 → 그 유형에 고정된 함수 하나만 실행"(`query_router.py`)
방식은, 질문이 애매하거나 여러 능력을 조합해야 답할 수 있을 때 완전히 무관한 답을 내놓는 구조적
결함이 실측으로 확인됐다(2026-07-28, 사용자가 "내가 RAG를 안하면 많이 불리할까?" 질문으로 재현 —
`single_skill_gap`으로 분류돼 원본 데이터만 던지고 질문 자체엔 답을 안 함).

이 모듈은 그 대신, 기존 함수들(`market_demand_hybrid`/`assess_gap`/`assess_all_gaps`/
`list_postings`/`judge_topic_postings`/`compare_postings`/`generate_action_plan`/
`generate_sequenced_plan`)을 도구로 노출하고, LLM이 질문마다 어떤 도구를(몇 개든, 안 쓰든) 쓸지
스스로 판단해 답하게 한다(ReAct 스타일 tool-use 루프, `llm.base.LLMProvider.run_agent()`).

Claude만 이 루프를 지원한다(2026-07-28 확정 — "Claude 먼저 만들고 나중에 provider 확장"). 메인 앱의
provider 설정(Gemini 등)과 무관하게, 이 에이전트는 항상 Claude를 직접 쓴다.
"""
import psycopg

from config import settings
from llm.anthropic import AnthropicProvider
from rag.answer import (
    generate_action_plan,
    generate_sequenced_plan,
    rank_priority_gaps,
    summarize_strengths,
)
from rag.embed.base import EmbeddingProvider
from rag.postgres.gap import assess_all_gaps, assess_gap, market_demand_hybrid
from rag.postgres.query_router import compare_postings, judge_topic_postings, list_postings
from rag.skills import TRACKED_SKILLS

AGENT_SYSTEM = """당신은 채용공고·후보자 프로필 데이터를 근거로 커리어 질문에 답하는 어시스턴트입니다.

아래 도구들을 필요한 만큼(0개, 1개, 여러 개) 자유롭게 사용해 사실을 확인한 뒤, 그 근거를 바탕으로
**질문에 실제로 답하세요.** 도구 결과를 그대로 나열하지 말고, 질문이 요구하는 형태(의견, 판단, 목록,
숫자, 계획 등)로 종합해서 답하세요 — 예를 들어 "불리할까?"라고 물으면 예/아니오에 가까운 판단과 그
근거를 주고, "어디어디야?"라고 물으면 목록을 주는 식입니다.

반드시 지켜야 할 규칙:
- 도구가 반환한 사실(숫자, 근거 수준, 발췌문)에 없는 내용을 추측하지 마세요.
- 도구 결과가 질문에 답하기에 근거가 부족하면, 무엇이 부족한지 솔직히 말하세요 — 억지로 답을
  꾸며내지 마세요.
- 이 도메인 데이터(수집한 공고, 후보자 프로필)로 원천적으로 답할 수 없는 질문(예: 미래 예측,
  채용과 무관한 주제)이면, 도구를 쓰지 말고 왜 답할 수 없는지 설명하세요.
- 여러 도구의 결과를 조합해야 답이 되는 질문이면 필요한 도구를 전부 호출한 뒤 종합하세요."""

TOOL_DEFS = [
    {
        "name": "get_market_demand",
        "description": "특정 기술/개념을 요구하는 채용공고가 전체 중 몇 건·몇 %인지 정확한 숫자로 계산합니다. 시장 수요·경쟁력 판단의 근거로 씁니다.",
        "input_schema": {
            "type": "object",
            "properties": {"skill": {"type": "string", "description": "기술/개념명(예: Redis, RAG, GCP)"}},
            "required": ["skill"],
        },
    },
    {
        "name": "assess_skill_gap",
        "description": "후보자 프로필에 특정 기술/개념에 대한 실무 경험 근거가 있는지 판정합니다. 근거 수준(직접 근거/부분 근거/인접 경험/근거 없음), 판정 이유, 관련 발췌문, 시장 수요를 함께 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {"skill": {"type": "string", "description": "기술/개념명"}},
            "required": ["skill"],
        },
    },
    {
        "name": "assess_all_gaps_summary",
        "description": "미리 정의된 핵심 기술 전체에 대해 근거 수준을 평가해, 우선 보완해야 할 gap과 이미 강점인 기술을 정리합니다. 파라미터 없음.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_matching_postings",
        "description": "특정 기술·직무·주제에 맞는 채용공고 목록을 회사명·직무와 함께 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "기술명 또는 자유 텍스트 주제(예: Redis, 헬스케어 관련 경험)"},
                "job_role": {"type": "string", "description": "직무명 필터(선택, 없으면 빈 문자열)"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "compare_companies",
        "description": "회사 둘 이상의 공고 정보(기술스택·복지·안정성·잡플래닛평점·적합도 등)를 나란히 비교합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "company_names": {"type": "array", "items": {"type": "string"}, "description": "비교할 회사명 목록"},
            },
            "required": ["company_names"],
        },
    },
    {
        "name": "generate_action_plan_for_skill",
        "description": "특정 기술의 gap을 보완하기 위한 구체적 활동·남길 증거·완료 조건을 제안합니다. 이미 assess_skill_gap으로 근거 수준을 확인해 gap이 실제로 있을 때만 쓰세요.",
        "input_schema": {
            "type": "object",
            "properties": {"skill": {"type": "string", "description": "기술/개념명"}},
            "required": ["skill"],
        },
    },
    {
        "name": "generate_sequenced_plan_for_priority_gaps",
        "description": "우선순위 gap 여러 개를 한 번에 보완할 순서와 완료 기준을 제안합니다. 먼저 assess_all_gaps_summary로 우선순위 gap을 확인한 뒤에 쓰세요. 파라미터 없음.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _make_tool_executor(conn: psycopg.Connection, embed_provider: EmbeddingProvider):
    async def execute(name: str, args: dict) -> dict:
        if name == "get_market_demand":
            return await market_demand_hybrid(conn, args["skill"], embed_provider)

        if name == "assess_skill_gap":
            return await assess_gap(conn, args["skill"], embed_provider)

        if name == "assess_all_gaps_summary":
            results = await assess_all_gaps(conn, embed_provider)
            return {
                "priority_gaps": rank_priority_gaps(results),
                "strengths": summarize_strengths(results),
            }

        if name == "list_matching_postings":
            topic = args["topic"]
            job_role = args.get("job_role") or ""
            if topic in TRACKED_SKILLS:
                return {"postings": list_postings(conn, skill=topic, job_title=job_role)}
            if job_role and not topic:
                return {"postings": list_postings(conn, job_title=job_role)}
            return {"postings": await judge_topic_postings(conn, topic, embed_provider, method="llm")}

        if name == "compare_companies":
            return {"comparison": compare_postings(conn, args["company_names"])}

        if name == "generate_action_plan_for_skill":
            gap_result = await assess_gap(conn, args["skill"], embed_provider)
            if gap_result["evidence_level"] == "직접 근거":
                return {"message": "이미 직접 근거가 있어 행동 계획이 불필요합니다.", "gap": gap_result}
            plan = await generate_action_plan(gap_result)
            return {"gap": gap_result, "action_plan": plan}

        if name == "generate_sequenced_plan_for_priority_gaps":
            results = await assess_all_gaps(conn, embed_provider)
            priority_gaps = rank_priority_gaps(results)
            if not priority_gaps:
                return {"message": "보완이 필요한 우선순위 gap이 없습니다."}
            plan = await generate_sequenced_plan(priority_gaps)
            return {"priority_gaps": priority_gaps, "plan": plan}

        return {"error": f"알 수 없는 도구: {name}"}

    return execute


async def answer_query_agent(
    conn: psycopg.Connection,
    question: str,
    embed_provider: EmbeddingProvider,
    history: list[dict] | None = None,
) -> dict:
    provider = AnthropicProvider()
    tool_executor = _make_tool_executor(conn, embed_provider)
    result = await provider.run_agent(
        system=AGENT_SYSTEM,
        question=question,
        tools=TOOL_DEFS,
        tool_executor=tool_executor,
        model=settings.claude_high_model,
        operation="RAG 에이전트 응답",
        history=history,
    )
    return {"answer": result["text"], "tool_calls": result["tool_calls"]}
