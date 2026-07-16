"""
FastAPI 앱 진입점.

모든 API 엔드포인트를 정의하며, frontend/ 디렉토리를 정적 파일로 서빙한다.
회사 추가 흐름(_process_company)은 다음 순서로 진행된다:
  1. Lightweight — 공고 텍스트 구조화 추출
  2. 잡플래닛 평점 수집 (네이버 검색 스크래핑, 실패 시 무시)
  3. Lightweight — 마크다운 본문 생성
  4. High — 후보자 프로필 대비 적합도 평가 (프로필이 없으면 생략)
  5. 마크다운 파일 저장
"""
import asyncio
import base64
import csv
import io
import json
import logging
import re
import zipfile
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import httpx
import jwt

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile, File, Form
from pydantic import BaseModel
from fastapi.responses import JSONResponse, StreamingResponse

import storage
import scraper
import pdf_parser
import prompts
import usage_tracker
from jobplanet import fetch_jobplanet_score
from llm.base import LLMAPIError
from pdf_parser import PDFExtractError
from notify import send_notification
from config import (
    ensure_dirs,
    get_active_provider,
    get_model_override,
    get_notify_pref,
    get_weekly_summary_schedule,
    set_active_provider,
    set_model_override,
    set_notify_pref,
    set_weekly_summary_schedule,
    settings,
)
from llm.router import (
    capture_snapshot,
    high_from_snapshot,
    high_provider,
    light_from_snapshot,
    light_provider,
)


def _evaluate_fit_system(provider: str) -> str:
    """주어진 provider에 맞는 EVALUATE_FIT_SYSTEM 반환."""
    if provider == "openai":
        return prompts.EVALUATE_FIT_SYSTEM_OPENAI
    if provider == "gemini":
        return prompts.EVALUATE_FIT_SYSTEM_GEMINI
    return prompts.EVALUATE_FIT_SYSTEM
from models import (
    CandidateProfile,
    CompanyFrontmatter,
    CompanyMeta,
    CompanyRecord,
    CompanyUpdateRequest,
    FromTextRequest,
    FromUrlRequest,
    LoginRequest,
    ManualCompanyRequest,
    MultiQARequest,
    ProfileUpdateRequest,
    QAMessage,
    QARequest,
    SettingsResponse,
    SettingsUpdateRequest,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)



@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    task = asyncio.create_task(_weekly_summary_loop())
    yield
    task.cancel()


app = FastAPI(title="Job FitCheck", version="0.1.0", lifespan=lifespan)

# ── 이미지 유틸 ───────────────────────────────────────────────────────────────

_MAX_IMAGE_SIDE = 1568  # Anthropic 권장 최대 크기 (이 이상은 서버에서 강제 리사이즈되며 토큰 낭비)
_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# 업로드 개수/크기 상한 — 정상 사용 범위를 정밀하게 맞춘 값이 아니라,
# 정상 사용에서는 절대 걸리지 않을 만큼 넉넉하되 실수·이상 입력만 걸러내는 안전판.
_MAX_UPLOAD_FILES = 10  # 이력서+포트폴리오 여러 개, 공고 스크린샷 여러 장 정도는 통과
_MAX_PDF_BYTES = 30 * 1024 * 1024  # 이미지가 많은 포트폴리오 PDF도 통과하는 수준
_MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 스크린샷/사진 1장 기준 (리사이즈 전 원본)


def _resize_image(data: bytes) -> tuple[bytes, str]:
    """이미지를 최대 1568px로 다운샘플링하고 (bytes, media_type) 반환.
    비용 절감 목적 — Anthropic이 이 크기 이상을 강제 리사이즈하기 전에 미리 처리."""
    from PIL import Image as PilImage

    img = PilImage.open(io.BytesIO(data))
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")

    w, h = img.size
    long_side = max(w, h)
    if long_side > _MAX_IMAGE_SIDE:
        scale = _MAX_IMAGE_SIDE / long_side
        img = img.resize((int(w * scale), int(h * scale)), PilImage.LANCZOS)
        logger.info("이미지 리사이즈: %dx%d → %dx%d", w, h, int(w * scale), int(h * scale))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"

_JWT_ALGORITHM = "HS256"
_JWT_EXPIRE_DAYS = 30

