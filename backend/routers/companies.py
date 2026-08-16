"""회사 CRUD, 목록/타임라인/비교/CSV export, 원티드 동기화, 주간 요약,
그리고 회사 추가 파이프라인(URL/텍스트/이미지 → 구조화 추출 → 잡플래닛 → 본문 → 적합도 평가 → 저장).

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
import functools
import io
import json
import logging
import re
from datetime import date, datetime, timedelta

import httpx
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import prompts
from services import scraper
import storage
from config import get_notify_pref, get_weekly_summary_schedule, settings
from export import save_backup_zip
from routers.rag import trigger_reindex_background
from services.app_db import create_fit_history_entry, latest_profile_version_id, list_fit_history
from services.jobplanet import fetch_jobplanet_score
from llm.base import LLMAPIError
from llm.router import LLMSnapshot, capture_snapshot, high_from_snapshot, light_from_snapshot
from models import (
    CompanyFrontmatter,
    CompanyMeta,
    CompanyRecord,
    CompanyUpdateRequest,
    FromTextRequest,
    FromUrlRequest,
    ManualCompanyRequest,
)
from notify import send_notification

logger = logging.getLogger(__name__)
router = APIRouter()

# ── 이미지 유틸 (from-image 전용) ──────────────────────────────────────────────

_MAX_IMAGE_SIDE = 1568  # Anthropic 권장 최대 크기 (이 이상은 서버에서 강제 리사이즈되며 토큰 낭비)
_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# 업로드 개수/크기 상한 — 정상 사용 범위를 정밀하게 맞춘 값이 아니라,
# 정상 사용에서는 절대 걸리지 않을 만큼 넉넉하되 실수·이상 입력만 걸러내는 안전판.
_MAX_UPLOAD_FILES = 10  # 공고 스크린샷 여러 장 정도는 통과
_MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 스크린샷/사진 1장 기준 (리사이즈 전 원본)
_MAX_IMAGE_PIXELS = 16_000_000  # 압축률이 높은 이미지의 압축폭탄 방지 — 폰 스크린샷(~460만 픽셀)의 3배 이상 여유


def _resize_image(data: bytes) -> tuple[bytes, str]:
    """이미지를 최대 1568px로 다운샘플링하고 (bytes, media_type) 반환.
    비용 절감 목적 — Anthropic이 이 크기 이상을 강제 리사이즈하기 전에 미리 처리."""
    from PIL import Image as PilImage

    img = PilImage.open(io.BytesIO(data))
    if img.width * img.height > _MAX_IMAGE_PIXELS:
        raise ValueError(f"이미지 해상도가 너무 큽니다 ({img.width}x{img.height}).")
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


# ── 진행 중 표시 ──────────────────────────────────────────────────────────────

def _snapshot_fit_history(slug: str, fit_score, fit_label) -> None:
    """방금 저장된 회사 평가 결과를 이력(SQLite)에 추가한다 — 덮어쓰기 아니라 누적.
    실제 평가가 있었을 때만(fit_score가 있을 때만) 기록하고, 실패해도 회사 저장 자체에는
    영향 주지 않는다."""
    if fit_score is None:
        return
    try:
        content = (settings.companies_dir / f"{slug}.md").read_text(encoding="utf-8")
        create_fit_history_entry(slug, latest_profile_version_id(), fit_score, fit_label, content)
    except Exception as e:
        logger.warning("평가 이력 저장 실패: %s", e)


_in_progress_count = 0


def _track_in_progress(fn):
    """분석 요청 전체(URL 스크래핑·이미지 OCR 같은 선행 구간 포함) 실행 중 건수를 추적한다.
    페이지를 나갔다가 돌아와도(프론트가 리셋돼도) 진행 상태를 조회할 수 있도록 서버에 건수를 남겨둔다.
    FastAPI 라우트 핸들러에 직접 씌우므로 functools.wraps로 원래 시그니처를 보존해야
    FastAPI가 파라미터(요청 바디 등)를 정상적으로 인식한다."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        global _in_progress_count
        _in_progress_count += 1
        try:
            return await fn(*args, **kwargs)
        finally:
            _in_progress_count -= 1
    return wrapper


@router.get("/api/analysis-in-progress")
async def get_analysis_in_progress():
    """진행 중인 회사 분석 건수 — 페이지 이동 후에도 프론트가 배너로 표시할 수 있도록 제공."""
    return {"count": _in_progress_count}


# ── 회사 목록 / 상세 ──────────────────────────────────────────────────────────

@router.get("/api/companies", response_model=list[CompanyMeta])
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


@router.get("/api/companies/export/csv")
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


@router.get("/api/companies/compare")
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


# ── 주간 지원 현황 요약 알림 ───────────────────────────────────────────────────

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


async def weekly_summary_loop():
    """설정된 요일·시각(기본 월요일 09:00)에 지원 현황 요약 알림을 발송한다.
    main.py의 lifespan에서 백그라운드 태스크로 시작한다."""
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


@router.get("/api/companies/timeline")
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


