"""
프롬프트 템플릿과 Tool/Function 스키마 정의.

provider-agnostic 설계: Anthropic tool use와 OpenAI function calling에서
동일한 스키마를 재사용한다. provider별 호출 방식 차이는 llm/anthropic.py,
llm/openai.py에서 처리하므로 이 파일은 내용(What)에만 집중한다.

섹션 구성:
  EXTRACT_COMPANY_*  — Lightweight 티어: 공고 텍스트 → 구조화 JSON
  GENERATE_BODY_*    — Lightweight 티어: 구조화 데이터 → 마크다운 본문 (섹션 1~3)
  EVALUATE_FIT_*     — High 티어: 후보자 프로필 + 공고 → 적합도 점수/리포트 (섹션 4)
  EXTRACT_PROFILE_*  — High 티어: 이력서 PDF → 후보자 프로필
  QA_*               — High 티어: 회사 정보 기반 Q&A 스트리밍 (시스템 프롬프트만; 유저 메시지는 main.py에서 content block으로 구성)

마크다운 본문 섹션 번호 규칙:
  1. 기본정보  2. 회사 규모/안정성  3. 공고 내용
  4. 적합도 리포트 (프로필 있을 때)  5. 지원 상태 로그
  프로필 없을 때: 4. 지원 상태 로그
  섹션 4, 5는 LLM이 아닌 main.py에서 직접 추가한다.
"""

# ── 회사 정보 구조화 추출 (Lightweight 티어) ──────────────────────────────────

EXTRACT_COMPANY_SYSTEM = """당신은 채용공고와 회사 정보를 분석하는 전문가입니다.
주어진 텍스트에서 회사 및 채용 정보를 최대한 정확하게 추출하세요.
텍스트에 명시되지 않은 정보는 반드시 null로 남기세요. 절대 추측하지 마세요."""

EXTRACT_COMPANY_USER_TEMPLATE = """다음 텍스트에서 회사 및 채용공고 정보를 추출해주세요.

<source_text>
{raw_text}
</source_text>

추출 후 extract_company_info 툴(함수)을 호출하여 결과를 제출하세요."""

EXTRACT_COMPANY_TOOL_NAME = "extract_company_info"

EXTRACT_COMPANY_TOOL_DESCRIPTION = "채용공고 텍스트에서 회사 및 채용 정보를 구조화하여 저장합니다."

EXTRACT_COMPANY_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "company_name": {"type": "string", "description": "회사 정식 법인명"},
        "display_name": {"type": "string", "description": "브랜드명 또는 서비스명 (예: 채널톡)"},
        "job_title": {"type": "string", "description": "채용 직무명"},
        "source_url": {"type": ["string", "null"], "description": "공고 URL (있는 경우)"},
        "location": {"type": ["string", "null"], "description": "근무지 (예: 서울 성수)"},
        "employee_count": {"type": ["string", "null"], "description": "임직원 수 범위 (예: 51~200)"},
        "employee_count_meets_threshold": {
            "type": ["boolean", "null"],
            "description": "임직원 50명 이상 여부"
        },
        "stability": {
            "type": ["string", "null"],
            "enum": ["강", "중", "약"],
            "description": "회사 안정성: 강(상장/대기업/시리즈C+/매출확인), 중(시리즈B/성장중), 약(초기/불명확)"
        },
        "investment_stage": {"type": ["string", "null"], "description": "투자 단계 (예: 시리즈C)"},
        "funding_total": {"type": ["string", "null"], "description": "누적 투자금 (예: 누적 400억+)"},
        "revenue_status": {"type": ["string", "null"], "description": "매출 현황 (예: 확인됨, 미확인)"},
        "website": {"type": ["string", "null"], "description": "회사 웹사이트"},
        "industry": {"type": ["string", "null"], "description": "산업/도메인 (예: B2B SaaS / CRM)"},
        "experience_required": {"type": ["string", "null"], "description": "요구 경력 (예: 신입/경력 3년+)"},
        "employment_type": {"type": ["string", "null"], "description": "고용 형태 (예: 정규직)"},
        "salary_min": {"type": ["integer", "null"], "description": "공고에 명시된 최소 연봉 (만원 단위, 없으면 null)"},
        "salary_max": {"type": ["integer", "null"], "description": "공고에 명시된 최대 연봉 (만원 단위, 없으면 null)"},
        "salary_note": {"type": ["string", "null"], "description": "연봉 관련 부가 설명 (예: '협의', '경력별 차등')"},
        "tech_stack": {
            "type": "array",
            "items": {"type": "string"},
            "description": "기술 스택 목록"
        },
        "key_responsibilities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "주요 업무 목록"
        },
        "required_skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "필수 요건 목록"
        },
        "preferred_skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "우대 요건 목록"
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "분류 태그 (예: AI, RAG, 핀테크)"
        },
        "benefits": {
            "type": "array",
            "items": {"type": "string"},
            "description": "복리후생 목록 (예: 스톡옵션, 원격근무, 식대 지원)"
        },
        "hiring_process": {
            "type": "array",
            "items": {"type": "string"},
            "description": "채용 절차 순서 목록 (예: 서류전형, 1차 기술면접, 2차 임원면접)"
        },
    },
    "required": ["company_name", "display_name", "job_title"],
}