def _make_token() -> str:
    payload = {
        "sub": "user",
        "exp": datetime.now(timezone.utc) + timedelta(days=_JWT_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.app_secret, algorithm=_JWT_ALGORITHM)

def _verify_token(token: str) -> bool:
    try:
        jwt.decode(token, settings.app_secret, algorithms=[_JWT_ALGORITHM])
        return True
    except jwt.PyJWTError:
        return False

# 인증 미들웨어 — APP_SECRET이 설정된 경우에만 적용
_AUTH_SKIP = {"/api/login", "/api/health"}

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not settings.app_secret or request.url.path in _AUTH_SKIP:
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not _verify_token(auth.split(" ", 1)[1]):
        return JSONResponse(status_code=401, content={"detail": "인증이 필요합니다."})
    return await call_next(request)


# ── 인증 ─────────────────────────────────────────────────────────────────────

@app.post("/api/login")
async def login(req: LoginRequest):
    if not settings.app_secret:
        logger.info("로그인 성공 (APP_SECRET 미설정 — 개발 모드)")
        return {"token": "dev"}
    if req.password != settings.app_secret:
        logger.warning("로그인 실패 — 비밀번호 불일치")
        raise HTTPException(status_code=401, detail="비밀번호가 틀렸습니다.")
    logger.info("로그인 성공")
    return {"token": _make_token()}


# ── 헬스 체크 ────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    companies = storage.list_companies()
    return {
        "status": "ok",
        "company_count": len(companies),
        "profile_exists": storage.profile_exists(),
        "provider": get_active_provider(),
    }


@app.get("/api/analysis-in-progress")
async def get_analysis_in_progress():
    """진행 중인 회사 분석 건수 — 페이지 이동 후에도 프론트가 배너로 표시할 수 있도록 제공."""
    return {"count": _in_progress_count}


# ── 설정 ─────────────────────────────────────────────────────────────────────

@app.get("/api/settings", response_model=SettingsResponse)
async def get_settings():
    def _m(key: str, default: str) -> str:
        return get_model_override(key) or default
    return SettingsResponse(
        provider=get_active_provider(),
        claude_high_model=_m("claude_high_model", settings.claude_high_model),
        claude_light_model=_m("claude_light_model", settings.claude_light_model),
        openai_high_model=_m("openai_high_model", settings.openai_high_model),
        openai_light_model=_m("openai_light_model", settings.openai_light_model),
        openai_reasoning_effort=_m("openai_reasoning_effort", settings.openai_reasoning_effort),
        gemini_high_model=_m("gemini_high_model", settings.gemini_high_model),
        gemini_light_model=_m("gemini_light_model", settings.gemini_light_model),
        notify_strengths=get_notify_pref("notify_strengths"),
        notify_gaps=get_notify_pref("notify_gaps"),
        notify_jobplanet_rating=get_notify_pref("notify_jobplanet_rating"),
        notify_employee_count=get_notify_pref("notify_employee_count"),
        notify_weekly_summary=get_notify_pref("notify_weekly_summary"),
        weekly_summary_weekday=get_weekly_summary_schedule()["weekday"],
        weekly_summary_time="{hour:02d}:{minute:02d}".format(**get_weekly_summary_schedule()),
    )


@app.put("/api/settings")
async def update_settings(req: SettingsUpdateRequest):
    if req.provider:
        try:
            prev = get_active_provider()
            set_active_provider(req.provider)
            if prev != req.provider:
                logger.info("provider 변경: %s → %s", prev, req.provider)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    for key in ("claude_high_model", "claude_light_model", "openai_high_model", "openai_light_model", "openai_reasoning_effort", "gemini_high_model", "gemini_light_model"):
        val = getattr(req, key)
        if val:
            prev_model = get_model_override(key)
            set_model_override(key, val)
            if prev_model != val:
                logger.info("모델 변경: %s = %s", key, val)
    for key in ("notify_strengths", "notify_gaps", "notify_jobplanet_rating", "notify_employee_count", "notify_weekly_summary"):
        val = getattr(req, key)
        if val is not None:
            set_notify_pref(key, val)
    if req.weekly_summary_weekday is not None or req.weekly_summary_time is not None:
        schedule = get_weekly_summary_schedule()
        if req.weekly_summary_weekday is not None:
            if not (0 <= req.weekly_summary_weekday <= 6):
                raise HTTPException(status_code=400, detail="요일은 0(월)~6(일) 사이여야 합니다.")
            schedule["weekday"] = req.weekly_summary_weekday
        if req.weekly_summary_time is not None:
            try:
                hour, minute = (int(p) for p in req.weekly_summary_time.split(":", 1))
                assert 0 <= hour <= 23 and 0 <= minute <= 59
            except (ValueError, AssertionError):
                raise HTTPException(status_code=400, detail="시간 형식은 HH:MM 이어야 합니다.")
            schedule["hour"], schedule["minute"] = hour, minute
        set_weekly_summary_schedule(schedule["weekday"], schedule["hour"], schedule["minute"])
    return {"status": "ok", "provider": get_active_provider()}


# ── 모델 목록 ─────────────────────────────────────────────────────────────────

@app.get("/api/models")
async def list_models(provider: str = "claude"):
    try:
        if provider == "claude":
            async with httpx.AsyncClient(timeout=15) as client:
                res = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": settings.anthropic_api_key,
                        "anthropic-version": "2023-06-01",
                    },
                    params={"limit": 100},
                )
                res.raise_for_status()
                data = res.json().get("data", [])
                model_ids = sorted([m["id"] for m in data], reverse=True)
        elif provider == "openai":
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=settings.openai_api_key)
            page = await client.models.list()
            model_ids = sorted(
                [m.id for m in page.data if m.id.startswith("gpt-") or m.id.startswith("o1") or m.id.startswith("o3")],
                reverse=True,
            )
        elif provider == "gemini":
            async with httpx.AsyncClient(timeout=15) as client:
                res = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": settings.google_api_key, "pageSize": 50},
                )
                res.raise_for_status()
                data = res.json().get("models", [])
                model_ids = sorted(
                    [
                        m["name"].replace("models/", "")
                        for m in data
                        if "gemini" in m.get("name", "")
                        and "generateContent" in m.get("supportedGenerationMethods", [])
                        and "tts" not in m.get("name", "")
                        and "embedding" not in m.get("name", "")
                    ],
                    reverse=True,
                )
        else:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 provider: {provider}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"모델 목록 조회 실패: {e}")
    return {"provider": provider, "models": model_ids}


# ── 평가 기준 ─────────────────────────────────────────────────────────────────

@app.get("/api/eval-criteria")
async def get_eval_criteria():
    return {"text": storage.read_eval_criteria()}


@app.get("/api/usage")
async def get_usage():
    return usage_tracker.read_usage()


@app.put("/api/eval-criteria")
async def update_eval_criteria(req: dict):
    text = req.get("text", "")
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text 필드는 문자열이어야 합니다.")
    storage.write_eval_criteria(text)
    logger.info("평가 기준 업데이트: %d자", len(text))
    return {"status": "ok"}


# ── 후보자 프로필 ─────────────────────────────────────────────────────────────

@app.get("/api/profile/status")
async def profile_status():
    return {"exists": storage.profile_exists()}


@app.get("/api/profile")
async def get_profile():
    record = storage.read_profile()
    if not record:
        raise HTTPException(status_code=404, detail="프로필이 없습니다. PDF를 업로드해주세요.")
    return record


def _build_export_zip(buf: io.BytesIO, include_pdf: bool = False, include_log: bool = False) -> None:
    """지정된 옵션에 따라 데이터 파일을 buf에 ZIP으로 압축한다."""
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(settings.companies_dir.glob("*.md")):
            zf.write(path, f"companies/{path.name}")
        for path in sorted(settings.companies_dir.glob("*.raw.txt")):
            zf.write(path, f"companies/{path.name}")
        profile = settings.candidate_profile_path
        if profile.exists():
            zf.write(profile, profile.name)
        criteria = settings.data_dir / "eval_criteria.md"
        if criteria.exists():
            zf.write(criteria, criteria.name)
        if include_pdf:
            for path in sorted(settings.uploads_dir.glob("*.pdf")):
                zf.write(path, f"uploads/{path.name}")
        if include_log:
            log_path = settings.data_dir / "usage_log.jsonl"
            if log_path.exists():
                zf.write(log_path, log_path.name)


def _save_backup_zip() -> None:
    """삭제 직전 자동 백업 — 타임스탬프 파일로 저장, 최근 5개만 유지."""
    backup_dir = settings.data_dir / "backup"
    backup_dir.mkdir(exist_ok=True)
    buf = io.BytesIO()
    _build_export_zip(buf)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    (backup_dir / f"backup_{ts}.zip").write_bytes(buf.getvalue())
    existing = sorted(backup_dir.glob("backup_*.zip"), key=lambda p: p.name)
    for old in existing[:-5]:
        old.unlink()


@app.get("/api/export/zip")
async def export_zip(include_pdf: bool = False, include_log: bool = False):
    """선택 옵션에 따라 데이터를 ZIP 파일로 내보낸다."""
    buf = io.BytesIO()
    _build_export_zip(buf, include_pdf=include_pdf, include_log=include_log)
    buf.seek(0)
    filename = f"job-fitcheck_backup_{date.today().isoformat()}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.get("/api/profile/export")
