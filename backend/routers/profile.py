"""후보자 프로필: PDF 업로드 → LLM 추출 → candidate_profile.md 생성/조회/수정."""
import json
import logging
from datetime import date
from pathlib import Path

import frontmatter
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from services import pdf_parser
import prompts
import storage
from config import settings
from llm.base import LLMAPIError
from llm.router import capture_snapshot, high_from_snapshot
from models import CandidateProfile, CandidateRecord, ProfileUpdateRequest, ProfileVersionNoteRequest
from routers.rag import trigger_reindex_background
from services.app_db import (
    create_profile_version,
    delete_profile_version,
    get_profile_version,
    list_profile_versions,
    update_profile_version_note,
)
from services.pdf_parser import PDFExtractError

logger = logging.getLogger(__name__)
router = APIRouter()

# 업로드 개수/크기 상한 — 정상 사용 범위를 정밀하게 맞춘 값이 아니라,
# 정상 사용에서는 절대 걸리지 않을 만큼 넉넉하되 실수·이상 입력만 걸러내는 안전판.
_MAX_UPLOAD_FILES = 10  # 이력서+포트폴리오 여러 개 정도는 통과
_MAX_PDF_BYTES = 30 * 1024 * 1024  # 이미지가 많은 포트폴리오 PDF도 통과하는 수준


def _snapshot_profile(note: str | None = None) -> None:
    """방금 저장된 candidate_profile.md를 프로필 히스토리(SQLite)에 스냅샷으로 남긴다.
    note는 사용자가 남긴 짧은 메모(선택). 실패해도 프로필 저장 자체(핵심 동작)에는
    영향 주지 않는다."""
    try:
        content = settings.candidate_profile_path.read_text(encoding="utf-8")
        create_profile_version(content, note=(note.strip() or None) if note else None)
    except Exception as e:
        logger.warning("프로필 스냅샷 저장 실패: %s", e)


@router.get("/api/profile/status")
async def profile_status():
    return {"exists": storage.profile_exists()}


@router.get("/api/profile")
async def get_profile():
    record = storage.read_profile()
    if not record:
        raise HTTPException(status_code=404, detail="프로필이 없습니다. PDF를 업로드해주세요.")
    return record


@router.get("/api/profile/export")
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


@router.get("/api/profile/versions")
async def list_profile_version_history():
    """프로필 스냅샷 목록(최신순) — id/시점만. 내용은 상세 조회(GET .../{id})에서."""
    return list_profile_versions()


@router.get("/api/profile/versions/{version_id}")
async def get_profile_version_detail(version_id: int):
    """특정 시점 프로필 스냅샷 전체(현재 `GET /api/profile`과 같은 형태)."""
    v = get_profile_version(version_id)
    if not v:
        raise HTTPException(status_code=404, detail="해당 버전을 찾을 수 없습니다.")
    post = frontmatter.loads(v["content"])
    fm = CandidateProfile(**post.metadata)
    record = CandidateRecord(frontmatter=fm, body=post.content)
    return {**record.model_dump(), "note": v["note"]}


@router.patch("/api/profile/versions/{version_id}")
async def update_profile_version_note_endpoint(version_id: int, req: ProfileVersionNoteRequest):
    note = (req.note.strip() or None) if req.note else None
    if not update_profile_version_note(version_id, note):
        raise HTTPException(status_code=404, detail="해당 버전을 찾을 수 없습니다.")
    return {"status": "ok", "note": note}


@router.delete("/api/profile/versions/{version_id}")
async def delete_profile_version_endpoint(version_id: int):
    if not delete_profile_version(version_id):
        raise HTTPException(status_code=404, detail="해당 버전을 찾을 수 없습니다.")
    return {"status": "deleted"}


@router.put("/api/profile")
async def update_profile(req: ProfileUpdateRequest):
    record = storage.write_profile(req.frontmatter, req.body)
    logger.info("프로필 수동 업데이트 완료")
    _snapshot_profile()
    # RAG_INCLUDE_PROFILE=true면 프로필도 임베딩 대상 — 훅이 없으면 gap 도구가 옛 프로필
    # 임베딩을 계속 쓴다(Codex 4차 리뷰로 발견, 2026-08-03). RAG 꺼져 있으면 no-op.
    trigger_reindex_background()
    return record


@router.post("/api/profile/upload")
async def upload_profile(files: list[UploadFile] = File(...), extra_note: str = Form(""), max_tokens: int = Form(8192), version_note: str = Form("")):
    """PDF 업로드 → pdfplumber 추출 → High 티어 LLM → candidate_profile.md 생성."""
    max_tokens = min(max_tokens, 32768)
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

    # provider/모델뿐 아니라 reasoning_effort도 1·2단계 사이에 안 바뀌도록 스냅샷을 떠서 재사용한다.
    snap = capture_snapshot()
    provider, model = high_from_snapshot(snap)
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
            reasoning_effort=snap.reasoning_effort,
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
            reasoning_effort=snap.reasoning_effort,
        )
    except LLMAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    logger.info("프로필 본문 생성 완료: %d자", len(body))

    fm = CandidateProfile(**result, source_files=filenames)
    record = storage.write_profile(fm, body)
    logger.info("프로필 저장 완료: %s", settings.candidate_profile_path)
    _snapshot_profile(version_note)
    trigger_reindex_background()
    return record