# ── 마크다운 본문 생성 (Lightweight 티어) ────────────────────────────────────

GENERATE_BODY_SYSTEM = """당신은 구직자를 위한 회사 분석 문서를 작성하는 전문가입니다.
주어진 구조화 데이터를 바탕으로 읽기 쉬운 한국어 마크다운 문서를 작성하세요.

[중요] 채용공고와 기업 정보에 명시되지 않은 내용은 절대 추측하거나 지어내지 마세요. 제공된 데이터에 없는 항목은 생략하세요."""

GENERATE_BODY_USER_TEMPLATE = """다음 회사 정보를 바탕으로 구조화된 분석 문서를 작성해주세요.

## 회사 정보 (JSON)
{company_json}

## 원문 텍스트
{raw_text}

아래 형식을 반드시 지켜 마크다운 문서를 작성하세요.
값이 없는 행/항목은 생략하세요. 섹션 1~3만 작성하세요.

---

# {{display_name}} ({{company_name}}) — {{job_title}}

## 1. 기본정보

| 항목 | 내용 |
|------|------|
| 회사명 | {{company_name}} |
| 근무지 | {{location}} |
| 경력요건 | {{experience_required}} |
| 고용형태 | {{employment_type}} |
| 분야 | {{industry}} |
| 연봉 | {{salary_min}}~{{salary_max}}만원 또는 {{salary_note}} (없으면 이 행 생략) |

## 2. 회사 규모 / 안정성

| 항목 | 내용 |
|------|------|
| 임직원 수 | {{employee_count}} |
| 투자 단계 | {{investment_stage}} |
| 누적 투자금 | {{funding_total}} |
| 매출현황 | {{revenue_status}} |
| 잡플래닛 | {{jobplanet_score}}점 (리뷰 {{jobplanet_review_count}}개) |
| 안정성 | {{stability}} |

> 📝 {{안정성 판단 근거를 1~2문장으로 서술. 투자단계·매출·임직원 수 근거 포함.}}

## 3. 공고 내용

**기술 스택**: `기술1` `기술2` `기술3` (없으면 이 줄 생략)

**주요 업무**
- 항목1
- 항목2

**필수 요건**
- 항목 (기술명은 `인라인 코드`로 표기)

**우대 요건**
- 항목 (기술명은 `인라인 코드`로 표기)

**복리후생** (없으면 이 항목 전체 생략)
- 항목1

**채용 절차** (없으면 이 항목 전체 생략)
- 1단계
- 2단계

---

문서만 출력하고 다른 설명은 하지 마세요."""

# ── 적합도 평가 (High 티어) ───────────────────────────────────────────────────

