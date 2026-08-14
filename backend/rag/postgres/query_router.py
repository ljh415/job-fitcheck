"""자유 텍스트 주제 검색 + 공고 목록/비교 — Agent(agent.py)가 실제로 쓰는 조회 함수들.

옛 Phase 1~4의 "질문을 7종으로 분류 → 고정 함수 실행" 라우팅 시스템(QUERY_TYPES/classify_query/
answer_query)은 2026-07-28 Agent(tool-use) 전환 이후 완전히 대체돼 어디서도 안 쓰였다(2026-07-29
Codex 리뷰로 참조 없음 재확인). 미래에 다시 쓸 계획도 없어 2026-07-30 삭제 — git 히스토리에는
그대로 남아있다.

경위·설계 결정 상세는 `docs/rag-project-plans/conversational-rag/00_design.md` Phase 1 참고.
"""
import asyncio

import psycopg

from llm.base import LLMProvider
from llm.router import capture_snapshot, high_from_snapshot
from rag.embed.base import EmbeddingProvider
from rag.gap import (
    JUDGE_CANDIDATES_SYSTEM,
    JUDGE_CANDIDATES_TOOL_DESCRIPTION,
    JUDGE_CANDIDATES_TOOL_NAME,
    JUDGE_CANDIDATES_TOOL_SCHEMA,
)
from rag.postgres.retrieval import search_chunks

TOPIC_LOCAL_TOP_K = 15  # method="local"일 때 벡터 검색 반환 개수(비교/실험용)


def list_postings(
    conn: psycopg.Connection, skill: str = "", job_title: str = "", limit: int = 50
) -> list[dict]:
    """공고 목록 조회/필터 — 순수 SQL, LLM 호출 없음. skill과 job_title을 동시에 주면 둘 다
    적용한다(2026-07-29 발견 — 예전엔 skill이 있으면 job_title을 무시해서 "백엔드 직무 중 AWS
    공고" 같은 복합 질문이 전체 AWS 공고를 반환했음, Codex 리뷰)."""
    if skill:
        query = (
            "SELECT p.slug, p.company_name, p.job_title FROM posting p"
            " JOIN posting_skill ps ON ps.posting_id = p.id"
            " WHERE ps.skill = %s"
        )
        params: list = [skill]
        if job_title:
            query += " AND p.job_title ILIKE %s"
            params.append(f"%{job_title}%")
        query += " ORDER BY p.company_name LIMIT %s"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
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
    conn: psycopg.Connection,
    topic: str,
    job_role: str = "",
    llm: tuple[LLMProvider, str, str | None] | None = None,
) -> list[dict]:
    """자유 텍스트 주제(TRACKED_SKILLS 밖)를 corpus 전체 원문으로 LLM이 직접 판정한다.

    벡터 코사인 유사도로 후보를 미리 추려서 넘기면(_candidate_postings 패턴) 이 corpus에서는
    1차 검색 단계 자체가 진짜 정답을 통째로 놓치는 사례가 실측으로 확인됐다(2026-07-28,
    docs/rag-project-plans/00_meta/HISTORY.md 해당 항목). corpus 규모가 작아(공고 수십~백 건)
    점수로 거르지 않고 전체를 LLM에 넘기는 쪽이 비용 대비 recall이 훨씬 낫다.

    `job_role`을 주면 LLM 판정 전에 SQL로 직무를 먼저 좁힌다(2026-07-29 발견 — 예전엔 이
    파라미터 자체가 없어서 "백엔드 직무 중 헬스케어 경험" 같은 복합 질문에서 직무 조건이
    통째로 무시됐음, Codex 리뷰). LLM 호출 비용도 같이 줄어드는 부수 효과가 있다."""
    query = (
        "SELECT po.id, po.slug, po.company_name, po.job_title, dc.text"
        " FROM document_chunk dc JOIN posting po ON po.slug = dc.source_id"
        " WHERE dc.source_type = 'posting_raw'"
    )
    params: list = []
    if job_role:
        query += " AND po.job_title ILIKE %s"
        params.append(f"%{job_role}%")
    query += " ORDER BY po.id, dc.chunk_index"
    # 동기 DB 쿼리라 to_thread로 감싼다(Codex 4차 리뷰로 발견, 2026-08-03).
    rows = await asyncio.to_thread(lambda: conn.execute(query, params).fetchall())
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


def _judge_topic_postings_local(
    conn: psycopg.Connection, topic: str, embed_provider: EmbeddingProvider, job_role: str = ""
) -> list[dict]:
    """벡터 검색만으로 top-k를 그대로 반환한다(판정 없이 순위만) — LLM 호출 없는 비교/실험용 경로.
    이번 corpus에서는 점수 격차가 razor-thin이라 신뢰도가 낮다는 게 이미 확인됐다(정확도 우선이면
    method="llm" 기본값을 쓴다). job_role 필터는 LLM 경로와 함수 계약을 맞추기 위해 추가함
    (Codex 리뷰로 발견, 2026-07-29 — 처음엔 method="llm"만 고치고 이 비교용 경로는 안 건드렸다가
    같은 시그니처를 공유하는 두 구현이 서로 다르게 동작하는 게 지적됨).

    지금 아무 진입점에서도 안 불리는 코드다(judge_topic_postings의 유일한 실제 호출부인 agent.py가
    method="llm"을 하드코딩해서 부름) — 공고 데이터가 늘어나 벡터 방식을 다시 비교 테스트할 때
    쓰려고 의도적으로 남겨뒀다(2026-07-30, `docs/rag-project-plans/00_meta/STATUS.md` "향후 탐색
    아이디어" 참고). 그때는 이 함수를 evaluate_hybrid.py/hnsw_eval.py처럼 직접 호출해서 쓰면 된다."""
    # top_k=None(전체 청크) — job_role 필터가 순위 컷오프 뒤에 걸려서, 상한을 두면 그 상한
    # 밖에 있는 관련 공고를 원천적으로 놓칠 수 있었다(사용자 지적, 2026-07-30). 이 corpus는
    # 청크가 수백 개 수준이라 전체를 도는 비용이 무시할 만하다.
    results = search_chunks(conn, embed_provider, topic, source_type="posting_raw", top_k=None)
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
        if job_role and job_role.lower() not in (row[3] or "").lower():
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
    job_role: str = "",
    llm: tuple[LLMProvider, str, str | None] | None = None,
) -> list[dict]:
    if method == "local":
        return _judge_topic_postings_local(conn, topic, embed_provider, job_role=job_role)
    return await _judge_topic_postings_llm(conn, topic, job_role=job_role, llm=llm)
