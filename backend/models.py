"""
Pydantic 데이터 모델.

CompanyFrontmatter  — 회사 MD 파일의 YAML frontmatter 전체 스키마
CandidateProfile    — 후보자 프로필 MD 파일의 YAML frontmatter
API 요청/응답 모델  — 각 엔드포인트의 입출력 타입
"""
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator


# ── 회사 frontmatter ──────────────────────────────────────────────────────────

class CompanyFrontmatter(BaseModel):
    # 기본 메타
    company_name: str = ""
    display_name: str = ""          # 브랜드명 (예: 채널톡)
    job_title: str = ""
    source_url: str | None = None
    source_type: Literal["url", "text_paste", "manual", "image"] = "manual"
    created_at: str = ""
    updated_at: str = ""
    llm_provider: str = ""          # 분석에 사용된 provider 기록 (재현성)

    # 회사 정보 — Lightweight 티어 LLM이 공고 텍스트에서 추출
    location: str | None = None
    employee_count: str | None = None
    employee_count_meets_threshold: bool | None = None  # 50명 이상 여부
    stability: Literal["강", "중", "약"] | None = None
    investment_stage: str | None = None
    funding_total: str | None = None
    revenue_status: str | None = None
    # jobplanet_score는 LLM 추출 대상이 아님 — jobplanet.py가 별도 수집
    jobplanet_score: float | None = None
    jobplanet_review_count: int | None = None
    website: str | None = None
    industry: str | None = None
    salary_min: int | None = None   # 공고 명시 최소 연봉 (만원)
    salary_max: int | None = None   # 공고 명시 최대 연봉 (만원)
    salary_note: str | None = None  # "협의", "경력별 차등" 등

    # 채용 공고 정보 — Lightweight 티어 추출
    experience_required: str | None = None
    employment_type: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    key_responsibilities: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    hiring_process: list[str] = Field(default_factory=list)

    # 적합도 — High 티어 LLM이 후보자 프로필과 공고를 비교해서 생성
    fit_score: int | None = None    # 0~100
    fit_label: str | None = None    # 강력추천 / 추천 / 조건부추천 / 보류 / 비추천
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    salary_check: str | None = None     # 양호 / 미확인 / 낮음
    stability_check: str | None = None  # 충족 / 조건부 / 미달
    location_check: str | None = None

    # 지원 현황 — 사용자가 직접 업데이트
    status: Literal["미지원", "지원", "서류통과", "인터뷰", "최종", "탈락", "보류", "지원마감"] = "미지원"
    application_source: str | None = None  # 지원 경로 (예: 원티드, 링크드인)
    tags: list[str] = Field(default_factory=list)
    pinned: bool = False  # 즐겨찾기 여부

    @field_validator(
        "tech_stack", "key_responsibilities", "required_skills",
        "preferred_skills", "benefits", "hiring_process",
        "strengths", "gaps", "tags",
        mode="before",
    )
    @classmethod
    def coerce_null_to_list(cls, v: Any) -> list:
        if v is None or v == "null":
            return []
        return v


class CompanyRecord(BaseModel):
    """상세 조회용 — frontmatter + 마크다운 본문."""
    slug: str
    frontmatter: CompanyFrontmatter
    body: str = ""


class CompanyMeta(BaseModel):
    """목록 뷰용 — frontmatter만, 본문 없음 (응답 크기 최소화)."""
    slug: str
    frontmatter: CompanyFrontmatter


# ── 후보자 프로필 frontmatter ─────────────────────────────────────────────────

class CandidateProfile(BaseModel):
    """이력서/포트폴리오 PDF에서 High 티어 LLM이 추출하는 후보자 정보."""
    name: str = ""
    updated_at: str = ""
    source_files: list[str] = Field(default_factory=list)  # 업로드된 PDF 파일명 목록

    tech_skills: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    experience_years: int | None = None
    experience_roles: list[str] = Field(default_factory=list)
    education: str | None = None

    # 희망 조건 — 적합도 평가 시 salary_check, location_check 판단 기준
    preferred_location: list[str] = Field(default_factory=list)
    preferred_employment_type: str | None = None
    preferred_min_salary: int | None = None  # 만원 단위

    summary: str = ""   # Q&A 컨텍스트용 2~3문장 요약


class CandidateRecord(BaseModel):
    frontmatter: CandidateProfile
    body: str = ""


# ── API 요청/응답 ─────────────────────────────────────────────────────────────

class FromUrlRequest(BaseModel):
    url: str


class FromTextRequest(BaseModel):
    company_name: str
    job_title: str
    source_url: str | None = None
    # 정상 공고 텍스트(수 KB)보다 훨씬 넉넉하게 잡아, 실수로 문서 전체를 붙여넣는 경우만 차단
    text: str = Field("", max_length=100_000)


class ManualCompanyRequest(BaseModel):
    company_name: str
    display_name: str = ""
    job_title: str = ""
    source_url: str | None = None
    notes: str = ""


class CompanyUpdateRequest(BaseModel):
    frontmatter: CompanyFrontmatter
    body: str = ""


class QAMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=20_000)


class QARequest(BaseModel):
    question: str = Field(max_length=2_000)
    history: list[QAMessage] = Field(default_factory=list, max_length=40)


class MultiQARequest(BaseModel):
    slugs: list[str] = Field(max_length=5)
    question: str = Field(max_length=2_000)
    history: list[QAMessage] = Field(default_factory=list, max_length=40)


class SettingsResponse(BaseModel):
    provider: str
    claude_high_model: str
    claude_light_model: str
    openai_high_model: str
    openai_light_model: str
    openai_reasoning_effort: str
    gemini_high_model: str
    gemini_light_model: str
    notify_strengths: bool
    notify_gaps: bool
    notify_jobplanet_rating: bool
    notify_employee_count: bool


class SettingsUpdateRequest(BaseModel):
    provider: str | None = None
    claude_high_model: str | None = None
    claude_light_model: str | None = None
    openai_high_model: str | None = None
    openai_light_model: str | None = None
    openai_reasoning_effort: str | None = None
    gemini_high_model: str | None = None
    gemini_light_model: str | None = None
    notify_strengths: bool | None = None
    notify_gaps: bool | None = None
    notify_jobplanet_rating: bool | None = None
    notify_employee_count: bool | None = None


class ProfileUpdateRequest(BaseModel):
    frontmatter: CandidateProfile
    body: str = ""


class LoginRequest(BaseModel):
    password: str