async def export_profile():
    """분석 완료된 프로필을 마크다운 파일로 다운로드한다."""
    if not storage.profile_exists():
        raise HTTPException(status_code=404, detail="프로필이 없습니다.")
    content = settings.candidate_profile_path.read_text(encoding="utf-8")
    filename = f"profile_{date.today().isoformat()}.md"
    return StreamingResponse(
        iter([content]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.put("/api/profile")
async def update_profile(req: ProfileUpdateRequest):
    record = storage.write_profile(req.frontmatter, req.body)
    logger.info("프로필 수동 업데이트 완료")
    return record


@app.post("/api/profile/upload")
async def upload_profile(files: list[UploadFile] = File(...), extra_note: str = Form(""), max_tokens: int = Form(8192)):
    max_tokens = min(max_tokens, 32768)
    """PDF 업로드 → pdfplumber 추출 → High 티어 LLM → candidate_profile.md 생성."""
    if len(files) > _MAX_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail=f"PDF는 한 번에 최대 {_MAX_UPLOAD_FILES}개까지 업로드할 수 있습니다.")
    uploaded_paths: list[Path] = []
    filenames: list[str] = []

    for file in files:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"PDF 파일만 업로드 가능합니다: {file.filename}")
        if file.size is not None and file.size > _MAX_PDF_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"파일이 너무 큽니다: {file.filename} ({_MAX_PDF_BYTES // (1024*1024)}MB 이하만 가능)",
            )
        safe_name = Path(file.filename).name  # 디렉토리 경로 제거 (../../ 등 차단)
        dest = (settings.uploads_dir / safe_name).resolve()
        if not dest.is_relative_to(settings.uploads_dir.resolve()):
            raise HTTPException(status_code=400, detail=f"유효하지 않은 파일명입니다: {file.filename}")
        content = await file.read()
        dest.write_bytes(content)
        uploaded_paths.append(dest)
        filenames.append(safe_name)

    logger.info("PDF 텍스트 추출 시작: %s", filenames)
    try:
        pdf_text = pdf_parser.extract_texts(uploaded_paths)
    except PDFExtractError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not pdf_text.strip():
        raise HTTPException(
            status_code=422,
            detail="PDF에서 텍스트를 추출할 수 없습니다. 스캔 이미지 PDF이거나 텍스트가 없는 파일일 수 있습니다.",
        )
    logger.info("PDF 텍스트 추출 완료: %d자", len(pdf_text))
    pdf_text = prompts.escape_tag_chars(pdf_text)
    extra_note = prompts.escape_tag_chars(extra_note)

    provider, model = high_provider()
    extra_section = f"<candidate_note>\n{extra_note}\n</candidate_note>\n\n" if extra_note.strip() else ""

    # 1단계: tool use로 구조화 필드 추출 (name, skills, summary 등)
    logger.info("프로필 구조화 추출 시작 (model=%s)", model)
    user_extract = prompts.EXTRACT_PROFILE_USER_TEMPLATE.format(pdf_text=pdf_text, extra_section=extra_section)
    try:
        result = await provider.extract_structured(
            system=prompts.EXTRACT_PROFILE_SYSTEM,
            user=user_extract,
            tool_name=prompts.EXTRACT_PROFILE_TOOL_NAME,
            tool_description=prompts.EXTRACT_PROFILE_TOOL_DESCRIPTION,
            tool_schema=prompts.EXTRACT_PROFILE_TOOL_SCHEMA,
            model=model,
            operation="프로필 추출",
        )
    except LLMAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    logger.info("프로필 구조화 추출 완료: name=%s, tech_skills=%s", result.get("name"), result.get("tech_skills"))

    # 2단계: complete()로 프로필 본문 생성 — tool use는 장문 산문 생성에 부적합
    logger.info("프로필 본문 생성 시작")
    user_body = prompts.GENERATE_PROFILE_BODY_USER_TEMPLATE.format(
        pdf_text=pdf_text,
        extracted_json=json.dumps(result, ensure_ascii=False, indent=2),
        extra_note=extra_note.strip() if extra_note.strip() else "(없음)",
    )
    try:
        body = await provider.complete(
            system=prompts.GENERATE_PROFILE_BODY_SYSTEM,
            user=user_body,
            model=model,
            operation="프로필 본문 생성",
            max_tokens=max_tokens,
        )
    except LLMAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    logger.info("프로필 본문 생성 완료: %d자", len(body))

    fm = CandidateProfile(**result, source_files=filenames)
    record = storage.write_profile(fm, body)
    logger.info("프로필 저장 완료: %s", settings.candidate_profile_path)
    return record


# ── 회사 목록 / 상세 ──────────────────────────────────────────────────────────

@app.get("/api/companies", response_model=list[CompanyMeta])
async def list_companies():
    return storage.list_companies()


_CSV_SCALAR_COLS = [
    "slug", "company_name", "display_name", "job_title", "status",
    "fit_score", "fit_label",
    "location", "employee_count", "stability", "investment_stage",
    "jobplanet_score", "salary_min", "salary_max", "salary_note",
    "industry", "experience_required", "employment_type",
    "salary_check", "stability_check", "location_check",
    "source_url", "created_at",
]
_CSV_LIST_COLS = ["tech_stack", "tags", "strengths", "gaps", "required_skills"]


@app.get("/api/companies/export/csv")
async def export_companies_csv():
    """모든 회사 데이터를 CSV 파일로 내보낸다. Excel 호환을 위해 UTF-8 BOM 포함."""
    companies = storage.list_companies()
    all_cols = _CSV_SCALAR_COLS + _CSV_LIST_COLS

    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM — Excel이 한글을 깨지 않고 여는 데 필요
    writer = csv.DictWriter(buf, fieldnames=all_cols)
    writer.writeheader()

    for company in companies:
        fm = company.frontmatter
        row: dict = {"slug": company.slug}
        for col in _CSV_SCALAR_COLS[1:]:
            val = getattr(fm, col, None)
            row[col] = "" if val is None else str(val)
        for col in _CSV_LIST_COLS:
            val = getattr(fm, col, [])
            row[col] = " | ".join(val) if isinstance(val, list) else str(val or "")
        writer.writerow(row)

    filename = f"companies_{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.get("/api/companies/compare")
async def compare_companies(slugs: list[str] = Query(...)):
    """slug 목록(반복 query parameter)을 받아 여러 회사 데이터를 반환."""
    slug_list = [s.strip() for s in slugs if s.strip()]
    if len(slug_list) > 5:
        raise HTTPException(status_code=422, detail="한 번에 최대 5개 회사까지 비교할 수 있습니다.")
    results = []
    for slug in slug_list:
        record = storage.read_company(slug)
        if record:
            results.append(record)
    return results


def _parse_status_log(body: str, slug: str = "") -> list[dict]:
    """마크다운 본문의 '지원 상태 로그' 섹션에서 날짜·레이블 항목을 파싱한다."""
    if not body:
        logger.warning("_parse_status_log: body가 비어 있습니다 (slug=%s)", slug)
        return []
    entries: list[dict] = []
    in_log = False
    for line in body.split('\n'):
        if re.match(r'##\s*\d*\.?\s*지원 상태 로그', line):
            in_log = True
            continue
        if in_log:
            if line.startswith('## '):
                break
            m = re.match(r'-\s*(\d{4}-\d{2}-\d{2}):\s*(.+)', line.strip())
            if m:
                entries.append({"date": m.group(1), "label": m.group(2).strip()})
    return entries


_NEGLECTED_DAYS = 7  # 이 기간 동안 상태 변경 없으면 "방치된 항목"으로 콜아웃
_ACTIVE_STATUSES = {"지원", "서류통과", "인터뷰", "최종", "보류"}  # 미지원/탈락/지원마감은 방치 대상 아님


