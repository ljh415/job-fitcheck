"""대화형 근거 기반 RAG Phase 1 — 질문 이해와 라우팅.

지금 있는 함수(assess_gap 등)에 맞춰 질문을 끼워맞추는 게 아니라, "유연한 챗봇에 필요한 능력"을
먼저 정하고(단일 기술 Gap/전체 Gap·우선순위/시장 수요 통계/행동 계획/공고 목록 조회/공고 비교) 그중
없던 두 가지(공고 목록 조회, 공고 비교)를 이번에 새로 만들었다. `unanswerable`은 진짜 이 도메인
데이터로 답할 수 없는 질문에만 쓴다 — 위 6개 능력 중 하나에 해당하는데 여기로 떨어지면 버그다.

경위·설계 결정 상세는 `docs/rag-project-plans/conversational-rag/00_design.md` Phase 1 참고.
"""
import json

import psycopg

from llm.base import LLMProvider
from llm.router import capture_snapshot, high_from_snapshot, light_from_snapshot
from rag.answer import (
    generate_action_plan,
    generate_sequenced_plan,
    rank_priority_gaps,
    summarize_strengths,
)
from rag.embed.base import EmbeddingProvider
from rag.gap import (
    JUDGE_CANDIDATES_SYSTEM,
    JUDGE_CANDIDATES_TOOL_DESCRIPTION,
    JUDGE_CANDIDATES_TOOL_NAME,
    JUDGE_CANDIDATES_TOOL_SCHEMA,
)
from rag.postgres.gap import assess_all_gaps, assess_gap, market_demand_hybrid
from rag.postgres.retrieval import search_chunks
from rag.skills import TRACKED_SKILLS

TOPIC_LOCAL_TOP_K = 15  # method="local"일 때 벡터 검색 반환 개수(비교/실험용)

# 아래 QUERY_TYPES ~ classify_query()는 미사용 코드다(파일 끝의 answer_query()도 마찬가지 —
# 그 앞에 있는 list_postings/compare_postings/judge_topic_postings는 agent.py가 실제로 씀).
# Phase 1~4의 "질문을 7종으로 분류 → 고정 함수 실행" 라우팅 방식이며, 2026-07-28 Agent(tool-use)
# 전환 이후 `/api/rag/ask`가 더 이상 호출하지 않는다. 다른 곳에서도 참조 없음을 grep으로 확인
# 완료(2026-07-29, Codex 리뷰로 독립 재확인). 삭제하지 않고 주석으로만 표시 — 사용자 지침.
QUERY_TYPES = [
    "single_skill_gap",
    "all_gaps",
    "market_aggregate",
    "action_plan",
    "posting_list",
    "posting_comparison",
    "unanswerable",
]

