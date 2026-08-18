"""
마크다운 파일 기반 저장소.

회사 정보와 후보자 프로필을 YAML frontmatter + 마크다운 본문 형식으로
로컬 파일에 읽고 쓴다. python-frontmatter 라이브러리 사용.

파일 쓰기는 .tmp → os.replace() 순서로 원자적으로 처리해
중간에 프로세스가 죽어도 파일이 손상되지 않는다.
"""
import logging
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import frontmatter
from fastapi import HTTPException

from config import settings
from models import (
    CandidateProfile,
    CandidateRecord,
    CompanyFrontmatter,
    CompanyMeta,
    CompanyRecord,
)

logger = logging.getLogger(__name__)


# ── 슬러그 ────────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    """회사명을 파일명으로 사용 가능한 슬러그로 변환."""
    name = unicodedata.normalize("NFC", name).strip()
    name = re.sub(r'[\\/:*?"<>|]', "", name)  # 윈도우/리눅스 파일명 불가 문자 제거
    name = name.replace(" ", "-")
    return name or "unknown"


def _unique_slug(base: str, exclude: str | None = None) -> str:
    """동일 슬러그 파일이 이미 존재하면 -1, -2 ... 를 붙여 충돌을 회피."""
    slug = base
    path = settings.companies_dir / f"{slug}.md"
    counter = 1
    while path.exists() and slug != exclude:
        slug = f"{base}-{counter}"
        path = settings.companies_dir / f"{slug}.md"
        counter += 1
    return slug


# ── 경로 안전 검사 ────────────────────────────────────────────────────────────

def _safe_company_path(slug: str, suffix: str) -> Path:
    """slug + suffix 로 조합한 경로가 companies_dir 안에 있는지 확인 후 반환.
    ../를 통한 디렉토리 탈출(path traversal)을 차단한다."""
    base = settings.companies_dir.resolve()
    path = (settings.companies_dir / f"{slug}{suffix}").resolve()
    if not path.is_relative_to(base):
        raise HTTPException(status_code=400, detail="유효하지 않은 slug입니다.")
    return path


# ── 회사 파일 ─────────────────────────────────────────────────────────────────

_companies_cache: list[CompanyMeta] = []
_companies_cache_mtime: float = -1.0


def _companies_dir_mtime() -> float:
    """companies/ 디렉토리 자체의 mtime을 반환한다.
    파일 추가·삭제 시 디렉토리 mtime이 갱신되므로 캐시 무효화 신호로 사용한다.
    디렉토리가 없으면 -1 반환."""
    try:
        return settings.companies_dir.stat().st_mtime
    except FileNotFoundError:
        return -1.0


def list_companies() -> list[CompanyMeta]:
    """companies/ 디렉토리의 모든 .md 파일을 읽어 목록을 반환한다.
    디렉토리 mtime이 변하지 않으면 캐시를 그대로 반환한다.
    파싱에 실패한 파일은 경고 로그만 남기고 건너뛴다."""
    global _companies_cache, _companies_cache_mtime

    current_mtime = _companies_dir_mtime()
    if current_mtime == _companies_cache_mtime:
        return _companies_cache

    results: list[CompanyMeta] = []
    for md_path in settings.companies_dir.glob("*.md"):
        try:
            post = frontmatter.load(str(md_path))
            fm = CompanyFrontmatter(**post.metadata)
            slug = md_path.stem
            results.append(CompanyMeta(slug=slug, frontmatter=fm))
        except Exception as e:
            logger.warning("MD 파일 파싱 실패: %s — %s", md_path.name, e)
            continue
    results.sort(key=lambda c: c.frontmatter.updated_at or "", reverse=True)

    _companies_cache = results
    _companies_cache_mtime = current_mtime
    return results


def invalidate_companies_cache() -> None:
    """회사 파일 쓰기 후 캐시를 즉시 무효화한다."""
    global _companies_cache_mtime
    _companies_cache_mtime = -1.0


def read_company(slug: str) -> CompanyRecord | None:
    path = _safe_company_path(slug, ".md")
    if not path.exists():
        return None
    post = frontmatter.load(str(path))
    fm = CompanyFrontmatter(**post.metadata)
    return CompanyRecord(slug=slug, frontmatter=fm, body=post.content)


