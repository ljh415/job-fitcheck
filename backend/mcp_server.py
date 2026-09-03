"""Job FitCheck MCP 서버 — Codex/Claude 같은 외부 AI가 회사·프로필·RAG 기능을 도구로 쓸 수
있게 한다. 전송은 HTTP/SSE(streamable HTTP), 인증은 `backend/main.py`의 기존 JWT Bearer
미들웨어를 그대로 재사용한다(`/api/mcp` 아래 마운트해서 별도 인증 코드를 만들지 않음).

도구는 기존 REST 핸들러·내부 함수를 얇게 감싸는 정도로만 구현한다 — MCP를 위해 앱 내부
함수를 새로 쪼개지 않는다(docs/planning/mcp_plan_notes.md "기존 회사 분석 로직 재사용 방향").

설계 배경: docs/planning/mcp_plan_notes.md(로컬 전용), 진행 기록: docs/mcp-server/(로컬 전용).
"""
import asyncio
from typing import Literal

import httpx
import prompts
import storage
from config import resolve_rag_embedding_provider, settings
from fastapi import HTTPException
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.server import MCPServer
from models import CompanyFrontmatter
from notify import send_notification
from pydantic import ValidationError
from rag.embed.google import GoogleEmbeddingProvider
from rag.embed.local import LocalEmbeddingProvider
from rag.postgres.db import get_connection
from rag.postgres.query_router import list_postings
from rag.postgres.retrieval import search_chunks
from rag.reindex_service import trigger_background as trigger_reindex_background
from routers import companies, profile, rag
from services import fit_normalization, scraper

mcp = MCPServer(name="job-fitcheck")

# list_companies 응답에서 제외할 필드 — 문장 단위 상세 텍스트라 회사 수가 늘면 MCP 클라이언트
# 라이브러리(httpx-sse)의 SSE 이벤트 크기 제한(기본 1MB, streamable_http_client()가 설정을
# 노출하지 않아 서버 쪽에서 줄이는 것 외엔 대응 방법이 없음)을 넘길 수 있다. 상세는 get_company로.
_LIST_COMPANIES_EXCLUDED_FIELDS = {
    "strengths", "gaps", "key_responsibilities",
    "required_skills", "preferred_skills", "benefits", "hiring_process",
    # 적합도 평가 구조 개편(docs/fit-eval-structural-redesign/PLAN.md) 1단계 중간
    # 판정 결과 — 디버깅·감사용 상세 배열이라 목록 크기만 키운다(4차 리뷰 반영,
    # 116개 기준 1MB SSE 제한 재초과 재현됨). 상세는 get_company로.
    "item_judgments", "decision_factors",
}

