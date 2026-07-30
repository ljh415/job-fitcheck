"""대화형 근거 기반 RAG — Agent(tool-use) 구조.

Phase 1~4의 "질문을 7종 중 하나로 분류 → 그 유형에 고정된 함수 하나만 실행"(`query_router.py`)
방식은, 질문이 애매하거나 여러 능력을 조합해야 답할 수 있을 때 완전히 무관한 답을 내놓는 구조적
결함이 실측으로 확인됐다(2026-07-28, 사용자가 "내가 RAG를 안하면 많이 불리할까?" 질문으로 재현 —
`single_skill_gap`으로 분류돼 원본 데이터만 던지고 질문 자체엔 답을 안 함).

이 모듈은 그 대신, 기존 함수들(`market_demand_hybrid`/`assess_gap`/`assess_all_gaps`/
`list_postings`/`judge_topic_postings`/`compare_postings`/`generate_action_plan`/
`generate_sequenced_plan`)을 도구로 노출하고, LLM이 질문마다 어떤 도구를(몇 개든, 안 쓰든) 쓸지
스스로 판단해 답하게 한다(ReAct 스타일 tool-use 루프, `llm.base.LLMProvider.run_agent()`).

Claude/Gemini/OpenAI 셋 다 지원한다(2026-07-30 — main이 이미 셋을 위계 없이 동등하게 다루므로,
Agent도 같은 원칙을 따라야 필수 키(Gemini)만 있는 사용자도 쓸 수 있음. 처음엔 "Claude 먼저 만들고
나중에 확장"으로 Claude만 구현했었는데, 그 방식대로 main에 이식하면 대다수 사용자가 못 쓰게 돼
셋 다 먼저 완성함). 임베딩(`embed_provider`)만 빼고, 오케스트레이션(`answer_query_agent()`)과
도구 내부 판정(`_make_tool_executor()`)은 `llm.router.high_provider()`가 반환하는(=main의
현재 설정을 따르는) **같은 provider/model 인스턴스 하나**를 공유한다 — 두 곳에 각각 provider를
따로 하드코딩하면 구조적으로 어긋날 위험이 있어서다(2026-07-29 발견·수정 이후 유지).
"""
import json
import logging
import uuid
from datetime import datetime

import psycopg

from config import get_active_provider, settings
from llm.base import LLMProvider
from llm.router import high_provider
from services.usage_tracker import current_request_id
from rag.answer import (
    generate_action_plan,
    generate_sequenced_plan,
    rank_priority_gaps,
    summarize_strengths,
)
from rag.embed.base import EmbeddingProvider
from rag.postgres.gap import assess_all_gaps, assess_gap, market_demand_hybrid
from rag.postgres.query_router import compare_postings, judge_topic_postings, list_postings
from rag.skills import TRACKED_SKILLS, normalize_skill

logger = logging.getLogger(__name__)

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


def _without_excerpts(gap_result: dict) -> dict:
    """Agent에게 여러 기술을 한 번에 요약해줄 때, 기술마다 딸려오는 이력서 원문 발췌(`excerpts`)를
    그대로 다 넘기면 입력 토큰이 기술 수만큼 불어난다(2026-07-29 실측: 13개 기술 합산 시 4~9만
    토큰까지 치솟음). 요약 판단에는 skill/evidence_level/reasoning/market_demand만 있으면 충분하고,
    특정 기술의 발췌문이 필요하면 Agent가 assess_skill_gap(단일 기술)을 별도로 불러 확인하면 된다."""
    return {k: v for k, v in gap_result.items() if k != "excerpts"}