EVALUATE_FIT_SYSTEM = """당신은 구직자의 이력서와 채용공고를 비교하여 적합도를 평가하는 전문 커리어 컨설턴트입니다.
후보자 프로필과 채용공고를 꼼꼼히 비교하고, 객관적이고 실용적인 평가를 제공하세요.

[중요] 채용공고·기업 정보·지원자 프로필에 명시된 사실만 근거로 사용하세요. 지원자의 경험이나 기업 정보를 지어내거나 추측하지 마세요."""

EVALUATE_FIT_USER_TEMPLATE = """## 후보자 프로필
{candidate_profile}

## 채용공고 구조화 데이터
{company_json}

## 원문 채용공고
{raw_text}

위 정보를 바탕으로 적합도를 평가하고 evaluate_fit 툴(함수)을 호출하여 결과를 제출하세요.

---

## 점수 산정 우선순위

아래 우선순위 순서로 종합 점수(0~100점)를 산정하세요. 세부 판단은 맥락에 맞게 자율적으로 결정하세요.

1. **필수요건 충족도** — 가장 중요. 핵심 요건 미충족 시 강하게 반영
2. **우대요건 충족도** — 충족 시 가산, 미충족은 소폭만 반영 (우대사항이므로 감점 최소화)
3. **경력 연수** — 요구 경력과 크게 어긋나면 반영. 유사 범위는 유연하게 판단
4. **회사 안정성** — stability 필드 기준 반영
5. **근무지** — 후보자 선호 위치가 설정된 경우만 반영
6. **연봉** — 공고에 명시된 경우만 가산 요소로 참고. 미명시·미달 시 감점 없음
- **직무 경험 연관성** — 참고 요소. 직무 전환 가능성을 고려해 비중 낮게 판단

## 점수 → 라벨 기준

| 점수 | 라벨 |
|------|------|
| 85점 이상 | 강력추천 |
| 70~84점 | 추천 |
| 55~69점 | 조건부추천 |
| 40~54점 | 보류 |
| 39점 이하 | 비추천 |

## fit_report_body 작성 형식

아래 형식을 반드시 지켜 마크다운으로 작성하세요.
값이 없는 행은 생략하세요.

---

## 4. 적합도 리포트

### 자격요건 충족 현황

| 항목 | 충족 여부 | 근거 |
|------|----------|------|
| 요건 항목 | ✅ 충족 또는 ❌ 미충족 | 한 줄 근거 |

### 우대사항 충족 현황

| 항목 | 충족 여부 | 근거 |
|------|----------|------|
| 우대 항목 | ✅ 충족 또는 ❌ 미충족 또는 🔲 불명확 | 한 줄 근거 |

### 직무 적합도 분석

| 주요 업무 | 후보자 경험 연관성 |
|----------|------------------|
| 업무 항목 | 연관 경험 서술 |

### 종합 평가

(2~4줄. 점수와 라벨에 대한 이유를 구체적으로 서술. 핵심 판단 근거 포함)

### 평가 근거

(강점, 갭, 우려 요소를 종합하여 서술. 충족 강점과 부족한 부분의 영향도를 구체적 근거와 함께 서술)

### 지원 전략

(이 공고에 지원한다면 어떤 강점을 어필하고, 부족한 부분을 어떻게 보완·제시할지 구체적으로 서술)

---
{custom_criteria}"""

EVALUATE_FIT_TOOL_NAME = "evaluate_fit"
EVALUATE_FIT_TOOL_DESCRIPTION = "후보자 프로필과 채용공고를 비교한 적합도 평가 결과를 제출합니다."