def write_company(slug: str, fm: CompanyFrontmatter, body: str) -> CompanyRecord:
    """회사 정보를 {slug}.md 파일로 저장한다.
    None 필드는 파일에 기록하지 않아 가독성을 유지한다 (exclude_none=True).
    읽을 때는 Pydantic 기본값으로 채워지므로 데이터 손실 없음."""
    path = _safe_company_path(slug, ".md")
    settings.companies_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec='seconds')
    if not fm.created_at:
        fm.created_at = now
    fm.updated_at = now

    post = frontmatter.Post(content=body, **fm.model_dump(exclude_none=True))
    tmp = _safe_company_path(slug, ".tmp")
    frontmatter.dump(post, str(tmp))
    os.replace(tmp, path)  # 원자적 교체 — 쓰기 도중 크래시가 나도 기존 파일 보존
    invalidate_companies_cache()
    return CompanyRecord(slug=slug, frontmatter=fm, body=body)


def delete_company(slug: str, pre_delete_hook=None) -> bool:
    path = _safe_company_path(slug, ".md")
    if not path.exists():
        return False
    if pre_delete_hook:
        pre_delete_hook()  # 백업 실패 시 예외를 그대로 전파해 삭제를 중단시킨다 (fail-closed)
    path.unlink()
    invalidate_companies_cache()
    raw_path = _safe_company_path(slug, ".raw.txt")
    if raw_path.exists():
        raw_path.unlink()
    return True


def write_raw_text(slug: str, raw_text: str) -> None:
    """회사 추가 시 원문 공고 텍스트를 별도 파일로 보관한다."""
    path = _safe_company_path(slug, ".raw.txt")
    path.write_text(raw_text, encoding="utf-8")


def read_raw_text(slug: str) -> str | None:
    """저장된 원문 텍스트를 반환한다. 없으면 None."""
    path = _safe_company_path(slug, ".raw.txt")
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def make_slug(company_name: str, job_title: str = "") -> str:
    """신규 저장 시 슬러그 생성.
    {company_name}__{job_title} 구조로, __ 로 두 파트를 구분한다."""
    company_part = _slugify(company_name or "company")
    base = f"{company_part}__{_slugify(job_title)}" if job_title else company_part
    return _unique_slug(base)


def make_slug_for_update(slug: str, company_name: str, job_title: str = "") -> str:
    """수정 시 슬러그 생성 — 자기 자신은 충돌에서 제외."""
    company_part = _slugify(company_name or "company")
    base = f"{company_part}__{_slugify(job_title)}" if job_title else company_part
    return _unique_slug(base, exclude=slug)


# ── 후보자 프로필 ─────────────────────────────────────────────────────────────

def read_profile() -> CandidateRecord | None:
    path = settings.candidate_profile_path
    if not path.exists():
        return None
    post = frontmatter.load(str(path))
    fm = CandidateProfile(**post.metadata)
    return CandidateRecord(frontmatter=fm, body=post.content)


def write_profile(fm: CandidateProfile, body: str) -> CandidateRecord:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    fm.updated_at = datetime.now().isoformat(timespec='seconds')

    post = frontmatter.Post(content=body, **fm.model_dump(exclude_none=True))
    tmp = settings.candidate_profile_path.with_suffix(".tmp")
    frontmatter.dump(post, str(tmp))
    os.replace(tmp, settings.candidate_profile_path)
    return CandidateRecord(frontmatter=fm, body=body)


def profile_exists() -> bool:
    return settings.candidate_profile_path.exists()


def read_profile_text() -> str | None:
    """프로필을 High 티어 LLM에 넘길 텍스트 형식으로 반환.
    frontmatter 구조화 데이터 + 마크다운 본문을 합쳐서 단일 문자열로 만든다."""
    record = read_profile()
    if not record:
        return None
    fm_text = "\n".join(
        f"{k}: {v}" for k, v in record.frontmatter.model_dump(exclude_none=True).items()
    )
    return f"---\n{fm_text}\n---\n\n{record.body}"


# ── 평가 기준 ──────────────────────────────────────────────────────────────────

def read_eval_criteria() -> str:
    """사용자 정의 평가 기준 텍스트를 반환한다. 파일이 없으면 빈 문자열."""
    path = settings.data_dir / "eval_criteria.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_eval_criteria(text: str) -> None:
    """사용자 정의 평가 기준을 파일에 저장한다."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.data_dir / "eval_criteria.md"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ── 프로필 추가 설명 ────────────────────────────────────────────────────────────

def read_candidate_note() -> str:
    """프로필 업로드 시 입력하는 추가 설명 텍스트를 반환한다. 파일이 없으면 빈 문자열."""
    path = settings.data_dir / "candidate_note.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_candidate_note(text: str) -> None:
    """프로필 추가 설명을 파일에 저장한다(다음 업로드 때도 기본값으로 남게)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.data_dir / "candidate_note.md"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