def _build_weekly_summary_materials() -> dict:
    """주간 지원 현황 요약 알림 재료를 조립한다: 신규 등록/상태별 개수/방치된 항목."""
    metas = storage.list_companies()
    today = date.today()
    week_start = today - timedelta(days=today.weekday())

    new_count = 0
    status_counts: dict[str, int] = {}
    neglected = []

    for meta in metas:
        fm = meta.frontmatter
        status_counts[fm.status] = status_counts.get(fm.status, 0) + 1

        if fm.created_at:
            try:
                if datetime.fromisoformat(fm.created_at).date() >= week_start:
                    new_count += 1
            except ValueError:
                pass

        if fm.status in _ACTIVE_STATUSES:
            record = storage.read_company(meta.slug)
            if not record:
                continue
            log_entries = _parse_status_log(record.body, slug=meta.slug)
            if not log_entries:
                continue
            last_date = date.fromisoformat(log_entries[-1]["date"])
            days_elapsed = (today - last_date).days
            if days_elapsed >= _NEGLECTED_DAYS:
                neglected.append({
                    "name": fm.display_name or fm.company_name,
                    "status": fm.status,
                    "days": days_elapsed,
                })

    return {
        "kind": "weekly_summary",
        "period": f"{week_start.isoformat()} ~ {today.isoformat()}",
        "new_count": new_count,
        "status_counts": status_counts,
        "neglected": neglected,
    }


async def _weekly_summary_loop():
    """설정된 요일·시각(기본 월요일 09:00)에 지원 현황 요약 알림을 발송한다."""
    while True:
        now = datetime.now()
        schedule = get_weekly_summary_schedule()
        days_ahead = (schedule["weekday"] - now.weekday()) % 7
        next_run = (now + timedelta(days=days_ahead)).replace(
            hour=schedule["hour"], minute=schedule["minute"], second=0, microsecond=0
        )
        if next_run <= now:
            next_run += timedelta(days=7)
        await asyncio.sleep((next_run - now).total_seconds())
        if get_notify_pref("notify_weekly_summary"):
            try:
                await send_notification(_build_weekly_summary_materials())
            except Exception as e:
                logger.warning("주간 요약 알림 전송 실패: %s", e)


@app.get("/api/companies/timeline")
async def get_companies_timeline():
    """모든 회사의 상태 로그를 파싱해 타임라인 데이터를 반환한다."""
    metas = storage.list_companies()
    results = []
    for meta in metas:
        try:
            record = storage.read_company(meta.slug)
        except Exception as e:
            logger.warning("타임라인 조회 중 파일 읽기 실패 (slug=%s): %s", meta.slug, e)
            continue
        if not record:
            continue
        fm = record.frontmatter
        log_entries = _parse_status_log(record.body, slug=meta.slug)
        if not log_entries:
            continue
        results.append({
            "slug": meta.slug,
            "display_name": fm.display_name or fm.company_name,
            "job_title": fm.job_title or "",
            "status": fm.status or "미지원",
            "fit_score": fm.fit_score,
            "fit_label": fm.fit_label,
            "log_entries": log_entries,
        })
    return results


@app.get("/api/companies/{slug}", response_model=CompanyRecord)
async def get_company(slug: str):
    record = storage.read_company(slug)
    if not record:
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다.")
    return record


_EDIT_FORM_FIELDS = {
    "company_name", "job_title", "source_url",
    "location", "employee_count", "stability", "investment_stage",
    "jobplanet_score", "status",
    "tech_stack", "tags",
}


@app.put("/api/companies/{slug}")
async def update_company(slug: str, req: CompanyUpdateRequest):
    existing = storage.read_company(slug)
    if not existing:
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다.")
    # 편집 폼 필드만 덮어쓰고, LLM 분석 데이터(strengths/gaps/required_skills 등)는 보존
    fm = existing.frontmatter.model_copy(
        update={k: v for k, v in req.frontmatter.model_dump().items() if k in _EDIT_FORM_FIELDS}
    )
    fm.created_at = existing.frontmatter.created_at
    record = storage.write_company(slug, fm, req.body)
    logger.info("공고 수동 편집: %s", slug)
    return record


@app.delete("/api/companies/{slug}")
async def delete_company(slug: str):
    try:
        deleted = storage.delete_company(slug, pre_delete_hook=_save_backup_zip)
    except Exception as e:
        logger.error("삭제 전 백업 실패로 삭제 중단 (slug=%s): %s", slug, e)
        raise HTTPException(status_code=500, detail="삭제 전 백업에 실패해 삭제가 취소되었습니다. 잠시 후 다시 시도해주세요.")
    if not deleted:
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다.")
    logger.info("공고 삭제: %s", slug)
    return {"status": "deleted"}


class SyncWantedRequest(BaseModel):
    source_url: str | None = None


@app.post("/api/companies/{slug}/sync-wanted")
async def sync_wanted(slug: str, req: SyncWantedRequest = SyncWantedRequest()):
    """Wanted 기업 페이지에서 구조화된 회사 정보를 가져와 frontmatter를 업데이트한다.
    source_url을 body에 넘기면 그 URL을 사용하고, 없으면 기존 frontmatter의 source_url을 사용한다.
    """
    existing = storage.read_company(slug)
    if not existing:
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다.")

    url = req.source_url or existing.frontmatter.source_url
    if not url or not scraper.is_wanted_host(url):
        raise HTTPException(status_code=400, detail="원티드 URL이 없습니다. 편집 폼에서 source_url을 먼저 입력해주세요.")

    facts = await scraper.fetch_wanted_facts(url)
    if not facts:
        raise HTTPException(status_code=502, detail="원티드에서 기업 정보를 가져오지 못했습니다.")

    fm = existing.frontmatter
    if req.source_url:
        fm.source_url = req.source_url
        fm.source_type = "url"

    if facts["employee_count"]:
        fm.employee_count = facts["employee_count"]
    if facts["employee_count_meets_threshold"] is not None:
        fm.employee_count_meets_threshold = facts["employee_count_meets_threshold"]
    if facts["website"]:
        fm.website = facts["website"]
    if facts["location"] and not fm.location:
        fm.location = facts["location"]
    if facts["investment_stage"]:
        fm.investment_stage = facts["investment_stage"]
    if facts["stability"]:
        fm.stability = facts["stability"]
    if facts["revenue_status"]:
        fm.revenue_status = facts["revenue_status"]

    record = storage.write_company(slug, fm, existing.body)
    updated_keys = [k for k, v in facts.items() if v is not None]
    logger.info("원티드 동기화: %s → 업데이트 필드: %s", slug, updated_keys)
    return {"status": "ok", "updated": {k: v for k, v in facts.items() if v is not None}}


# ── 본문 섹션 유틸 ───────────────────────────────────────────────────────────