def _make_tool_executor(
    conn: psycopg.Connection, embed_provider: EmbeddingProvider, llm: tuple[LLMProvider, str, str | None]
):
    # 도구의 판정 LLM은 임베딩(embed_provider)만 빼고 오케스트레이션과 같은 provider/model을 쓴다
    # (`llm`은 answer_query_agent()가 만든 것 그대로 전달받음 — 이 함수 안에서 별도로 다시
    # 만들지 않는다. Provider를 하드코딩한 자리가 두 곳으로 나뉘면, 나중에 Claude 외 provider로
    # Agent를 확장할 때 한쪽만 고치고 다른 쪽을 놓칠 수 있다). 이 함수들은 /api/rag/gap-check 등
    # 기존 호출부와도 공유되므로, 여기서 `llm`을 명시적으로 넘길 때만 이 지정이 적용되고 그 외
    # 호출부는 기존처럼 메인 앱 설정을 그대로 따른다(회귀 없음).
    claude_llm = llm
    all_gaps_cache: list[dict] | None = None
    skill_gap_cache: dict[str, dict] = {}

    async def get_all_gaps() -> list[dict]:
        # 한 턴 안에서 assess_all_gaps_summary와 generate_sequenced_plan_for_priority_gaps를
        # 둘 다 부르면(도구 설명이 그 순서를 안내함) 같은 13개 기술이 두 번 판정되는 걸 막는다
        # — 이 executor는 요청 1건마다 새로 만들어지므로(_make_tool_executor 호출 시점), 캐시가
        # 다음 요청으로 새는 일은 없다.
        nonlocal all_gaps_cache
        if all_gaps_cache is None:
            all_gaps_cache = await assess_all_gaps(conn, embed_provider, llm=claude_llm)
        return all_gaps_cache

    async def get_skill_gap(skill: str) -> dict:
        # get_all_gaps()와 같은 이유 — generate_action_plan_for_skill의 도구 설명이 "이미
        # assess_skill_gap으로 확인한 뒤에 쓰세요"라고 순서를 안내해서, 한 턴 안에 같은 기술로
        # 둘 다 부르면 assess_gap()(LLM 판정 포함)이 두 번 실행됐다(2026-07-29 발견).
        if skill not in skill_gap_cache:
            skill_gap_cache[skill] = await assess_gap(conn, skill, embed_provider, llm=claude_llm)
        return skill_gap_cache[skill]

    async def execute(name: str, args: dict) -> dict:
        if name == "get_market_demand":
            return await market_demand_hybrid(conn, args["skill"], embed_provider, llm=claude_llm)

        if name == "assess_skill_gap":
            return await get_skill_gap(args["skill"])

        if name == "assess_all_gaps_summary":
            results = await get_all_gaps()
            return {
                "priority_gaps": [_without_excerpts(r) for r in rank_priority_gaps(results)],
                "strengths": [_without_excerpts(r) for r in summarize_strengths(results)],
            }

        if name == "list_matching_postings":
            topic = normalize_skill(args["topic"])
            job_role = args.get("job_role") or ""
            if not topic and not job_role:
                # topic이 스키마상 required지만 빈 문자열도 통과되는 tool-use 특성상, Claude가
                # 필터 없이 이 도구를 부르면 judge_topic_postings(topic="")로 떨어져 빈 주제로
                # LLM 판정을 도는 낭비 호출이 될 수 있다 — 신뢰 경계 입력 검증(2026-07-29 발견).
                return {"error": "topic 또는 job_role 중 최소 하나는 필요합니다."}
            if topic in TRACKED_SKILLS:
                return {"postings": list_postings(conn, skill=topic, job_title=job_role)}
            if job_role and not topic:
                return {"postings": list_postings(conn, job_title=job_role)}
            return {
                "postings": await judge_topic_postings(
                    conn, topic, embed_provider, method="llm", job_role=job_role, llm=claude_llm
                )
            }

        if name == "compare_companies":
            return {"comparison": compare_postings(conn, args["company_names"])}

        if name == "generate_action_plan_for_skill":
            gap_result = await get_skill_gap(args["skill"])
            if gap_result["evidence_level"] == "직접 근거":
                return {"message": "이미 직접 근거가 있어 행동 계획이 불필요합니다.", "gap": gap_result}
            plan = await generate_action_plan(gap_result, llm=claude_llm)
            return {"gap": gap_result, "action_plan": plan}

        if name == "generate_sequenced_plan_for_priority_gaps":
            results = await get_all_gaps()
            priority_gaps = rank_priority_gaps(results)
            if not priority_gaps:
                return {"message": "보완이 필요한 우선순위 gap이 없습니다."}
            plan = await generate_sequenced_plan(priority_gaps, llm=claude_llm)
            return {"priority_gaps": [_without_excerpts(r) for r in priority_gaps], "plan": plan}

        return {"error": f"알 수 없는 도구: {name}"}

    return execute