@router.get("/api/companies/{slug}", response_model=CompanyRecord)
async def get_company(slug: str):
    record = storage.read_company(slug)
    if not record:
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다.")
    return record


@router.get("/api/companies/{slug}/fit-history")
async def get_company_fit_history(slug: str):
    """적합도 평가 이력(최신순) — 표시용 요약만, 무거운 원문(content)은 제외."""
    if not storage.read_company(slug):
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다.")
    history = list_fit_history(slug)
    return [
        {
            "id": h["id"],
            "created_at": h["created_at"],
            "profile_version_id": h["profile_version_id"],
            "profile_version_created_at": h["profile_version_created_at"],
            "fit_score": h["fit_score"],
            "fit_label": h["fit_label"],
        }
        for h in history
    ]


_EDIT_FORM_FIELDS = {
    "company_name", "job_title", "source_url",
    "location", "employee_count", "stability", "investment_stage",
    "jobplanet_score", "status",
    "tech_stack", "tags",
}


@router.put("/api/companies/{slug}")
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
    # 원문(.raw.txt)은 안 바뀌지만 RAG의 posting 테이블이 tech_stack/stability/employee_count
    # 등 frontmatter 필드를 그대로 복제해 비교 도구에 노출한다 — 훅이 없으면 수동 재색인 전까지
    # 옛 값이 계속 반환된다(Codex 4차 리뷰로 발견, 2026-08-03). RAG 꺼져 있으면 no-op.
    trigger_reindex_background()
    return record


@router.delete("/api/companies/{slug}")
async def delete_company(slug: str):
    try:
        deleted = storage.delete_company(slug, pre_delete_hook=save_backup_zip)
    except Exception as e:
        logger.error("삭제 전 백업 실패로 삭제 중단 (slug=%s): %s", slug, e)
        raise HTTPException(status_code=500, detail="삭제 전 백업에 실패해 삭제가 취소되었습니다. 잠시 후 다시 시도해주세요.")
    if not deleted:
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다.")
    logger.info("공고 삭제: %s", slug)
    # RAG(opt-in) 자동 재색인 — 삭제된 공고의 고아 임베딩을 prune_deleted_postings()가 정리하도록.
    trigger_reindex_background()
    return {"status": "deleted"}


class SyncWantedRequest(BaseModel):
    source_url: str | None = None


@router.post("/api/companies/{slug}/sync-wanted")
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
    trigger_reindex_background()  # RAG가 복제하는 stability/employee_count 등 갱신, RAG 꺼져 있으면 no-op
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