def _replace_fit_section(body: str, new_section: str) -> str:
    """\n## 기준으로 섹션을 분리해 적합도 리포트 섹션을 교체한다.
    기존 섹션이 여러 개 쌓여있으면 모두 제거하고 하나로 교체한다.
    없으면 지원 상태 로그 앞에 삽입하고, 그것도 없으면 끝에 추가한다."""
    parts = body.split("\n## ")
    section_content = new_section.removeprefix("## ")

    # 1. 기존 적합도 리포트 + 종합 의견 섹션 모두 제거 후 첫 위치에 교체
    fit_indices = [
        i for i in range(1, len(parts))
        if parts[i].startswith("4. 적합도 리포트") or parts[i].startswith("5. 종합 의견")
    ]
    if fit_indices:
        first = fit_indices[0]
        parts[first] = section_content
        for i in reversed(fit_indices[1:]):
            del parts[i]
        return "\n## ".join(parts)

    # 2. 없으면 지원 상태 로그 앞에 삽입
    for i in range(1, len(parts)):
        if re.match(r"\d*\.?\s*지원 상태 로그", parts[i]):
            parts.insert(i, section_content)
            return "\n## ".join(parts)

    # 3. 둘 다 없으면 맨 끝에 추가
    return body.rstrip() + f"\n\n{new_section}"


def _append_status_log(body: str, label: str) -> str:
    """'지원 상태 로그' 섹션 끝에 오늘 날짜로 새 항목을 추가한다. 섹션이 없으면 새로 만든다."""
    new_line = f"- {date.today().isoformat()}: {label}"
    lines = body.split("\n")
    log_header_idx = next(
        (i for i, line in enumerate(lines) if re.match(r"##\s*\d*\.?\s*지원 상태 로그", line)), None,
    )
    if log_header_idx is None:
        return body.rstrip() + f"\n\n## 지원 상태 로그\n{new_line}"

    insert_idx = len(lines)
    for i in range(log_header_idx + 1, len(lines)):
        if lines[i].startswith("## "):
            insert_idx = i
            break
    while insert_idx > log_header_idx + 1 and lines[insert_idx - 1].strip() == "":
        insert_idx -= 1
    lines.insert(insert_idx, new_line)
    return "\n".join(lines)


# ── 회사 추가 ─────────────────────────────────────────────────────────────────

_in_progress_count = 0


def _track_in_progress(fn):
    """분석 파이프라인 실행 중 건수를 추적한다.
    페이지를 나갔다가 돌아와도(프론트가 리셋돼도) 진행 상태를 조회할 수 있도록 서버에 건수를 남겨둔다."""
    async def wrapper(*args, **kwargs):
        global _in_progress_count
        _in_progress_count += 1
        try:
            return await fn(*args, **kwargs)
        finally:
            _in_progress_count -= 1
    return wrapper


