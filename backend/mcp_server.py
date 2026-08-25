"""Job FitCheck MCP 서버 — Codex/Claude 같은 외부 AI가 회사·프로필·RAG 기능을 도구로 쓸 수
있게 한다. 전송은 HTTP/SSE(streamable HTTP), 인증은 `backend/main.py`의 기존 JWT Bearer
미들웨어를 그대로 재사용한다(`/api/mcp` 아래 마운트해서 별도 인증 코드를 만들지 않음).

도구는 기존 REST 핸들러·내부 함수를 얇게 감싸는 정도로만 구현한다 — MCP를 위해 앱 내부
함수를 새로 쪼개지 않는다(docs/planning/mcp_plan_notes.md "기존 회사 분석 로직 재사용 방향").

설계 배경: docs/planning/mcp_plan_notes.md(로컬 전용), 진행 기록: docs/mcp-server/(로컬 전용).
"""
import asyncio

import storage
from config import settings
from mcp.server.mcpserver.server import MCPServer
from rag.postgres.db import get_connection
from rag.postgres.query_router import list_postings
from routers import companies, profile, rag

mcp = MCPServer(name="job-fitcheck")


@mcp.tool()
async def get_rag_status() -> dict:
    """RAG(대화형 근거 기반 검색) 활성화 여부와 설정을 확인한다."""
    return await rag.status()


@mcp.tool()
async def list_companies(
    search: str = "",
    status: str = "",
    pinned_only: bool = False,
    min_score: int | None = None,
) -> list[dict]:
    """등록된 회사 목록을 조회한다. 인자를 안 주면 전체 목록을 반환한다.

    search: 회사명/직무/지역/상태에서 부분 일치 검색(대소문자 무시)
    status: 정확히 일치하는 상태(예: "지원", "탈락")만 필터
    pinned_only: True면 즐겨찾기(핀)된 회사만
    min_score: 이 점수 이상인 회사만(적합도 미평가 회사는 제외)
    """
    metas = storage.list_companies()
    q = search.lower().strip()
    result = []
    for meta in metas:
        fm = meta.frontmatter
        if pinned_only and not fm.pinned:
            continue
        if status and fm.status != status:
            continue
        if min_score is not None and (fm.fit_score is None or fm.fit_score < min_score):
            continue
        if q:
            haystacks = [fm.company_name, fm.display_name, fm.job_title, fm.location, fm.status]
            if not any(q in (h or "").lower() for h in haystacks):
                continue
        result.append(meta.model_dump())
    return result


@mcp.tool()
async def get_company(slug: str) -> dict:
    """특정 회사의 공고 원문, 분석 결과(frontmatter), 상태 로그(본문)를 조회한다."""
    record = storage.read_company(slug)
    if not record:
        raise ValueError(f"회사를 찾을 수 없습니다: {slug}")
    return record.model_dump()


@mcp.tool()
async def compare_companies(slugs: list[str]) -> list[dict]:
    """여러 회사의 공고와 적합도 정보를 나란히 비교한다(최대 5개)."""
    records = await companies.compare_companies(slugs)
    return [r.model_dump() for r in records]


@mcp.tool()
async def get_application_timeline() -> list[dict]:
    """지원한 회사들의 상태 변화 이력(지원→서류통과→인터뷰 등)을 시간순으로 조회한다."""
    return await companies.get_companies_timeline()


@mcp.tool()
async def get_profile() -> dict:
    """후보자 프로필(이력서 기반 구조화 정보 + 본문)을 조회한다."""
    record = await profile.get_profile()
    return record.model_dump()


@mcp.tool()
async def list_matching_postings(skill: str = "", job_title: str = "", limit: int = 50) -> dict:
    """기술 스택·직무명으로 공고를 검색한다(LLM 미사용, 순수 DB 조회). skill과 job_title을
    같이 주면 둘 다 만족하는 공고만 반환한다."""
    if not settings.rag_postgres_host:
        return {"enabled": False, "postings": []}
    conn = await asyncio.to_thread(get_connection)
    try:
        rows = await asyncio.to_thread(list_postings, conn, skill, job_title, limit)
        return {"enabled": True, "postings": rows}
    finally:
        await asyncio.to_thread(conn.close)