CLASSIFY_SYSTEM = """당신은 채용 공고·후보자 프로필 RAG 시스템에 들어온 질문을 아래 7개 유형 중
하나로 분류하고, 필요한 조건을 추출하는 라우터입니다.

- single_skill_gap: 기술/개념 하나에 대해 후보자가 근거(경험)를 갖고 있는지 묻는 질문
  (예: "AWS를 안 해봤으면 단점이 될까?", "저 Redis 경험 있어요?")
- all_gaps: 여러 기술을 종합해서 강점·부족한 부분 전체를 묻는 질문 (예: "가장 부족한 스킬이 뭘까?",
  "내 강점이 뭐야?") — 특정 기술 하나로 좁혀지지 않는 질문
- market_aggregate: 시장에서 특정 기술을 요구하는 정도를 숫자로 묻는 질문 (예: "AWS 요구하는 공고
  몇 개야?", "Redis 수요가 얼마나 돼?") — "몇 개/얼마나"처럼 개수·비율을 원하는 질문
- action_plan: 부족한 부분을 어떻게 보완할지 순서·계획을 묻는 질문 (예: "뭘 준비해야 돼?", "어떤
  순서로 공부하면 좋을까?")
- posting_list: 특정 조건(기술·직무 등)에 맞는 공고를 목록으로 나열해달라는 질문 (예: "Redis
  요구하는 공고 어디어디야?", "백엔드 공고 뭐 있어?") — "어디어디/뭐 있어"처럼 목록을 원하는 질문.
  market_aggregate와 헷갈리지 말 것: 개수만 원하면 market_aggregate, 실제 목록을 원하면 posting_list.
- posting_comparison: 특정 회사 둘 이상을 직접 비교해달라는 질문 (예: "네이버랑 카카오 공고 비교해줘")
  — compare_targets에 언급된 회사명을 모두 추출
- unanswerable: 위 6개 중 어디에도 해당하지 않는 질문. 채용/기술 gap과 관련 없는 완전히 다른 주제일
  수도 있고, 이 도메인과 관련은 있지만 지금 갖고 있는 데이터(수집한 공고 원문, 후보자 프로필)로는
  원천적으로 답할 수 없는 질문(예: "제가 이 회사에 최종 합격할 수 있을까요?" — 면접 결과나 미래
  예측은 corpus에 없는 정보)일 수도 있다. 둘 다 unanswerable로 분류한다.

skill 필드에는 질문에서 언급된 구체적 기술/개념명을 추출한다(단일 기술 gap·시장 수요·공고 목록
질문일 때만 채움). compare_targets에는 posting_comparison일 때 언급된 회사명을 모두 배열로 담는다.
질문에 없는 정보는 채우지 않는다 — 추측하지 않는다.

이전 대화 상태가 함께 주어질 수 있다(직전 턴에서 쓰인 query_type/skill/job_role/compare_targets):
- 질문이 "그중에서", "거기서", "그거", "왜?" 등으로 이전 답변을 참조하면, 질문 자체에 새로 언급되지
  않은 필드는 이전 상태 값을 그대로 이어받아 채운다.
- 질문이 새로운 기술/직무/회사명을 명확히 언급하면, 그 필드는 이전 상태를 무시하고 새로 추출한 값을
  쓴다(주제가 바뀐 것이므로).
- 이전 대화 상태가 없거나 질문이 이전 내용과 무관한 완전히 새로운 주제면, 이전 상태를 참고하지 않고
  질문만으로 처음부터 분류한다."""

CLASSIFY_TOOL_NAME = "classify_rag_query"
CLASSIFY_TOOL_DESCRIPTION = "질문을 7개 유형 중 하나로 분류하고 필요한 조건을 추출해 제출합니다."
CLASSIFY_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query_type": {"type": "string", "enum": QUERY_TYPES},
        "skill": {
            "type": "string",
            "description": "질문에서 언급된 구체적 기술/개념명. 없으면 빈 문자열.",
        },
        "job_role": {
            "type": "string",
            "description": "질문에서 언급된 직무명(posting_list 필터용). 없으면 빈 문자열.",
        },
        "compare_targets": {
            "type": "array",
            "items": {"type": "string"},
            "description": "posting_comparison일 때 언급된 회사명 목록. 그 외엔 빈 배열.",
        },
    },
    "required": ["query_type"],
}

UNANSWERABLE_MESSAGE = (
    "이 질문은 지금 갖고 있는 데이터(수집한 채용 공고, 후보자 프로필)로는 답할 수 없습니다. 기술"
    " gap·시장 수요·공고 조회·비교·행동 계획 관련 질문을 해주세요."
)


async def classify_query(question: str, session_state: dict | None = None) -> dict:
    user = question
    if session_state:
        user = f"이전 대화 상태: {json.dumps(session_state, ensure_ascii=False)}\n\n질문: {question}"
    snap = capture_snapshot()
    light, light_model = light_from_snapshot(snap)
    result = await light.extract_structured(
        system=CLASSIFY_SYSTEM,
        user=user,
        tool_name=CLASSIFY_TOOL_NAME,
        tool_description=CLASSIFY_TOOL_DESCRIPTION,
        tool_schema=CLASSIFY_TOOL_SCHEMA,
        model=light_model,
        operation="RAG 질문 분류",
        reasoning_effort=snap.reasoning_effort,
    )
    return result