@_track_in_progress
async def _process_company(
    raw_text: str,
    source_type: str,
    source_url: str | None,
    existing_slug: str | None = None,
    company_name_override: str = "",
    job_title_override: str = "",
    preserve_fm: CompanyFrontmatter | None = None,
    preserved_log_entries: list[dict] | None = None,
) -> CompanyRecord:
    """텍스트 → 추출 → 잡플래닛 조회 → 본문 생성 → 적합도 평가 → 저장.

    preserve_fm/preserved_log_entries가 주어지면(refill 시) 사용자가 직접 관리하는
    상태·핀·태그·지원경로·생성일과 지원 상태 로그 이력을 새 분석 결과에 덮어써 보존한다.
    """
    # storage에는 원본 raw_text를 그대로 저장해야 하므로, 프롬프트 삽입용 이스케이프 사본을 별도로 둔다.
    safe_raw_text = prompts.escape_tag_chars(raw_text)

    # 파이프라인 시작 시점에 provider/모델을 스냅샷 떠서 이후 실행 도중 설정이 바뀌어도 섞이지 않게 한다.
    snap = capture_snapshot()

    # 1. Lightweight: 공고 텍스트에서 구조화 데이터 추출
    light, light_model = light_from_snapshot(snap)
    logger.info("[1/4] 구조화 추출 시작 (model=%s, 텍스트 %d자)", light_model, len(raw_text))
    user_extract = prompts.EXTRACT_COMPANY_USER_TEMPLATE.format(raw_text=safe_raw_text)
    extracted = await light.extract_structured(
        system=prompts.EXTRACT_COMPANY_SYSTEM,
        user=user_extract,
        tool_name=prompts.EXTRACT_COMPANY_TOOL_NAME,
        tool_description=prompts.EXTRACT_COMPANY_TOOL_DESCRIPTION,
        tool_schema=prompts.EXTRACT_COMPANY_TOOL_SCHEMA,
        model=light_model,
        operation="공고 추출",
        reasoning_effort=snap.reasoning_effort,
    )
    logger.info("[1/4] 추출 완료: %s / %s", extracted.get("company_name"), extracted.get("job_title"))

    # 사용자가 직접 입력한 값으로 덮어쓰기 (LLM 추출 결과 무시)
    if company_name_override:
        extracted["company_name"] = company_name_override
        if not extracted.get("display_name"):
            extracted["display_name"] = company_name_override
    if job_title_override:
        extracted["job_title"] = job_title_override
    # display_name 폴백: LLM이 None 반환하면 company_name으로 대체
    if not extracted.get("display_name"):
        extracted["display_name"] = extracted.get("company_name") or ""

    # 2. 잡플래닛 평점 조회 (refill 시 기존 점수 있으면 재사용, 없으면 스크래핑)
    company_name_for_jp = extracted.get("display_name") or extracted.get("company_name", "")
    if company_name_for_jp:
        existing_jp_score = None
        existing_jp_review = None
        if existing_slug:
            try:
                existing_record = storage.read_company(existing_slug)
                if existing_record:
                    existing_jp_score = existing_record.frontmatter.jobplanet_score
                    existing_jp_review = existing_record.frontmatter.jobplanet_review_count
            except Exception:
                pass

        if existing_jp_score is not None:
            extracted["jobplanet_score"] = existing_jp_score
            extracted["jobplanet_review_count"] = existing_jp_review
            logger.info("[2/4] 잡플래닛 기존값 재사용: %.1f점", existing_jp_score)
        else:
            logger.info("[2/4] 잡플래닛 조회: %s", company_name_for_jp)
            jp = await fetch_jobplanet_score(company_name_for_jp)
            if jp.score is not None:
                extracted["jobplanet_score"] = jp.score
                extracted["jobplanet_review_count"] = jp.review_count
                logger.info("[2/4] 잡플래닛 결과: %.1f점 (%d건)", jp.score, jp.review_count or 0)
            else:
                logger.info("[2/4] 잡플래닛 결과: %s", jp.source)

    # 3. Lightweight: 구조화 데이터 + 원문을 바탕으로 마크다운 본문 생성
    logger.info("[3/4] 마크다운 본문 생성 시작")
    user_body = prompts.GENERATE_BODY_USER_TEMPLATE.format(
        company_json=json.dumps(extracted, ensure_ascii=False),
        raw_text=safe_raw_text[:4000],
    )
    body = await light.complete(
        system=prompts.GENERATE_BODY_SYSTEM,
        user=user_body,
        model=light_model,
        operation="본문 생성",
        reasoning_effort=snap.reasoning_effort,
    )
    logger.info("[3/4] 본문 생성 완료: %d자", len(body))

    # 4. High: 후보자 프로필 대비 적합도 평가 (프로필 없으면 생략)
    fit_data: dict = {}
    fit_report = ""
    if storage.profile_exists():
        high, high_model = high_from_snapshot(snap)
        logger.info("[4/4] 적합도 평가 시작 (model=%s)", high_model)
        profile_text = storage.read_profile_text() or ""
        eval_criteria = storage.read_eval_criteria().strip()
        custom_criteria_section = (
            f"\n\n## 추가 평가 기준 (사용자 지정)\n{eval_criteria}{prompts.CUSTOM_CRITERIA_BOUNDARY_NOTICE}"
            if eval_criteria else ""
        )
        user_fit = prompts.EVALUATE_FIT_USER_TEMPLATE.format(
            candidate_profile=profile_text,
            company_json=json.dumps(extracted, ensure_ascii=False),
            raw_text=safe_raw_text[:4000],
            custom_criteria=custom_criteria_section,
        )
        # Gemini는 function call 내에 장문 마크다운 생성 시 MALFORMED_FUNCTION_CALL이 발생함.
        # 구조화 데이터(점수·라벨·강점·갭)만 tool call로 추출하고, 리포트 본문은 complete()로 분리 생성.
        if snap.provider_name == "gemini":
            _gemini_fit_schema = {
                **prompts.EVALUATE_FIT_TOOL_SCHEMA,
                "properties": {k: v for k, v in prompts.EVALUATE_FIT_TOOL_SCHEMA["properties"].items() if k != "fit_report_body"},
                "required": [r for r in prompts.EVALUATE_FIT_TOOL_SCHEMA.get("required", []) if r != "fit_report_body"],
            }
            fit_result = await high.extract_structured(
                system=_evaluate_fit_system(snap.provider_name),
                user=user_fit,
                tool_name=prompts.EVALUATE_FIT_TOOL_NAME,
                tool_description=prompts.EVALUATE_FIT_TOOL_DESCRIPTION,
                tool_schema=_gemini_fit_schema,
                model=high_model,
                operation="적합도 평가",
                reasoning_effort=snap.reasoning_effort,
            )
            # Gemini 전용: location_check → gaps 자동 브릿지
            _loc = fit_result.get("location_check", "")
            _gaps = fit_result.get("gaps", [])
            if _loc and ("조건부" in _loc or "미달" in _loc):
                if not any(kw in g for g in _gaps for kw in ("근무지", "위치", "출퇴근", "판교", "location")):
                    fit_result["gaps"] = _gaps + [f"(하) 근무지 조건부 - {_loc}"]
                    logger.info("  → 근무지 갭 자동 보정: %s", _loc)
            logger.info("[4/4] 적합도 리포트 본문 생성 시작 (Gemini 분리 생성)")
            fit_report = await high.complete(
                system=_evaluate_fit_system(snap.provider_name),
                user=user_fit + f"\n\n평가 결과 (참고용):\n{json.dumps(fit_result, ensure_ascii=False)}\n\n위 평가 결과를 바탕으로 fit_report_body 전체를 아래 형식에 맞게 작성하세요. ## 4. 적합도 리포트 로 시작하고, ## 5. 종합 의견 (핵심 근거 + 지원 전략)까지 빠짐없이 작성하세요.",
                model=high_model,
                operation="적합도 리포트 본문 생성",
                max_tokens=8192,
                reasoning_effort=snap.reasoning_effort,
            )
            fit_report = re.sub(r'^##\s*4\.\s*적합도 리포트[^\n]*\n+', '', fit_report.strip()).strip()
        else:
            fit_result = await high.extract_structured(
                system=_evaluate_fit_system(snap.provider_name),
                user=user_fit,
                tool_name=prompts.EVALUATE_FIT_TOOL_NAME,
                tool_description=prompts.EVALUATE_FIT_TOOL_DESCRIPTION,
                tool_schema=prompts.EVALUATE_FIT_TOOL_SCHEMA,
                model=high_model,
                operation="적합도 평가",
                reasoning_effort=snap.reasoning_effort,
            )
            fit_report = re.sub(r'^##\s*4\.\s*적합도 리포트[^\n]*\n+', '', fit_result.pop("fit_report_body", "").strip()).strip()
        fit_data = fit_result
        logger.info("[4/4] 적합도 평가 완료: %s점 (%s)", fit_data.get("fit_score"), fit_data.get("fit_label"))
    else:
        logger.info("[4/4] 프로필 없음 — 적합도 평가 생략")

    # 5. frontmatter 조립 및 저장
    fm_data = {**extracted, **fit_data, "source_type": source_type, "llm_provider": snap.provider_name}
    if source_url:
        fm_data["source_url"] = source_url
    if preserve_fm:
        # 재분석(refill)으로 LLM이 갱신하는 필드가 아니라 사용자가 직접 관리하는 필드는 기존 값을 유지한다.
        for field in ("status", "pinned", "tags", "application_source", "created_at"):
            fm_data[field] = getattr(preserve_fm, field)
    fm = CompanyFrontmatter(**fm_data)

    # 지원 상태 로그: refill(existing_slug 있음)이면 기존 이력 위에 이어붙이고, 신규 생성이면 한 줄로 시작.
    # preserved_log_entries의 존재 여부가 아니라 호출 종류(existing_slug)로 문구를 판정해야,
    # 기존 로그가 비어있거나 파싱에 실패한 상태로 refill해도 "재분석 완료"가 정확히 기록된다.
    is_refill = existing_slug is not None
    log_lines = [f"- {e['date']}: {e['label']}" for e in (preserved_log_entries or [])]
    log_lines.append(f"- {date.today().isoformat()}: {'재분석 완료' if is_refill else '분석 완료'}")
    log_body = "\n".join(log_lines)

    # 섹션 추가: 적합도 있으면 4(리포트)+5(종합의견) → 6(로그), 없으면 4(로그)
    if fit_report:
        body += f"\n\n## 4. 적합도 리포트 — {fit_data.get('fit_score', '?')} / 100\n\n{fit_report}"
        body += f"\n\n## 6. 지원 상태 로그\n{log_body}"
    else:
        body += f"\n\n## 4. 지원 상태 로그\n{log_body}"

    slug = existing_slug or storage.make_slug(fm.company_name, fm.job_title or "")
    storage.write_raw_text(slug, raw_text)
    record = storage.write_company(slug, fm, body)

    materials = {
        "company": fm.display_name or fm.company_name,
        "job_title": fm.job_title or "",
        "score": fit_data.get("fit_score", ""),
        "label": fit_data.get("fit_label", ""),
    }
    if get_notify_pref("notify_strengths") and fm.strengths:
        materials["strengths"] = [item.split(" - ", 1)[0].strip() for item in fm.strengths[:2]]
    if get_notify_pref("notify_gaps") and fm.gaps:
        materials["gaps"] = [item.split(" - ", 1)[0].strip() for item in fm.gaps[:2]]
    if get_notify_pref("notify_jobplanet_rating") and fm.jobplanet_score:
        materials["jobplanet"] = fm.jobplanet_score
    if get_notify_pref("notify_employee_count") and fm.employee_count:
        materials["employee_count"] = fm.employee_count

    await send_notification(materials)
    return record


