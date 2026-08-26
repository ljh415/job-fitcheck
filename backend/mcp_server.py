"""Job FitCheck MCP 서버 — Codex/Claude 같은 외부 AI가 회사·프로필·RAG 기능을 도구로 쓸 수
있게 한다. 전송은 HTTP/SSE(streamable HTTP), 인증은 `backend/main.py`의 기존 JWT Bearer
미들웨어를 그대로 재사용한다(`/api/mcp` 아래 마운트해서 별도 인증 코드를 만들지 않음).

도구는 기존 REST 핸들러·내부 함수를 얇게 감싸는 정도로만 구현한다 — MCP를 위해 앱 내부
함수를 새로 쪼개지 않는다(docs/planning/mcp_plan_notes.md "기존 회사 분석 로직 재사용 방향").

설계 배경: docs/planning/mcp_plan_notes.md(로컬 전용), 진행 기록: docs/mcp-server/(로컬 전용).
"""
import asyncio
from typing import Literal

import prompts
import storage
from config import resolve_rag_embedding_provider, settings
from mcp.server.mcpserver.server import MCPServer
from models import CompanyFrontmatter
from pydantic import ValidationError
from rag.embed.google import GoogleEmbeddingProvider
from rag.embed.local import LocalEmbeddingProvider
from rag.postgres.db import get_connection
from rag.postgres.query_router import list_postings
from rag.postgres.retrieval import search_chunks
from rag.reindex_service import trigger_background as trigger_reindex_background
from routers import companies, profile, rag
from services import scraper

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


@mcp.tool()
async def update_company(
    slug: str,
    status: Literal["미지원", "지원", "서류통과", "인터뷰", "최종", "탈락", "보류", "지원마감"] | None = None,
    pinned: bool | None = None,
) -> dict:
    """회사의 지원 상태(status)·즐겨찾기(pinned)를 변경한다. 확인 없이 즉시 실행된다(사용자가
    대시보드에서 클릭 한 번으로 바꾸던 저위험 필드, AI 분석 내용은 안 건드림).

    status를 바꾸면 지원 상태 로그에 자동 기록되고, 웹 UI와 동일한 규칙으로 '지원'이면 자동
    핀 고정, '미지원'/'탈락'/'보류'/'지원마감'이면 자동 핀 해제된다. pinned를 명시적으로 같이
    주면 그 값이 자동 규칙보다 우선한다."""
    record = storage.read_company(slug)
    if not record:
        raise ValueError(f"회사를 찾을 수 없습니다: {slug}")

    fm = record.frontmatter
    body = record.body
    changed = False

    if status is not None and status != fm.status:
        fm = fm.model_copy(update={"status": status})
        body = companies.append_status_log(body, status)
        changed = True
        if pinned is None:
            if status in companies.AUTO_PIN_ON:
                pinned = True
            elif status in companies.AUTO_UNPIN_ON:
                pinned = False

    if pinned is not None and pinned != fm.pinned:
        fm = fm.model_copy(update={"pinned": pinned})
        changed = True

    if not changed:
        return record.model_dump()

    # model_copy()는 재검증을 안 해서 잘못된 값도 그대로 통과한다 — 파일로 저장되고 나면
    # read_company()/list_companies()가 생성자 검증에서 실패해 해당 회사를 못 읽게 된다
    # (2026-08-25 Codex 리뷰 finding). MCP 스키마의 Literal 타입으로 대부분 걸러지지만,
    # 저장 직전에 한 번 더 검증해 어떤 경로로든 잘못된 값이 파일에 쓰이지 않도록 한다.
    try:
        fm = CompanyFrontmatter.model_validate(fm.model_dump())
    except ValidationError as e:
        raise ValueError(f"저장할 수 없는 값입니다: {e}")

    updated = storage.write_company(slug, fm, body)
    # RAG의 posting 테이블 스키마·적재 로직 어디에도 status/pinned가 없다(rag/postgres/
    # ingest.py 확인) — toggle_pin()과 동일하게 재색인을 아예 트리거하지 않는다. 예전엔
    # PUT /api/companies/{slug}(tech_stack 등 RAG 대상 필드도 바꾸는 핸들러)의 트리거 호출을
    # 그대로 복사해왔는데, 그쪽과 달리 이 도구는 RAG 무관 필드만 다뤄서 불필요했다
    # (2026-08-25 Codex 리뷰 finding).
    return updated.model_dump()