def list_postings(
    conn: psycopg.Connection, skill: str = "", job_title: str = "", limit: int = 50
) -> list[dict]:
    """공고 목록 조회/필터 — 순수 SQL, LLM 호출 없음."""
    if skill:
        rows = conn.execute(
            "SELECT p.slug, p.company_name, p.job_title FROM posting p"
            " JOIN posting_skill ps ON ps.posting_id = p.id"
            " WHERE ps.skill = %s ORDER BY p.company_name LIMIT %s",
            (skill, limit),
        ).fetchall()
    elif job_title:
        rows = conn.execute(
            "SELECT slug, company_name, job_title FROM posting WHERE job_title ILIKE %s"
            " ORDER BY company_name LIMIT %s",
            (f"%{job_title}%", limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT slug, company_name, job_title FROM posting ORDER BY company_name LIMIT %s",
            (limit,),
        ).fetchall()
    return [{"slug": r[0], "company_name": r[1], "job_title": r[2]} for r in rows]


def compare_postings(conn: psycopg.Connection, company_names: list[str]) -> list[dict]:
    """공고 비교 — posting의 구조화 필드를 그대로 나열(메인 앱 /api/companies/compare와 같은
    원리). LLM 호출 없음 — "왜 더 나은지" 판단은 이번 범위 밖(Phase 2 하이브리드 검색 이후)."""
    results = []
    for name in company_names:
        row = conn.execute(
            "SELECT slug, company_name, job_title, tech_stack, benefits, stability,"
            " employee_count, investment_stage, jobplanet_score, fit_score, strengths, gaps"
            " FROM posting WHERE company_name ILIKE %s LIMIT 1",
            (f"%{name}%",),
        ).fetchone()
        if not row:
            results.append({"query": name, "found": False})
            continue
        results.append({
            "query": name,
            "found": True,
            "slug": row[0],
            "company_name": row[1],
            "job_title": row[2],
            "tech_stack": row[3],
            "benefits": row[4],
            "stability": row[5],
            "employee_count": row[6],
            "investment_stage": row[7],
            "jobplanet_score": row[8],
            "fit_score": row[9],
            "strengths": row[10],
            "gaps": row[11],
        })
    return results


async def _judge_topic_postings_llm(
    conn: psycopg.Connection, topic: str, llm: tuple[LLMProvider, str, str | None] | None = None
) -> list[dict]:
    """자유 텍스트 주제(TRACKED_SKILLS 밖)를 corpus 전체 원문으로 LLM이 직접 판정한다.

    벡터 코사인 유사도로 후보를 미리 추려서 넘기면(_candidate_postings 패턴) 이 corpus에서는
    1차 검색 단계 자체가 진짜 정답을 통째로 놓치는 사례가 실측으로 확인됐다(2026-07-28,
    docs/rag-project-plans/00_meta/HISTORY.md 해당 항목). corpus 규모가 작아(공고 수십~백 건)
    점수로 거르지 않고 전체를 LLM에 넘기는 쪽이 비용 대비 recall이 훨씬 낫다."""
    rows = conn.execute(
        "SELECT po.id, po.slug, po.company_name, po.job_title, dc.text"
        " FROM document_chunk dc JOIN posting po ON po.slug = dc.source_id"
        " WHERE dc.source_type = 'posting_raw' ORDER BY po.id, dc.chunk_index"
    ).fetchall()
    postings: dict[int, dict] = {}
    order: list[int] = []
    for pid, slug, company, job_title, text in rows:
        if pid not in postings:
            postings[pid] = {"slug": slug, "company_name": company, "job_title": job_title, "text": ""}
            order.append(pid)
        postings[pid]["text"] += text
    plist = [postings[pid] for pid in order]
    if not plist:
        return []

    listing = "\n".join(
        f"{i + 1}. {p['company_name']} / {p['job_title']} — {p['text']}" for i, p in enumerate(plist)
    )
    if llm is not None:
        high, high_model, reasoning_effort = llm
    else:
        snap = capture_snapshot()
        high, high_model = high_from_snapshot(snap)
        reasoning_effort = snap.reasoning_effort
    result = await high.extract_structured(
        system=JUDGE_CANDIDATES_SYSTEM,
        user=f"기술/개념: {topic}\n\n후보 공고 목록:\n{listing}",
        tool_name=JUDGE_CANDIDATES_TOOL_NAME,
        tool_description=JUDGE_CANDIDATES_TOOL_DESCRIPTION,
        tool_schema=JUDGE_CANDIDATES_TOOL_SCHEMA,
        model=high_model,
        operation="주제 공고 판정(전체원문)",
        reasoning_effort=reasoning_effort,
    )
    valid = {r["number"]: r["reason"] for r in result["relevant"] if 1 <= r["number"] <= len(plist)}
    return [
        {"slug": plist[n - 1]["slug"], "company_name": plist[n - 1]["company_name"],
         "job_title": plist[n - 1]["job_title"], "method": "llm", "evidence": reason}
        for n, reason in sorted(valid.items())
    ]


def _judge_topic_postings_local(conn: psycopg.Connection, topic: str, embed_provider: EmbeddingProvider) -> list[dict]:
    """벡터 검색만으로 top-k를 그대로 반환한다(판정 없이 순위만) — LLM 호출 없는 비교/실험용 경로.
    이번 corpus에서는 점수 격차가 razor-thin이라 신뢰도가 낮다는 게 이미 확인됐다(정확도 우선이면
    method="llm" 기본값을 쓴다)."""
    results = search_chunks(conn, embed_provider, topic, source_type="posting_raw", top_k=60)
    seen: set[int] = set()
    postings: list[dict] = []
    for _score, chunk_id, _text in results:
        row = conn.execute(
            "SELECT po.id, po.slug, po.company_name, po.job_title FROM document_chunk dc"
            " JOIN posting po ON po.slug = dc.source_id WHERE dc.id = %s",
            (chunk_id,),
        ).fetchone()
        if not row or row[0] in seen:
            continue
        seen.add(row[0])
        postings.append({"slug": row[1], "company_name": row[2], "job_title": row[3], "method": "local"})
        if len(postings) >= TOPIC_LOCAL_TOP_K:
            break
    return postings


async def judge_topic_postings(
    conn: psycopg.Connection,
    topic: str,
    embed_provider: EmbeddingProvider,
    method: str = "llm",
    llm: tuple[LLMProvider, str, str | None] | None = None,
) -> list[dict]:
    if method == "local":
        return _judge_topic_postings_local(conn, topic, embed_provider)
    return await _judge_topic_postings_llm(conn, topic, llm=llm)


# 미사용 코드(위 QUERY_TYPES/classify_query()와 같은 이유) — 삭제 안 하고 주석 표시만.
async def answer_query(
    conn: psycopg.Connection,
    question: str,
    embed_provider: EmbeddingProvider,
    method: str = "llm",
    session_state: dict | None = None,
) -> dict:
    classification = await classify_query(question, session_state)
    query_type = classification.get("query_type", "unanswerable")
    skill = classification.get("skill") or None
    job_role = classification.get("job_role") or None
    compare_targets = classification.get("compare_targets") or []
    # 이번 턴에 실제로 쓰인 조건 — 다음 턴에 프론트가 그대로 돌려보내면 후속 질문("그중에서" 등)이
    # 이걸 이어받는다. 배열로 계속 쌓이는 대화 로그가 아니라, 매 턴 덮어써지는 작은 상태 하나다
    # (설계 근거: `conversational-rag/00_design.md` Phase 4 "필요한 직무·필터·공고 참조만 유지").
    new_session_state = {
        "query_type": query_type, "skill": skill, "job_role": job_role, "compare_targets": compare_targets,
    }

    if query_type == "single_skill_gap" and skill:
        gap_result = await assess_gap(conn, skill, embed_provider)
        action_plan = None
        if gap_result["evidence_level"] != "직접 근거":
            action_plan = await generate_action_plan(gap_result)
        result = {"query_type": query_type, **gap_result, "action_plan": action_plan}

    elif query_type == "all_gaps":
        results = await assess_all_gaps(conn, embed_provider)
        result = {
            "query_type": query_type,
            "priority_gaps": rank_priority_gaps(results),
            "strengths": summarize_strengths(results),
        }

    elif query_type == "market_aggregate" and skill:
        demand = await market_demand_hybrid(conn, skill, embed_provider)
        result = {"query_type": query_type, "skill": skill, "market_demand": demand}

    elif query_type == "action_plan":
        results = await assess_all_gaps(conn, embed_provider)
        priority_gaps = rank_priority_gaps(results)
        plan = await generate_sequenced_plan(priority_gaps) if priority_gaps else None
        result = {"query_type": query_type, "priority_gaps": priority_gaps, "plan": plan}

    elif query_type == "posting_list":
        job_title = job_role or ""
        if not skill or skill in TRACKED_SKILLS:
            postings = list_postings(conn, skill=skill or "", job_title=job_title)
        else:
            postings = await judge_topic_postings(conn, skill, embed_provider, method=method)
        result = {"query_type": query_type, "postings": postings}

    elif query_type == "posting_comparison":
        result = {"query_type": query_type, "comparison": compare_postings(conn, compare_targets)}

    else:
        # unanswerable, 또는 skill이 필요한데 못 뽑은 경우 — 후자는 "미지원"이 아니라 분류 실패이므로
        # 같은 안내 메시지로 정직하게 답하되 query_type은 그대로 남겨 프론트/로그에서 원인 구분 가능.
        result = {"query_type": "unanswerable", "message": UNANSWERABLE_MESSAGE}

    result["session_state"] = new_session_state
    return result