@app.post("/api/companies/from-url")
async def add_from_url(req: FromUrlRequest):
    # 동일 URL이 이미 등록되어 있으면 기존 슬러그 반환
    duplicate = next(
        (c for c in storage.list_companies() if c.frontmatter.source_url == req.url),
        None,
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail={
                "slug": duplicate.slug,
                "name": duplicate.frontmatter.display_name or duplicate.frontmatter.company_name,
            },
        )
    try:
        raw_text = await scraper.fetch_url_text(req.url)
    except ValueError as e:
        # JS 렌더링 필요 — 텍스트 붙여넣기 유도
        raise HTTPException(status_code=422, detail=str(e))
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="사이트 응답 시간이 초과됐습니다 (20초). 사이트가 느리거나 접근이 차단됐을 수 있습니다.")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"사이트가 {e.response.status_code} 오류를 반환했습니다. URL을 확인해주세요.")
    except Exception:
        raise HTTPException(status_code=502, detail="URL 접근 실패: 네트워크 연결 오류. URL을 다시 확인해주세요.")
    try:
        return await asyncio.wait_for(_process_company(raw_text, "url", req.url), timeout=300)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="분석 시간이 초과되었습니다 (300초). 잠시 후 다시 시도해주세요.")
    except LLMAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@app.post("/api/companies/from-text")
async def add_from_text(req: FromTextRequest):
    if not req.text.strip():
        # 텍스트 없이 기본 정보만 입력한 경우 — LLM 없이 수동 저장
        fm = CompanyFrontmatter(
            company_name=req.company_name,
            display_name=req.company_name,
            job_title=req.job_title,
            source_url=req.source_url,
            source_type="manual",
        )
        body = f"# {req.company_name} — {req.job_title}\n\n## 지원 상태 로그\n- {date.today().isoformat()}: 등록"
        slug = storage.make_slug(req.company_name, req.job_title)
        return storage.write_company(slug, fm, body)
    try:
        return await asyncio.wait_for(
            _process_company(
                req.text, "text_paste", req.source_url,
                company_name_override=req.company_name,
                job_title_override=req.job_title,
            ),
            timeout=300,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="분석 시간이 초과되었습니다 (300초). 잠시 후 다시 시도해주세요.")
    except LLMAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@app.post("/api/companies/from-image")
async def add_from_image(files: list[UploadFile] = File(...)):
    """이미지(스크린샷/사진) 업로드 → 텍스트 추출 → 공고 분석."""
    if not files:
        raise HTTPException(status_code=400, detail="이미지 파일이 없습니다.")
    if len(files) > _MAX_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail=f"이미지는 한 번에 최대 {_MAX_UPLOAD_FILES}장까지 업로드할 수 있습니다.")

    image_blocks: list[dict] = []
    for file in files:
        if file.content_type not in _ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"지원하지 않는 형식입니다: {file.filename} ({file.content_type}). JPEG/PNG/WebP/GIF만 가능합니다.",
            )
        if file.size is not None and file.size > _MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"이미지가 너무 큽니다: {file.filename} ({_MAX_IMAGE_BYTES // (1024*1024)}MB 이하만 가능)",
            )
        data = await file.read()
        try:
            resized, media_type = _resize_image(data)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"이미지 처리 실패: {file.filename} — {e}")
        b64 = base64.standard_b64encode(resized).decode()
        image_blocks.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}})

    logger.info("이미지 텍스트 추출 시작 (%d장)", len(image_blocks))
    high, high_model = high_provider()
    content = image_blocks + [{
        "type": "text",
        "text": "이 이미지(들)는 채용공고 스크린샷입니다. 채용공고에 적힌 모든 텍스트를 순서대로 빠짐없이 추출해주세요. 텍스트만 출력하고 다른 설명은 하지 마세요.",
    }]
    try:
        raw_text = await high.complete(
            system="당신은 이미지에서 텍스트를 정확히 추출하는 전문가입니다.",
            user="",
            model=high_model,
            operation="이미지 텍스트 추출",
            content=content,
        )
    except LLMAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    if not raw_text.strip():
        raise HTTPException(status_code=422, detail="이미지에서 텍스트를 추출할 수 없습니다.")
    logger.info("이미지 텍스트 추출 완료: %d자", len(raw_text))

    try:
        return await asyncio.wait_for(_process_company(raw_text, "image", None), timeout=300)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="분석 시간이 초과되었습니다 (300초). 잠시 후 다시 시도해주세요.")
    except LLMAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@app.post("/api/companies/manual")
async def add_manual(req: ManualCompanyRequest):
    fm = CompanyFrontmatter(
        company_name=req.company_name,
        display_name=req.display_name or req.company_name,
        job_title=req.job_title,
        source_url=req.source_url,
        source_type="manual",
    )
    body = f"# {fm.display_name} — {fm.job_title}\n\n{req.notes}"
    slug = storage.make_slug(fm.company_name, fm.job_title or "")
    return storage.write_company(slug, fm, body)


@app.post("/api/companies/{slug}/refill")
async def refill_company(slug: str):
    """저장된 원문으로 회사 정보를 전체 재분석한다 (구조화 추출 → 잡플래닛 → 본문 → 적합도).
    원문이 없는 기존 항목은 마크다운 본문으로 폴백한다."""
    record = storage.read_company(slug)
    if not record:
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다.")
    raw_text = storage.read_raw_text(slug) or record.body
    source_url = record.frontmatter.source_url
    preserved_log_entries = _parse_status_log(record.body, slug=slug)
    try:
        return await _process_company(
            raw_text, record.frontmatter.source_type, source_url, existing_slug=slug,
            preserve_fm=record.frontmatter, preserved_log_entries=preserved_log_entries,
        )
    except LLMAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@app.post("/api/companies/{slug}/pin")
async def toggle_pin(slug: str):
    """즐겨찾기 핀 토글 — pinned 필드만 반전시켜 저장."""
    record = storage.read_company(slug)
    if not record:
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다.")
    fm = record.frontmatter.model_copy(update={"pinned": not record.frontmatter.pinned})
    storage.write_company(slug, fm, record.body)
    logger.info("핀 토글: %s → %s", slug, fm.pinned)
    return {"pinned": fm.pinned}