def _chunk_source(conn, chunk_id: int) -> dict:
    """청크가 어느 공고/프로필에서 왔는지 조회 — 근거 출처를 명확히 표시해 서로 다른
    회사·프로젝트의 근거가 뒤섞이지 않도록 한다(RAG 에이전트 근거 섞임 버그, v1.5.4와
    같은 이유)."""
    row = conn.execute(
        "SELECT source_type, source_id FROM document_chunk WHERE id = %s", (chunk_id,)
    ).fetchone()
    if not row:
        return {"type": "unknown"}
    source_type, source_id = row
    if source_type == "posting_raw":
        posting = conn.execute(
            "SELECT company_name, job_title FROM posting WHERE slug = %s", (source_id,)
        ).fetchone()
        if posting:
            return {"type": "posting", "slug": source_id, "company_name": posting[0], "job_title": posting[1]}
        return {"type": "posting", "slug": source_id}
    if source_type == "candidate_profile":
        return {"type": "profile"}
    return {"type": source_type, "id": source_id}


@mcp.tool()
async def search_rag_evidence(question: str, top_k: int = 5) -> dict:
    """자연어 질문과 관련된 근거 청크(공고·프로필 발췌문)를 벡터 검색으로 찾는다. LLM 판정
    없이 순수 검색만 하므로, 최종 판단·답변 생성은 호출한 클라이언트가 맡는다. 각 결과에
    출처(어느 회사 공고인지/프로필인지)를 항상 포함 — 근거를 인용할 때 출처를 섞지 않도록
    호출부에서 이 필드를 반드시 참고할 것."""
    if not settings.rag_postgres_host:
        return {"enabled": False, "evidence": []}
    provider_name = resolve_rag_embedding_provider()
    conn = await asyncio.to_thread(get_connection)
    embed_provider = None
    try:
        embed_provider = await asyncio.to_thread(
            GoogleEmbeddingProvider if provider_name == "google" else LocalEmbeddingProvider
        )
        rows = await asyncio.to_thread(search_chunks, conn, embed_provider, question, None, top_k)
        evidence = []
        for score, chunk_id, text in rows:
            source = await asyncio.to_thread(_chunk_source, conn, chunk_id)
            evidence.append({"score": round(score, 4), "source": source, "excerpt": text})
        return {"enabled": True, "provider": provider_name, "evidence": evidence}
    finally:
        # 두 자원을 독립적으로 정리한다(routers/rag.py의 기존 cleanup과 동일 패턴) —
        # embed_provider.close()(SSH 터널 종료 대기라 실패할 수 있음)가 예외를 던지면
        # 그 다음 줄인 conn.close()가 실행되지 않아 PostgreSQL 연결이 회수되지 않는다
        # (2026-08-25 Codex 리뷰 finding).
        close = getattr(embed_provider, "close", None)
        if close:
            try:
                await asyncio.to_thread(close)
            except Exception:
                pass
        await asyncio.to_thread(conn.close)


@mcp.tool()
async def prepare_company_import(url: str | None = None, raw_text: str | None = None) -> dict:
    """회사 공고 등록을 위한 분석 패킷을 준비한다. LLM을 호출하지 않으므로 Job FitCheck
    쪽 API 비용이 들지 않는다 — url 또는 raw_text 중 하나를 주면 원문을 모으고, 기존
    분석 파이프라인과 동일한 프롬프트 3종(구조화 추출 → 마크다운 본문 생성 → 적합도 평가)을
    채우지 않은 템플릿 그대로 반환한다.

    호출한 클라이언트(자신의 세션 모델)가 직접: 1) extract_company로 구조화 JSON을 뽑고,
    2) 그 결과로 generate_body의 {company_json} 자리를 채워 마크다운 본문(섹션 1~3)을
    만들고, 3) evaluate_fit이 available이면 적합도를 평가해 본문에 "## 4. 적합도 리포트"
    섹션을 이어붙인 뒤, create_company를 호출해 저장해야 한다."""
    if not url and not raw_text:
        raise ValueError("url 또는 raw_text 중 하나가 필요합니다.")

    if url:
        duplicate = next(
            (c for c in storage.list_companies() if c.frontmatter.source_url == url), None
        )
        if duplicate:
            return {
                "duplicate": True,
                "slug": duplicate.slug,
                "name": duplicate.frontmatter.display_name or duplicate.frontmatter.company_name,
            }
        text = await scraper.fetch_url_text(url)
    else:
        text = raw_text

    safe_text = prompts.escape_tag_chars(text)
    has_profile = storage.profile_exists()
    profile_text = None
    if has_profile:
        profile_text = prompts.escape_tag_chars(
            storage.strip_scoring_excluded(storage.read_profile_text() or "")
        )
    eval_criteria = storage.read_eval_criteria().strip()
    custom_criteria = (
        f"\n\n## 추가 평가 기준 (사용자 지정)\n{eval_criteria}{prompts.CUSTOM_CRITERIA_BOUNDARY_NOTICE}"
        if eval_criteria else ""
    )

    return {
        "duplicate": False,
        "raw_text": text,
        "raw_text_escaped": safe_text,
        "source_url": url,
        "extract_company": {
            "system": prompts.EXTRACT_COMPANY_SYSTEM,
            "user": prompts.EXTRACT_COMPANY_USER_TEMPLATE.format(raw_text=safe_text),
            "output_schema": prompts.EXTRACT_COMPANY_TOOL_SCHEMA,
        },
        "generate_body": {
            "system": prompts.GENERATE_BODY_SYSTEM,
            "user_template": prompts.GENERATE_BODY_USER_TEMPLATE,
            "note": "user_template엔 {company_json}·{raw_text} 자리가 아직 안 채워져 있음 — "
            "company_json은 extract_company 결과를 JSON 문자열로, raw_text는 "
            "raw_text_escaped 앞 4000자를 넣는 게 기존 파이프라인 관례.",
        },
        "evaluate_fit": {
            "available": has_profile,
            "system": prompts.EVALUATE_FIT_SYSTEM,
            "user_template": prompts.EVALUATE_FIT_USER_TEMPLATE,
            "output_schema": prompts.EVALUATE_FIT_TOOL_SCHEMA,
            "candidate_profile": profile_text,
            "custom_criteria": custom_criteria,
            "note": "user_template엔 {candidate_profile}·{company_json}·{raw_text}·"
            "{custom_criteria} 자리가 아직 안 채워져 있음 — candidate_profile·custom_criteria는 "
            "위 값을 그대로, company_json은 extract_company 결과, raw_text는 "
            "raw_text_escaped 앞 4000자를 넣는 게 기존 파이프라인 관례. available이 False면 "
            "프로필이 없어 적합도 평가를 생략해야 한다(웹 파이프라인과 동일).",
        },
    }