async def _process_company(
    raw_text: str,
    source_type: str,
    source_url: str | None,
    existing_slug: str | None = None,
    company_name_override: str = "",
    job_title_override: str = "",
    preserve_fm: CompanyFrontmatter | None = None,
    preserved_log_entries: list[dict] | None = None,
    snapshot: LLMSnapshot | None = None,
) -> CompanyRecord:
    """텍스트 → 추출 → 잡플래닛 조회 → 본문 생성 → 적합도 평가 → 저장.

    preserve_fm/preserved_log_entries가 주어지면(refill 시) 사용자가 직접 관리하는
    상태·핀·태그·지원경로·생성일과 지원 상태 로그 이력을 새 분석 결과에 덮어써 보존한다.
    snapshot이 주어지면(이미지 OCR 등 이 함수 호출 전에 이미 다른 LLM 호출을 한 경우) 그 스냅샷을
    그대로 사용해, 호출 전후 단계가 모두 같은 provider로 처리되도록 한다.
    """
    # storage에는 원본 raw_text를 그대로 저장해야 하므로, 프롬프트 삽입용 이스케이프 사본을 별도로 둔다.
    safe_raw_text = prompts.escape_tag_chars(raw_text)

    # 파이프라인 시작 시점에 provider/모델을 스냅샷 떠서 이후 실행 도중 설정이 바뀌어도 섞이지 않게 한다.
    snap = snapshot or capture_snapshot()

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
                system=prompts.evaluate_fit_system(snap.provider_name),
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
                system=prompts.evaluate_fit_system(snap.provider_name),
                user=user_fit + f"\n\n평가 결과 (참고용):\n{json.dumps(fit_result, ensure_ascii=False)}\n\n위 평가 결과를 바탕으로 fit_report_body 전체를 아래 형식에 맞게 작성하세요. ## 4. 적합도 리포트 로 시작하고, ## 5. 종합 의견 (핵심 근거 + 지원 전략)까지 빠짐없이 작성하세요.",
                model=high_model,
                operation="적합도 리포트 본문 생성",
                max_tokens=8192,
                reasoning_effort=snap.reasoning_effort,
            )
            fit_report = re.sub(r'^##\s*4\.\s*적합도 리포트[^\n]*\n+', '', fit_report.strip()).strip()
        else:
            fit_result = await high.extract_structured(
                system=prompts.evaluate_fit_system(snap.provider_name),
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
    _snapshot_fit_history(slug, fm.fit_score, fm.fit_label)

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
    # RAG(opt-in) 자동 재색인 — 공고 원문(.raw.txt)이 방금 바뀌었으니 백그라운드로 반영한다.
    # RAG가 꺼져 있으면 즉시 아무 일도 안 함(4번 "데이터 동기화" 항목).
    trigger_reindex_background()
    return record


@router.post("/api/companies/from-url")
@_track_in_progress
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


@router.post("/api/companies/from-text")
@_track_in_progress
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
        # RAG는 .raw.txt만 원문으로 스캔한다 — 프론트가 실제로 쓰는 경로인데도 이 분기는
        # write_raw_text()/trigger_reindex_background()가 빠져 있어 RAG에서 계속 안 보이고
        # 있었다(add_manual()만 고쳐졌는데 프론트는 그 엔드포인트를 안 씀, Codex 4차 리뷰로
        # 발견, 2026-08-03). body를 원문으로 저장한다 — 자유 텍스트 입력 자체가 없는
        # 최소 등록이라 body가 가장 원문에 가깝다.
        storage.write_raw_text(slug, body)
        record = storage.write_company(slug, fm, body)
        trigger_reindex_background()
        return record
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


@router.post("/api/companies/from-image")
@_track_in_progress
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
    # OCR과 뒤이은 _process_company()가 "이미지 분석 1건"으로 하나의 provider를 쓰도록
    # 여기서 한 번만 스냅샷을 떠서 아래로 그대로 전달한다.
    snap = capture_snapshot()
    high, high_model = high_from_snapshot(snap)
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
            reasoning_effort=snap.reasoning_effort,
        )
    except LLMAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    if not raw_text.strip():
        raise HTTPException(status_code=422, detail="이미지에서 텍스트를 추출할 수 없습니다.")
    logger.info("이미지 텍스트 추출 완료: %d자", len(raw_text))

    try:
        return await asyncio.wait_for(_process_company(raw_text, "image", None, snapshot=snap), timeout=300)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="분석 시간이 초과되었습니다 (300초). 잠시 후 다시 시도해주세요.")
    except LLMAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@router.post("/api/companies/manual")
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
    # RAG(rag/postgres/ingest.py)는 .raw.txt만 원문으로 스캔한다 — 수동 추가는 다른 경로
    # (add_from_url/text/image)와 달리 write_raw_text()를 안 불러서 원래 RAG에서 안
    # 보였다(4번 "데이터 동기화" 항목에서 발견). 수동 입력 시 사용자가 직접 쓴 notes가
    # 가장 원문에 가까우므로 그대로 raw.txt로 저장한다.
    storage.write_raw_text(slug, req.notes)
    record = storage.write_company(slug, fm, body)
    trigger_reindex_background()
    return record


@router.post("/api/companies/{slug}/refill")
@_track_in_progress
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


@router.post("/api/companies/{slug}/pin")
async def toggle_pin(slug: str):
    """즐겨찾기 핀 토글 — pinned 필드만 반전시켜 저장."""
    record = storage.read_company(slug)
    if not record:
        raise HTTPException(status_code=404, detail="회사를 찾을 수 없습니다.")
    fm = record.frontmatter.model_copy(update={"pinned": not record.frontmatter.pinned})
    storage.write_company(slug, fm, record.body)
    logger.info("핀 토글: %s → %s", slug, fm.pinned)
    return {"pinned": fm.pinned}


@router.post("/api/companies/{slug}/refit")
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
                system=prompts.evaluate_fit_system(snap.provider_name),
                user=user_fit,
                tool_name=prompts.EVALUATE_FIT_TOOL_NAME,
                tool_description=prompts.EVALUATE_FIT_TOOL_DESCRIPTION,
                tool_schema=_gemini_fit_schema,
                model=high_model,
                operation="적합도 재평가",
                reasoning_effort=snap.reasoning_effort,
            )
            fit_report_raw = await high.complete(
                system=prompts.evaluate_fit_system(snap.provider_name),
                user=user_fit + f"\n\n평가 결과 (참고용):\n{json.dumps(fit_result, ensure_ascii=False)}\n\n위 평가 결과를 바탕으로 fit_report_body 전체를 아래 형식에 맞게 작성하세요. ## 4. 적합도 리포트 로 시작하고, ## 5. 종합 의견 (핵심 근거 + 지원 전략)까지 빠짐없이 작성하세요.",
                model=high_model,
                operation="적합도 리포트 본문 생성",
                max_tokens=8192,
                reasoning_effort=snap.reasoning_effort,
            )
            fit_report = re.sub(r'^##\s*4\.\s*적합도 리포트[^\n]*\n+', '', fit_report_raw.strip()).strip()
        else:
            fit_result = await high.extract_structured(
                system=prompts.evaluate_fit_system(snap.provider_name),
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
    _snapshot_fit_history(slug, fm.fit_score, fm.fit_label)
    trigger_reindex_background()  # RAG가 복제하는 fit_score/strengths/gaps 갱신, RAG 꺼져 있으면 no-op
    return record