@app.post("/api/companies/{slug}/refit")
async def refit_company(slug: str):
    """적합도 점수만 재산정한다.
    기존 frontmatter(구조화 데이터)와 본문을 그대로 두고 High 티어 LLM만 1회 호출한다."""
    record = storage.read_company(slug)
    if not record:
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다.")
    if not storage.profile_exists():
        raise HTTPException(status_code=400, detail="프로필이 없습니다. 먼저 이력서를 업로드해주세요.")

    snap = capture_snapshot()
    high, high_model = high_from_snapshot(snap)
    logger.info("[refit] 적합도 재산정 시작 (slug=%s, model=%s)", slug, high_model)

    profile_text = storage.read_profile_text() or ""
    raw_text = prompts.escape_tag_chars(storage.read_raw_text(slug) or record.body)
    eval_criteria = storage.read_eval_criteria().strip()
    custom_criteria_section = (
        f"\n\n## 추가 평가 기준 (사용자 지정)\n{eval_criteria}{prompts.CUSTOM_CRITERIA_BOUNDARY_NOTICE}"
        if eval_criteria else ""
    )
    # 이전 평가 결과(strengths/gaps/fit_score 등)는 LLM 입력에서 제외 — 자기참조 편향 방지
    _REFIT_EXCLUDE = {"strengths", "gaps", "fit_score", "fit_label", "fit_report_body"}
    company_data = {
        k: v for k, v in record.frontmatter.model_dump().items()
        if k not in _REFIT_EXCLUDE
    }
    user_fit = prompts.EVALUATE_FIT_USER_TEMPLATE.format(
        candidate_profile=profile_text,
        company_json=json.dumps(company_data, ensure_ascii=False, indent=2),
        raw_text=raw_text[:4000],
        custom_criteria=custom_criteria_section,
    )
    try:
        if snap.provider_name == "gemini":
            _gemini_fit_schema = {
                **prompts.EVALUATE_FIT_TOOL_SCHEMA,
                "properties": {k: v for k, v in prompts.EVALUATE_FIT_TOOL_SCHEMA["properties"].items() if k != "fit_report_body"},
                "required": [r for r in prompts.EVALUATE_FIT_TOOL_SCHEMA.get("required", []) if r != "fit_report_body"],
            }
            fit_result = await high.extract_structured(
                system=_evaluate_fit_system(snap.provider_name),
                user=user_fit,
                tool_name=prompts.EVALUATE_FIT_TOOL_NAME,
                tool_description=prompts.EVALUATE_FIT_TOOL_DESCRIPTION,
                tool_schema=_gemini_fit_schema,
                model=high_model,
                operation="적합도 재평가",
                reasoning_effort=snap.reasoning_effort,
            )
            fit_report_raw = await high.complete(
                system=_evaluate_fit_system(snap.provider_name),
                user=user_fit + f"\n\n평가 결과 (참고용):\n{json.dumps(fit_result, ensure_ascii=False)}\n\n위 평가 결과를 바탕으로 fit_report_body 전체를 아래 형식에 맞게 작성하세요. ## 4. 적합도 리포트 로 시작하고, ## 5. 종합 의견 (핵심 근거 + 지원 전략)까지 빠짐없이 작성하세요.",
                model=high_model,
                operation="적합도 리포트 본문 생성",
                max_tokens=8192,
                reasoning_effort=snap.reasoning_effort,
            )
            fit_report = re.sub(r'^##\s*4\.\s*적합도 리포트[^\n]*\n+', '', fit_report_raw.strip()).strip()
        else:
            fit_result = await high.extract_structured(
                system=_evaluate_fit_system(snap.provider_name),
                user=user_fit,
                tool_name=prompts.EVALUATE_FIT_TOOL_NAME,
                tool_description=prompts.EVALUATE_FIT_TOOL_DESCRIPTION,
                tool_schema=prompts.EVALUATE_FIT_TOOL_SCHEMA,
                model=high_model,
                operation="적합도 재평가",
                reasoning_effort=snap.reasoning_effort,
            )
            fit_report = re.sub(r'^##\s*4\.\s*적합도 리포트[^\n]*\n+', '', fit_result.pop("fit_report_body", "").strip()).strip()
    except LLMAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    logger.info("[refit] 완료: %s점 (%s)", fit_result.get("fit_score"), fit_result.get("fit_label"))

    # frontmatter에 적합도 필드만 업데이트
    fm = record.frontmatter.model_copy(update=fit_result)

    body = record.body
    new_section = f"## 4. 적합도 리포트 — {fit_result.get('fit_score', '?')} / 100\n\n{fit_report}"
    body = _replace_fit_section(body, new_section)
    body = _append_status_log(body, "적합도 재평가 완료")

    record = storage.write_company(slug, fm, body)
    return record


# ── Q&A (SSE 스트리밍) ────────────────────────────────────────────────────────

def _make_sse(gen):
    """AsyncIterator를 SSE(text/event-stream) StreamingResponse로 변환."""
    async def _wrapped():
        try:
            async for chunk in gen:
                yield f"data: {json.dumps({'text': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(_wrapped(), media_type="text/event-stream")


def _build_qa_messages(context_part: str, history: list[QAMessage], question: str) -> list[dict]:
    """대화 히스토리를 포함한 멀티턴 메시지 배열 구성. 컨텍스트는 첫 메시지에만 포함하고,
    연속으로 같은 role이 나오면 하나의 메시지로 합쳐 역할이 user/assistant로 번갈아 나오게 한다."""
    turns = [{"role": h.role, "text": h.text} for h in history]
    turns.append({"role": "user", "text": f"## 질문\n{question}"})

    messages: list[dict] = []
    for turn in turns:
        if messages and messages[-1]["role"] == turn["role"]:
            if isinstance(messages[-1]["content"], list):
                messages[-1]["content"].append({"type": "text", "text": turn["text"]})
            else:
                messages[-1]["content"] += "\n\n" + turn["text"]
            continue
        if not messages:
            messages.append({"role": turn["role"], "content": [
                {"type": "text", "text": context_part, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": turn["text"]},
            ]})
        else:
            messages.append({"role": turn["role"], "content": turn["text"]})
    return messages


@app.post("/api/companies/{slug}/qa")
async def company_qa(slug: str, req: QARequest):
    """단일 회사 Q&A — 회사 정보 + 후보자 프로필을 컨텍스트로 High 티어 스트리밍."""
    record = storage.read_company(slug)
    if not record:
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다.")
    profile_text = storage.read_profile_text() or "후보자 프로필 없음"
    company_context = f"{record.frontmatter.model_dump_json(indent=2)}\n\n{record.body}"

    context_part = f"## 후보자 프로필\n{profile_text}\n\n## 회사 정보\n{company_context}\n\n"
    provider, model = high_provider()
    gen = provider.stream(
        system=prompts.QA_SYSTEM,
        messages=_build_qa_messages(context_part, req.history, req.question),
        model=model,
        operation="Q&A",
    )
    return _make_sse(gen)


@app.post("/api/companies/qa")
async def multi_company_qa(req: MultiQARequest):
    """다중 회사 Q&A — 선택한 회사들의 정보를 모두 컨텍스트로 넣어 비교 질문에 답변."""
    contexts: list[str] = []
    for slug in req.slugs:
        record = storage.read_company(slug)
        if record:
            contexts.append(
                f"=== {record.frontmatter.display_name} ===\n"
                f"{record.frontmatter.model_dump_json(indent=2)}\n\n{record.body}"
            )
    if not contexts:
        raise HTTPException(status_code=404, detail="선택한 회사를 찾을 수 없습니다.")

    profile_text = storage.read_profile_text() or "후보자 프로필 없음"
    company_context = "\n\n---\n\n".join(contexts)

    context_part = f"## 후보자 프로필\n{profile_text}\n\n## 회사 정보\n{company_context}\n\n"
    provider, model = high_provider()
    gen = provider.stream(
        system=prompts.QA_SYSTEM,
        messages=_build_qa_messages(context_part, req.history, req.question),
        model=model,
        operation="Multi Q&A",
    )
    return _make_sse(gen)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