# create_company가 company_data에서 받아들일 필드 — extract_company/evaluate_fit 결과만
# 허용하고, status/pinned/created_at 같은 사용자 관리·서버 관리 필드는 스키마에 없으므로
# 자동으로 제외된다(수작업 나열이 아니라 기존 도구 스키마에서 그대로 뽑음 — 2026-08-25
# Codex 리뷰 finding, 필드가 늘어도 스키마만 따라가면 됨).
_CREATE_COMPANY_ALLOWED_FIELDS = set(prompts.EXTRACT_COMPANY_TOOL_SCHEMA["properties"]) | (
    set(prompts.EVALUATE_FIT_TOOL_SCHEMA["properties"]) - {"fit_report_body"}
)


@mcp.tool()
async def create_company(
    company_data: dict,
    body: str,
    raw_text: str,
    source_url: str | None = None,
) -> dict:
    """prepare_company_import로 받은 분석 패킷을 클라이언트가 직접 처리한 결과를 저장한다.
    확인 없이 즉시 실행된다(기존 데이터를 덮어쓰지 않는 순수 추가).

    company_data: extract_company 결과 JSON에 evaluate_fit 결과 필드(fit_score, fit_label,
    strengths, gaps, salary_check, stability_check, location_check 등, fit_report_body는
    제외 — 그건 body에 포함)를 합친 것. 이 필드 집합 밖의 키(status/pinned/created_at 등
    사용자·서버 관리 필드)는 무시된다. body: 마크다운 본문(생성한 본문 + 적합도 리포트
    섹션까지 이미 합쳐진 상태) — 지원 상태 로그 섹션은 이 도구가 자동으로 추가한다."""
    # source_url 인자와 company_data["source_url"](raw_text만 줘도 extract_company가 원문에서
    # 찾아 채울 수 있음)가 서로 다른 값을 가질 수 있어, 중복검사·source_type·최종 저장 전부
    # 이 값 하나만 기준으로 통일한다(둘이 따로 놀아서 생긴 버그, 2026-08-25 Codex 리뷰 finding).
    effective_source_url = source_url or company_data.get("source_url")

    if effective_source_url:
        duplicate = next(
            (c for c in storage.list_companies() if c.frontmatter.source_url == effective_source_url), None
        )
        if duplicate:
            raise ValueError(
                f"이미 등록된 URL입니다: {duplicate.slug} "
                f"({duplicate.frontmatter.display_name or duplicate.frontmatter.company_name})"
            )

    projected = {k: v for k, v in company_data.items() if k in _CREATE_COMPANY_ALLOWED_FIELDS}
    if not (projected.get("company_name") or "").strip() or not (projected.get("job_title") or "").strip():
        raise ValueError("company_data에는 비어 있지 않은 company_name과 job_title이 필요합니다.")

    fm_data = {
        **projected,
        "source_url": effective_source_url,
        "source_type": "url" if effective_source_url else "text_paste",
        "llm_provider": "mcp",
    }
    fm = CompanyFrontmatter(**fm_data)

    body = companies.append_status_log(body, "분석 완료")
    slug = storage.make_slug(fm.company_name, fm.job_title or "")
    storage.write_raw_text(slug, raw_text)
    record = storage.write_company(slug, fm, body)
    trigger_reindex_background()
    return record.model_dump()