EVALUATE_FIT_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "fit_score": {"type": "integer", "minimum": 0, "maximum": 100, "description": "적합도 점수 (0~100)"},
        "fit_label": {
            "type": "string",
            "enum": ["강력추천", "추천", "조건부추천", "보류", "비추천"],
            "description": "적합도 라벨"
        },
        "strengths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "후보자 강점. 형식: '(강) 항목명 - 근거 한 줄'. 강도는 항목 맨 앞에 (강)·(중)·(약) 중 하나로 표기. 예: '(강) MLOps 파이프라인 경험 - Airflow·MLflow 실무 구축 이력 보유'"
        },
        "gaps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "부족한 부분. 형식: '(상)·(중)·(하) 항목명 - 근거 한 줄'. 심각도는 항목 맨 앞에 표기. 예: '(상) RAG/벡터검색 경험 없음 - 공고 필수 요건이나 이력서에 언급 없음'"
        },
        "salary_check": {
            "type": "string",
            "enum": ["양호", "미확인", "낮음"],
            "description": (
                "연봉 조건 평가. "
                "양호: 공고에 연봉이 명시되어 있고 후보자 희망 최소 연봉 이상 (가산). "
                "미확인: 공고에 연봉 미명시 (중립, 감점 없음). "
                "낮음: 공고에 연봉이 명시되었으나 희망 최소 연봉 미달 (참고 표시만, 감점 없음)."
            )
        },
        "stability_check": {
            "type": "string",
            "enum": ["충족", "조건부", "미달"],
            "description": "안정성 조건 평가"
        },
        "location_check": {
            "type": "string",
            "description": "위치 조건 평가 (예: 서울 충족)"
        },
        "fit_report_body": {
            "type": "string",
            "description": "적합도 리포트 마크다운 본문 (## 4. 적합도 리포트 섹션 내용)"
        },
    },
    "required": ["fit_score", "fit_label", "strengths", "gaps", "fit_report_body"],
}

# ── 후보자 프로필 추출 (High 티어) ───────────────────────────────────────────

EXTRACT_PROFILE_SYSTEM = """당신은 이력서와 포트폴리오에서 핵심 정보를 추출하는 전문가입니다.
제공된 모든 문서(이력서·포트폴리오·경력기술서 등)를 빠짐없이 검토하여 후보자의 역량, 경력, 희망 조건을 정확히 파악하세요.
명시되지 않은 정보는 null로 처리하고 절대 추측하지 마세요."""

EXTRACT_PROFILE_USER_TEMPLATE = """다음은 후보자의 이력서/포트폴리오 텍스트입니다. 여러 파일이 포함되어 있을 수 있으며, 모든 내용을 종합하여 판단하세요.

<documents>
{pdf_text}
</documents>

{{extra_section}}핵심 정보를 추출하여 extract_candidate_profile 툴(함수)을 호출하세요."""

EXTRACT_PROFILE_TOOL_NAME = "extract_candidate_profile"
EXTRACT_PROFILE_TOOL_DESCRIPTION = "이력서/포트폴리오에서 후보자 프로필 정보를 추출하여 저장합니다."

EXTRACT_PROFILE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "후보자 이름"},
        "tech_skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "기술 스킬 목록 — 언어·프레임워크·라이브러리·도구·인프라 등 (예: Python, React, AWS, Docker)"
        },
        "domains": {
            "type": "array",
            "items": {"type": "string"},
            "description": "경험한 산업/비즈니스 도메인 — 회사명이 아닌 실제 수행한 프로젝트·업무 내용을 기반으로 판단 (예: 핀테크, B2B SaaS, 커머스, 의료, MLOps). 여러 문서를 종합하여 추출"
        },
        "soft_skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "소프트 스킬 목록 — 이력서에 명시된 경우만 추출, 없으면 빈 배열 (예: 리더십, 커뮤니케이션, 문제해결)"
        },
        "experience_years": {"type": ["integer", "null"], "description": "총 경력 연수"},
        "experience_roles": {
            "type": "array",
            "items": {"type": "string"},
            "description": "경험한 직무 역할 목록"
        },
        "education": {"type": ["string", "null"], "description": "학력 (예: 컴퓨터공학 학사)"},
        "preferred_location": {
            "type": "array",
            "items": {"type": "string"},
            "description": "선호 근무지 목록"
        },
        "preferred_employment_type": {"type": ["string", "null"], "description": "선호 고용형태 (예: 정규직)"},
        "preferred_min_salary": {"type": ["integer", "null"], "description": "희망 최소 연봉 (만원 단위)"},
        "summary": {"type": "string", "description": "Q&A 컨텍스트용 후보자 소개 2~3문장. 총 경력 연수·주요 직무·핵심 기술·경험 도메인을 반드시 포함하여 서술. 예: '백엔드 개발 7년차로 Python·FastAPI 기반 B2B SaaS 플랫폼을 주로 개발했습니다. AWS 인프라 운영과 데이터 파이프라인 구축 경험이 있으며, 핀테크 도메인에서 대규모 트랜잭션 처리 시스템을 설계한 이력이 있습니다.'"},
    },
    "required": ["name", "tech_skills", "summary"],
}

