"""헬스체크, provider/모델 설정, 평가 기준, 사용량, 전체 데이터 export."""
import io
import logging
from datetime import date

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

import storage
import usage_tracker
from config import (
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
from export import build_export_zip
from models import SettingsResponse, SettingsUpdateRequest

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 헬스 체크 ────────────────────────────────────────────────────────────────

@router.get("/api/health")
async def health():
    companies = storage.list_companies()
    return {
        "status": "ok",
        "company_count": len(companies),
        "profile_exists": storage.profile_exists(),
        "provider": get_active_provider(),
    }


# ── 설정 ─────────────────────────────────────────────────────────────────────

@router.get("/api/settings", response_model=SettingsResponse)
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


@router.put("/api/settings")
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

@router.get("/api/models")
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
        # Gemini는 API 키를 URL 쿼리 파라미터(?key=...)로 전달하므로, httpx 예외 문자열에
        # 요청 URL 전체(키 포함)가 그대로 들어있을 수 있다. 클라이언트에는 노출하지 않고 서버 로그에만 남긴다.
        logger.warning("모델 목록 조회 실패 (provider=%s): %s", provider, e)
        raise HTTPException(status_code=502, detail=f"모델 목록 조회 실패 ({provider}). 서버 로그를 확인해주세요.")
    return {"provider": provider, "models": model_ids}


# ── 평가 기준 ─────────────────────────────────────────────────────────────────

@router.get("/api/eval-criteria")
async def get_eval_criteria():
    return {"text": storage.read_eval_criteria()}


@router.get("/api/usage")
async def get_usage():
    return usage_tracker.read_usage()


@router.put("/api/eval-criteria")
async def update_eval_criteria(req: dict):
    text = req.get("text", "")
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text 필드는 문자열이어야 합니다.")
    storage.write_eval_criteria(text)
    logger.info("평가 기준 업데이트: %d자", len(text))
    return {"status": "ok"}


# ── 전체 데이터 export ────────────────────────────────────────────────────────

@router.get("/api/export/zip")
async def export_zip(include_pdf: bool = False, include_log: bool = False):
    """선택 옵션에 따라 데이터를 ZIP 파일로 내보낸다."""
    buf = io.BytesIO()
    build_export_zip(buf, include_pdf=include_pdf, include_log=include_log)
    buf.seek(0)
    filename = f"job-fitcheck_backup_{date.today().isoformat()}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