# 웹 경로(REST)는 이 분석 하나만을 위한 격리된 단발성 API 호출이라 애초에 문제가 안 되지만,
# MCP는 이미 진행 중인 대화(다른 주제·톤·이전 논의)의 연장선에서 호출된다 — 그 맥락이 평가에
# 섞여 웹과 다른 결과가 나올 수 있다(docs/planning/mcp_analysis_consistency.md 참고). REST와
# 공유하는 prompts.py의 SYSTEM 상수엔 안 넣고, MCP 전용으로 여기서만 덧붙인다 — REST엔 애초에
# 해당 없는 지시라 원본 프롬프트를 불필요하게 늘릴 이유가 없다.
_MCP_ISOLATION_NOTICE = """
[격리된 작업 지시] 이 요청은 지금 진행 중인 대화의 다른 주제·톤·이전 논의와 무관한 단일 목적의 독립 작업입니다. 이 대화에서 오간 다른 내용(다른 회사에 대한 평가, 잡담, 사용자의 다른 요청 등)을 이 판단의 근거나 참고 자료로 쓰지 마세요. 오직 이 요청에서 함께 제공된 데이터(채용공고 원문·구조화된 회사 정보·후보자 프로필·평가 기준 등)만을 근거로 판단하세요."""


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
    """등록된 회사 목록을 조회한다. 인자를 안 주면 전체 목록을 반환한다. 강점/갭/주요업무/
    필수·우대요건/복지/채용절차처럼 문장 단위로 풀어쓴 상세 필드는 목록에서 빠진다(회사 수가
    늘면 MCP 클라이언트의 SSE 이벤트 크기 제한을 넘어설 수 있음) — 특정 회사의 전체 내용은
    get_company로 조회한다.

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
        result.append(meta.model_dump(exclude={"frontmatter": _LIST_COMPANIES_EXCLUDED_FIELDS}))
    return result


@mcp.tool()
async def get_company(slug: str) -> dict:
    """특정 회사의 공고 원문, 분석 결과(frontmatter), 상태 로그(본문)를 조회한다."""
    record = storage.read_company(slug)
    if not record:
        raise ToolError(f"회사를 찾을 수 없습니다: {slug}")
    return record.model_dump()


@mcp.tool()
async def compare_companies(slugs: list[str]) -> list[dict]:
    """여러 회사의 공고와 적합도 정보를 나란히 비교한다(최대 5개)."""
    try:
        records = await companies.compare_companies(slugs)
    except HTTPException as e:
        raise ToolError(str(e.detail))
    return [r.model_dump() for r in records]


@mcp.tool()
async def get_application_timeline() -> list[dict]:
    """지원한 회사들의 상태 변화 이력(지원→서류통과→인터뷰 등)을 시간순으로 조회한다."""
    return await companies.get_companies_timeline()


@mcp.tool()
async def get_profile() -> dict:
    """후보자 프로필(이력서 기반 구조화 정보 + 본문)을 조회한다."""
    try:
        record = await profile.get_profile()
    except HTTPException as e:
        raise ToolError(str(e.detail))
    return record.model_dump()


@mcp.tool()
async def list_matching_postings(skill: str = "", job_title: str = "", limit: int = 50) -> dict:
    """기술 스택·직무명으로 공고를 검색한다(LLM 미사용, 순수 DB 조회). skill과 job_title을
    같이 주면 둘 다 만족하는 공고만 반환한다."""
    if limit <= 0:
        raise ToolError("limit은 1 이상이어야 합니다.")
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
        raise ToolError(f"회사를 찾을 수 없습니다: {slug}")

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

    # model_copy()는 재검증을 안 하므로 저장 전에 한 번 더 검증한다(잘못된 값이 파일에
    # 쓰이면 read_company()/list_companies()가 이후 그 회사를 못 읽게 됨).
    try:
        fm = CompanyFrontmatter.model_validate(fm.model_dump())
    except ValidationError as e:
        raise ToolError(f"저장할 수 없는 값입니다: {e}")

    updated = storage.write_company(slug, fm, body)
    # status/pinned는 RAG posting 스키마에 없는 필드라 재색인 불필요(toggle_pin()과 동일).
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
    if len(question) > 2_000:
        raise ToolError("question은 2,000자 이하여야 합니다.")
    if top_k <= 0:
        raise ToolError("top_k는 1 이상이어야 합니다.")
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
        # provider.close() 실패가 conn.close()를 막지 않도록 독립적으로 정리한다(routers/rag.py와 동일 패턴).
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
        raise ToolError("url 또는 raw_text 중 하나가 필요합니다.")
    if raw_text and len(raw_text) > 100_000:
        raise ToolError("raw_text는 100,000자 이하여야 합니다.")

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
        try:
            text = await scraper.fetch_url_text(url)
        except ValueError as e:
            raise ToolError(str(e))
        except httpx.TimeoutException:
            raise ToolError("사이트 응답 시간이 초과됐습니다 (20초). 사이트가 느리거나 접근이 차단됐을 수 있습니다.")
        except httpx.HTTPStatusError as e:
            raise ToolError(f"사이트가 {e.response.status_code} 오류를 반환했습니다. URL을 확인해주세요.")
        except Exception:
            raise ToolError("URL 접근 실패: 네트워크 연결 오류. URL을 다시 확인해주세요.")
    else:
        text = raw_text

    safe_text = prompts.escape_tag_chars(text)
    has_profile = storage.profile_exists()
    profile_text = None
    profile_version_id = None
    if has_profile:
        profile_text = prompts.escape_tag_chars(
            storage.strip_scoring_excluded(storage.read_profile_text() or "")
        )
        # 이 프로필을 실제로 읽은 시점에 스냅샷 id를 고정한다 — create_company 시점에
        # 다시 조회하면, 클라이언트가 평가하는 동안 프로필이 갱신된 경우 실제 평가에
        # 쓰이지 않은 새 버전과 잘못 연결된다.
        profile_version_id = companies.resolve_profile_version_id_for_eval()
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
            "system": prompts.EXTRACT_COMPANY_SYSTEM + _MCP_ISOLATION_NOTICE,
            "user": prompts.EXTRACT_COMPANY_USER_TEMPLATE.format(raw_text=safe_text),
            "output_schema": prompts.EXTRACT_COMPANY_TOOL_SCHEMA,
        },
        "generate_body": {
            "system": prompts.GENERATE_BODY_SYSTEM + _MCP_ISOLATION_NOTICE,
            "user_template": prompts.GENERATE_BODY_USER_TEMPLATE,
            "note": "user_template엔 {company_json}·{raw_text} 자리가 아직 안 채워져 있음 — "
            "company_json은 extract_company 결과를 JSON 문자열로, raw_text는 "
            "raw_text_escaped 앞 4000자를 넣는 게 기존 파이프라인 관례.",
        },
        "evaluate_fit_judge": {
            "available": has_profile,
            "system": prompts.EVALUATE_FIT_JUDGE_SYSTEM + _MCP_ISOLATION_NOTICE,
            "user_template": prompts.EVALUATE_FIT_JUDGE_USER_TEMPLATE,
            "output_schema": prompts.EVALUATE_FIT_JUDGE_TOOL_SCHEMA,
            "candidate_profile": profile_text,
            "profile_version_id": profile_version_id,
            "custom_criteria": custom_criteria,
            "note": "REST와 동일한 1단계 판정 전용 스키마 — fit_report_body(산문)는 여기 없음, "
            "보고서는 별도 2단계(prepare_fit_report 도구)에서 작성한다. user_template엔 "
            "{candidate_profile}·{company_json}·{raw_text}·{item_list}·{tool_name}·"
            "{custom_criteria} 자리가 아직 안 채워져 있음 — candidate_profile·custom_criteria는 "
            "위 값을 그대로, company_json은 extract_company 결과, raw_text는 "
            "raw_text_escaped 앞 4000자, tool_name은 'evaluate_fit_judge'를 넣는 게 기존 "
            "파이프라인 관례. item_list는 extract_company 결과의 required_skills/"
            "preferred_skills/key_responsibilities 각 배열을 0부터 순서대로 "
            "'required:0', 'preferred:0', 'responsibility:0' 형식 id로 붙여 "
            "'- required:0: <항목 원문>' 한 줄씩 나열한 것(서버가 나중에 판정 결과를 "
            "정규화할 때 이 id로 원본 항목과 다시 짝짓는다 — 순서·prefix가 어긋나면 안 됨). "
            "available이 False면 프로필이 없어 적합도 평가를 생략해야 한다(웹 파이프라인과 "
            "동일). profile_version_id는 이 프로필을 평가에 실제로 사용했다면 그대로 "
            "create_company에 다시 전달할 것 — 이력에 정확한 프로필 버전을 연결하는 데 쓰인다.",
        },
    }


@mcp.tool()
async def prepare_fit_report(judge_result: dict, company_data: dict) -> dict:
    """evaluate_fit_judge 판정 결과를 받아 REST와 동일한 규칙(fit_normalization)으로
    검증·보정·확정한 뒤, 2단계 보고서(종합 의견) 작성에 필요한 프롬프트를 채워 반환한다.
    LLM을 호출하지 않으므로 비용이 들지 않는다 — evaluate_fit_judge 도구 호출 직후,
    보고서를 쓰기 전에 반드시 거쳐야 한다(판정을 먼저 확정한 뒤에만 보고서를 쓰게 해서,
    판정과 서술이 서로 다른 내용을 말하는 걸 막는 목적).

    judge_result: evaluate_fit_judge 스키마로 호출한 결과 JSON 그대로(fit_score/
    item_judgments/decision_factors). company_data: extract_company 결과. 둘 다
    court-of-record 취급 — 형식이 깨지거나 항목이 빠져도 예외 없이 안전한 기본값+
    evaluation_incomplete로 보정된다(재요청 불필요).

    반환값의 report.user는 EVALUATE_FIT_REPORT_USER_TEMPLATE을 이미 채운 상태 —
    그대로 산문(종합 의견)을 완성해서(도구 호출 아닌 일반 텍스트 응답) create_company의
    report_prose에 전달하면 된다. 반환값의 item_judgments/decision_factors/gaps/
    strengths/fit_score는 이 도구가 확정한 canonical 값이니, 그와 다른 내용을 보고서에
    쓰면 안 된다. 표·비기술 요인 요약 줄은 create_company가 저장 시점에 직접 렌더링하므로
    여기서는 반환하지 않는다(중복 방지)."""
    normalized = fit_normalization.normalize_and_render(judge_result, company_data)
    return {
        "fit_score": normalized["fit_score"],
        "fit_label": normalized["fit_label"],
        "gaps": normalized["gaps"],
        "strengths": normalized["strengths"],
        "evaluation_incomplete": normalized["evaluation_incomplete"],
        "item_judgments": normalized["item_judgments"],
        "decision_factors": normalized["decision_factors"],
        "report": {
            "system": prompts.EVALUATE_FIT_REPORT_SYSTEM + _MCP_ISOLATION_NOTICE,
            "user": normalized["report_user"],
            "note": "위 system+user로 일반 텍스트 완성(도구 호출 아님)을 요청하면 산문이 나온다. "
            "그 결과를 그대로(다듬지 말고) create_company의 report_prose 인자로 전달할 것.",
        },
    }


# extract_company 스키마 필드만 허용 — 적합도 평가 필드(fit_score/gaps/decision_factors 등)는
# judge_result에서만 받아 서버가 직접 재정규화해 채운다(company_data로 직접 제출 불가 —
# 2026-09-03 MCP 구조화 계약 전환, 클라이언트가 판정을 조작해 제출하는 경로를 원천 차단).
# status/pinned/created_at 등도 여기 없어 자동 제외된다.
_CREATE_COMPANY_ALLOWED_FIELDS = set(prompts.EXTRACT_COMPANY_TOOL_SCHEMA["properties"])


@mcp.tool()
async def create_company(
    company_data: dict,
    base_body: str,
    raw_text: str,
    judge_result: dict | None = None,
    report_prose: str | None = None,
    source_url: str | None = None,
    profile_version_id: int | None = None,
) -> dict:
    """prepare_company_import로 받은 분석 패킷을 클라이언트가 직접 처리한 결과를 저장한다.
    확인 없이 즉시 실행된다(기존 데이터를 덮어쓰지 않는 순수 추가).

    company_data: extract_company 결과 JSON(required_skills/preferred_skills/
    key_responsibilities 등 판정에 쓰인 원본 배열 포함 — normalize_and_render가 다시
    쓴다). fit_score/gaps/decision_factors 등 적합도 관련 필드를 여기 넣어도 무시된다
    (judge_result만 신뢰). base_body: 마크다운 본문 중 1~3절(회사 정보 등)만 —
    적합도 리포트(4~5절)는 이 도구가 서버에서 직접 조립하므로 포함하면 안 된다.

    judge_result: evaluate_fit_judge 도구 호출 결과(또는 prepare_fit_report 반환값)를
    그대로 — 클라이언트를 신뢰하지 않고 저장 직전 서버가 같은 정규화를 한 번 더
    실행한다(방어적 재정규화). 프로필이 없어 애초에 평가를 안 했다면 None(또는 생략) —
    이 경우 적합도 관련 필드는 전부 비어있는 상태(미평가)로 저장된다. report_prose:
    prepare_fit_report가 준 프롬프트로 작성한 종합 의견 산문 — judge_result가 있으면
    필수. 재정규화 결과 evaluation_incomplete=true면(판정 항목이 채워지지 않았거나
    형식이 깨짐) 저장을 거부하고 어느 항목이 문제인지 구체적으로 반환하니, 그 항목만
    다시 판정해 evaluate_fit_judge부터 재시도할 것.

    profile_version_id: prepare_company_import의 evaluate_fit_judge.profile_version_id를
    평가에 실제로 썼다면 그대로 전달 — 여기서 다시 조회하지 않는 이유는 조회 시점(저장
    직전)이 실제 평가 시점과 다를 수 있어(그 사이 프로필이 갱신되면) 엉뚱한 버전과
    연결될 수 있기 때문. 안 주면 이전 버전 불명(None)으로 기록된다."""
    # company_data["source_url"](raw_text만 줘도 extract_company가 채울 수 있음)도 함께 고려해
    # 중복검사·source_type·최종 저장을 전부 이 값 하나로 통일한다.
    effective_source_url = source_url or company_data.get("source_url")

    if effective_source_url:
        duplicate = next(
            (c for c in storage.list_companies() if c.frontmatter.source_url == effective_source_url), None
        )
        if duplicate:
            raise ToolError(
                f"이미 등록된 URL입니다: {duplicate.slug} "
                f"({duplicate.frontmatter.display_name or duplicate.frontmatter.company_name})"
            )

    projected = {k: v for k, v in company_data.items() if k in _CREATE_COMPANY_ALLOWED_FIELDS}
    if not (projected.get("company_name") or "").strip() or not (projected.get("job_title") or "").strip():
        raise ToolError("company_data에는 비어 있지 않은 company_name과 job_title이 필요합니다.")

    fit_fields: dict = {}
    fit_report_section = ""
    if judge_result:
        if not (report_prose or "").strip():
            raise ToolError("judge_result가 있으면 report_prose(prepare_fit_report로 작성한 종합 의견)도 필요합니다.")
        # 클라이언트가 prepare_fit_report를 실제로 거쳤는지 서버는 신뢰할 수 없다 —
        # 저장 직전 같은 함수로 다시 정규화한다(멱등, PLAN.md §6.2/§5.3).
        normalized = fit_normalization.normalize_and_render(judge_result, company_data)
        if normalized["evaluation_incomplete"]:
            fallback_items = [
                it["id"] for it in normalized["item_judgments"] if it.get("filled_by") == "code_fallback"
            ]
            fallback_factors = [
                k for k, v in normalized["decision_factors"].items()
                if v.get("status") == fit_normalization._FALLBACK_STATUS
            ]
            raise ToolError(
                "판정이 불완전해 저장을 거부합니다 — evaluate_fit_judge를 다시 호출해 "
                f"아래 항목을 재제출하세요. 판정 누락/무효 id: {fallback_items or '없음'}, "
                f"비기술 요인 판정 실패: {fallback_factors or '없음'}"
            )
        fit_fields = {
            "fit_score": normalized["fit_score"],
            "fit_label": normalized["fit_label"],
            "strengths": normalized["strengths"],
            "gaps": normalized["gaps"],
            "item_judgments": normalized["item_judgments"],
            "decision_factors": normalized["decision_factors"],
            "evaluation_incomplete": normalized["evaluation_incomplete"],
            "salary_check": normalized["salary_check"],
            "stability_check": normalized["stability_check"],
            "location_check": normalized["location_check"],
        }
        fit_report_section = f"{normalized['tables']}\n\n{report_prose.strip()}\n\n{normalized['factors_summary']}"

    fm_data = {
        **projected,
        **fit_fields,
        "source_url": effective_source_url,
        "source_type": "url" if effective_source_url else "text_paste",
        "llm_provider": "mcp",
    }
    fm = CompanyFrontmatter(**fm_data)

    final_body = f"{base_body}\n\n{fit_report_section}" if fit_report_section else base_body
    final_body = companies.append_status_log(final_body, "분석 완료")
    slug = storage.make_slug(fm.company_name, fm.job_title or "")
    storage.write_raw_text(slug, raw_text)
    record = storage.write_company(slug, fm, final_body)
    companies.snapshot_fit_history(slug, fm.fit_score, fm.fit_label, profile_version_id)
    await send_notification(companies.build_fit_notification_materials(fm))
    trigger_reindex_background()
    result = record.model_dump()
    result["next_step"] = (
        f"저장이 완료됐습니다. 사용자에게 회사명({fm.display_name or fm.company_name})·직무({fm.job_title}), "
        f"적합도 점수({fm.fit_score}점, {fm.fit_label}), 핵심 근거 2~3가지를 요약해서 보여주세요."
        if judge_result else
        f"저장이 완료됐습니다. 사용자에게 회사명({fm.display_name or fm.company_name})·직무({fm.job_title}) 등"
        " 주요 정보를 요약해서 보여주세요."
    )
    return result