# ── 후보자 프로필 본문 생성 (profile_body) — tool use와 분리 ─────────────────────
# tool use는 구조화 추출에 최적화되어 있고, 장문 산문 생성은 complete()가 적합하다.

GENERATE_PROFILE_BODY_SYSTEM = """당신은 구직자의 이력서와 포트폴리오를 분석하여 상세한 프로필 문서를 작성하는 전문가입니다.
주어진 원문 문서, 구조화 추출 정보, 후보자 추가 메모를 모두 종합하여 빠짐없이 반영하세요.
한국어로 작성하세요.

[중요] 할루시네이션 금지: 제공된 문서에 명시되지 않은 내용은 절대 작성하지 마세요.
추측, 가정, 창작은 허용되지 않습니다. 불확실한 내용은 생략하세요."""

GENERATE_PROFILE_BODY_USER_TEMPLATE = """아래 세 가지 소스를 종합하여 후보자 프로필 본문을 작성하세요.

## [소스 1] 이력서/포트폴리오 원문
<documents>
{pdf_text}
</documents>

## [소스 2] 구조화 추출 결과 (툴로 추출한 정보)
<structured_info>
{extracted_json}
</structured_info>

## [소스 3] 후보자 추가 메모
<candidate_note>
{extra_note}
</candidate_note>

---

위 세 소스를 모두 반영하여 아래 형식의 마크다운 본문을 작성하세요.
추가 메모가 비어있으면 해당 항목은 생략하세요.

## 기본 정보
(이름, 학력, 총 경력 연수, 희망 근무지, 희망 고용형태, 희망 최소 연봉 등 — 구조화 추출 결과 기준)

## 경력 요약
(총 경력, 재직 회사 목록, 포지션, 커리어 방향성 등)

## 주요 프로젝트
(각 프로젝트명·기간·역할·사용 기술·성과를 구체적으로 서술)

## 핵심 역량
(기술 스킬, 도메인 지식, 강점을 구체적으로 서술)

## 추가 메모 요약
(후보자가 직접 입력한 추가 정보·맥락·참고사항 — 추가 메모가 있을 때만 작성)

각 섹션을 충분히 상세하게 작성하세요. 마크다운 본문만 출력하고 다른 설명은 하지 마세요."""

# ── Q&A (High 티어) ──────────────────────────────────────────────────────────
# 유저 메시지는 main.py에서 두 content block으로 구성:
#   1. "후보자 프로필 + 회사 정보" — cache_control: ephemeral (캐시 가능 prefix)
#   2. "질문" — 매번 변동

QA_SYSTEM = """당신은 구직자의 개인 커리어 어시스턴트입니다.
후보자 프로필과 회사 정보를 바탕으로 실용적이고 구체적인 답변을 제공하세요.
한국어로 답변하세요.

[중요] 채용공고·기업 정보·지원자 프로필에 명시된 내용만 근거로 사용하세요. 지원자의 없는 경험이나 기업의 확인되지 않은 정보를 지어내거나 추측하지 마세요."""