def _log_agent_call(
    question: str, history_len: int, tool_calls: list[dict], answer: str, provider: str, request_id: str
) -> None:
    """도구 호출 트레이스를 data/rag_agent_log.jsonl에 한 줄 append한다.

    usage_log.jsonl과 같은 이유로 .tmp/os.replace 없이 단순 append — 매번 파일 전체를 재작성하는
    게 아니라 한 줄만 끝에 추가하므로 그 패턴이 필요 없다(config.py/storage.py는 반대로 파일 전체를
    재작성하는 경우라 그 패턴을 쓴다).

    `request_id`는 이 요청 동안 발생한 usage_log.jsonl의 LLM 호출들과 나중에 조인해서 비용을
    보기 위한 연결 키일 뿐이다 — 비용 자체는 여기 안 넣는다(2026-07-29, 두 로그의 목적을
    안 섞기 위해 사용자가 이 방식으로 확정)."""
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "request_id": request_id,
        "question": question,
        "history_len": history_len,
        "tool_calls": [
            {
                "name": tc.get("tool"),
                "args": tc.get("args"),
                # 잘라내면 나중에 답변 충실성(도구 결과 vs 최종 답변 대조) 검증 시마다 DB를
                # 다시 조회해야 해서, 개인용 앱 규모에서 무의미한 절약(디스크 몇 KB)보다 손해가
                # 컸음(2026-07-29 실측) — 자르지 않고 전체를 남긴다.
                "result_summary": str(tc.get("result")),
            }
            for tc in tool_calls
        ],
        "answer_preview": answer,
        "provider": provider,
    }
    try:
        path = settings.data_dir / "rag_agent_log.jsonl"
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("에이전트 도구 호출 로그 기록 실패: %s", e)


async def answer_query_agent(
    conn: psycopg.Connection,
    question: str,
    embed_provider: EmbeddingProvider,
    history: list[dict] | None = None,
) -> dict:
    request_id = uuid.uuid4().hex[:12]
    token = current_request_id.set(request_id)
    result: dict | None = None
    provider_name = get_active_provider()
    try:
        provider, model = high_provider()
        tool_executor = _make_tool_executor(conn, embed_provider, llm=(provider, model, None))
        result = await provider.run_agent(
            system=AGENT_SYSTEM,
            question=question,
            tools=TOOL_DEFS,
            tool_executor=tool_executor,
            model=model,
            operation="RAG 에이전트 응답",
            history=history,
        )
        return {"answer": result["text"], "tool_calls": result["tool_calls"]}
    finally:
        # run_agent()가 예외를 던지면(LLMAPIError 등) 기존엔 이 블록까지 못 와서 실패한
        # 요청이 rag_agent_log.jsonl에 안 남았다 — usage_log.jsonl의 request_id와 조인할
        # 대상이 없어짐(Codex 리뷰로 발견, 2026-07-29). 성공/실패 둘 다 finally에서 남긴다.
        current_request_id.reset(token)
        _log_agent_call(
            question=question,
            history_len=len(history or []),
            tool_calls=(result or {}).get("tool_calls", []),
            answer=(result or {}).get("text") or "(요청 실패 — 로그만 남김)",
            provider=provider_name,
            request_id=request_id,
        )
