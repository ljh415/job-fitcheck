# Job FitCheck

구직 활동 중 조사한 회사 정보를 LLM으로 자동 파싱·정리·적합도 평가하고, 로컬 마크다운 파일로 아카이빙하는 웹앱.

## 주요 기능

- **URL / 텍스트 / 이미지 입력** → Claude 또는 ChatGPT가 회사 정보 구조화 추출
  - 원티드·리멤버: URL 자동 스크래핑
  - 사람인·잡코리아 등: 텍스트 붙여넣기 또는 스크린샷 이미지 업로드 (여러 장 지원)
- **잡플래닛 평점 자동 수집** → 네이버 검색 스크래핑으로 평점·리뷰 수 자동 수집
- **적합도 평가** → 내 이력서·포트폴리오(PDF)와 비교해 0~100점 자동 평가 + 커스텀 기준 지원
- **마크다운 아카이빙** → 회사별 `.md` 파일로 로컬 저장, 원문 텍스트 별도 보관
- **즐겨찾기(핀)** → 대시보드 상단 카드 섹션에 중요 회사 고정
- **탈락 공고 숨기기** → 탈락·지원마감 공고 기본 숨김, 토글로 하단 표시
- **비교 테이블** → 여러 회사 사이드바이사이드 비교. 적합도·잡플래닛 최고값 녹색 하이라이트, 기술스택 칩 표시, 강점/갭 스크롤
- **Q&A 채팅** → 저장된 회사 정보 기반으로 면접 준비 등 질문 응답 (스트리밍)
- **인라인 상태 변경** → 목록 및 상세 페이지에서 지원 상태 즉시 변경 (8가지 상태)
- **대시보드 필터링** → 지원 상태·적합도 점수·검색어·정렬 복합 필터 + 핀 아이콘 클릭으로 즐겨찾기만 표시
- **내보내기** → 설정 페이지 `내보내기 ▾` 드롭다운으로 ZIP 백업·전체 CSV·프로필 MD 한 곳에서 관리. 회사 삭제 시 `data/backup/backup_YYYYMMDD_HHMMSS.zip` 자동 백업 (최근 5개 유지)
- **CSV 내보내기** → 대시보드 `CSV` 버튼으로 현재 필터 기준 회사 데이터 다운로드 (Excel 호환)
- **LLM 비용 추적** → 작업별 토큰 사용량 및 비용 로그, 설정 화면에서 조회
- **멀티 LLM 지원** → Claude (Anthropic) / ChatGPT (OpenAI) 런타임 전환
- **지원 현황 타임라인 + 캘린더** → 실제 지원한 회사의 상태 변경 이력을 타임라인(월별 그룹)·캘린더(달력 그리드) 두 뷰로 확인. 상태별 카운트 요약 카드 표시
- **브라우저 히스토리** → 뒤로가기/앞으로가기·URL 직접 접근 지원 (`/`, `/detail/:slug`, `/compare`, `/settings`, `/timeline`)

## 기술 스택

| 영역 | 기술 |
|---|---|
| Backend | FastAPI, Python 3.12 |
| LLM | Anthropic Claude, OpenAI GPT (추상화 레이어로 전환 가능) |
| PDF 추출 | pdfplumber |
| 이미지 처리 | Pillow (서버 사이드 리사이즈) |
| 스크래핑 | httpx + BeautifulSoup4 |
| 저장소 | 로컬 마크다운 파일 (python-frontmatter) |
| Frontend | Vanilla JS SPA (빌드 없음, marked.js) |
| 컨테이너 | Docker Compose (nginx + api) |

## 프로젝트 구조

```
llm-toy-project/
├── backend/
│   ├── main.py              # FastAPI 앱 + 모든 엔드포인트
│   ├── config.py            # 설정 (API 키, provider, 모델 티어)
│   ├── models.py            # Pydantic 데이터 모델
│   ├── storage.py           # 마크다운 파일 read/write
│   ├── scraper.py           # URL 스크래핑 (Wanted 포함)
│   ├── pdf_parser.py        # PDF 텍스트 추출
│   ├── jobplanet.py         # 잡플래닛 평점 수집 (네이버 검색)
│   ├── prompts.py           # LLM 프롬프트 + Tool 스키마
│   ├── usage_tracker.py     # LLM API 비용 추적 (JSONL 로그 + 단가 계산)
│   ├── telegram.py          # 분석 완료 텔레그램 알림 (선택)
│   ├── llm/
│   │   ├── base.py          # LLMProvider 추상 클래스
│   │   ├── anthropic.py     # Claude 구현
│   │   ├── openai.py        # ChatGPT 구현
│   │   └── router.py        # 작업별 모델 티어 선택
│   └── requirements.txt
├── data/
│   ├── companies/           # 회사별 .md + .raw.txt 파일 (자동 생성)
│   ├── uploads/             # 업로드된 원본 PDF
│   ├── backup/              # 자동 백업 (삭제 직전 타임스탬프 ZIP, 최근 5개 유지)
│   ├── candidate_profile.md # 내 프로필 (PDF 업로드 후 자동 생성)
│   ├── eval_criteria.md     # 커스텀 평가 기준 (설정 화면에서 입력)
│   └── usage_log.jsonl      # LLM API 호출 로그 (append-only)
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── style.css
├── nginx.conf
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## 시작하기

### 1. 환경 설정

```bash
cp .env.example .env
```

`.env` 파일에 API 키 입력:

```env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...        # 선택 (Claude만 써도 됨)
DEFAULT_PROVIDER=claude
```

### 2. 실행

**Docker (권장)**

```bash
docker compose up --build
```

nginx(프론트 서빙) + api(백엔드) 두 컨테이너로 구성됩니다.

**로컬 직접 실행**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
python3 backend/main.py
```

브라우저에서 `http://localhost:8000` 접속

### 3. 첫 사용 순서

1. **설정** 탭 → 이력서/포트폴리오 PDF 업로드 → 프로필 자동 생성
2. **회사 추가** → URL 붙여넣기 또는 공고 텍스트 입력 → AI 분석
3. **대시보드** → 회사 목록 확인, 상태 변경, 비교, Q&A

## LLM 모델 티어

작업 성격에 따라 자동으로 적절한 모델을 선택합니다.

| 작업 | 티어 | Claude | GPT |
|---|---|---|---|
| 공고 텍스트 구조화 추출 | Lightweight | claude-haiku-4-5 | gpt-4o-mini |
| 마크다운 본문 생성 | Lightweight | claude-haiku-4-5 | gpt-4o-mini |
| PDF → 후보자 프로필 추출 | High | claude-sonnet-4-6 | gpt-4o |
| 적합도 평가 (이력서 비교) | High | claude-sonnet-4-6 | gpt-4o |
| Q&A 채팅 | High | claude-sonnet-4-6 | gpt-4o |

설정 화면에서 모델을 수동으로 변경할 수 있습니다.

## API 엔드포인트

```
# 후보자 프로필
POST   /api/profile/upload        PDF 업로드 → 프로필 자동 생성 (form: files, extra_note, max_tokens)
GET    /api/profile               프로필 조회
PUT    /api/profile               프로필 수동 수정
GET    /api/profile/status        프로필 존재 여부
GET    /api/profile/export        분석 완료된 프로필 마크다운 파일 다운로드

# 회사 관리
POST   /api/companies/from-url    URL로 회사 추가 (중복 URL 시 409 + slug 반환)
POST   /api/companies/from-text   텍스트로 회사 추가 (회사명·직무명 필수, 텍스트 없으면 수동 저장)
POST   /api/companies/from-image  이미지(스크린샷) 업로드로 공고 추출 후 추가
GET    /api/companies             전체 목록
GET    /api/companies/{slug}      상세 조회
PUT    /api/companies/{slug}      수정
DELETE /api/companies/{slug}      삭제
GET    /api/companies/compare     ?slugs=a,b,c 비교 (최대 5개, 초과 시 422)
POST   /api/companies/{slug}/qa   Q&A (스트리밍)
POST   /api/companies/qa          다중 회사 Q&A (스트리밍)
POST   /api/companies/{slug}/refill      AI 재분석 (저장된 원문 기반)
POST   /api/companies/{slug}/refit       적합도만 재평가 (High 티어 1회)
POST   /api/companies/{slug}/pin         즐겨찾기 토글
POST   /api/companies/{slug}/sync-wanted Wanted 기업 정보 동기화
GET    /api/companies/export/csv         전체 회사 데이터 CSV 다운로드
GET    /api/companies/timeline          상태 로그 기반 타임라인 데이터 (전 회사)

# 내보내기
GET    /api/export/zip                   데이터 ZIP 백업 (?include_pdf=true&include_log=true)

# 적합도 평가 기준
GET    /api/eval-criteria         커스텀 평가 기준 조회
PUT    /api/eval-criteria         커스텀 평가 기준 저장

# 시스템
GET    /api/health                상태 확인
GET    /api/settings              현재 설정 조회
PUT    /api/settings              Provider / 모델 변경
GET    /api/usage                 LLM API 사용 이력 조회
```

## 데이터 저장 형식

회사 정보는 YAML frontmatter + 마크다운 본문으로 저장됩니다.  
파일명(slug)은 `{회사명}__{직무명}` 형식입니다 (예: `쏘카__Applied-Research-Scientist.md`).  
원문 공고 텍스트는 `{slug}.raw.txt`로 별도 보관해 재분석 품질을 유지합니다.

```markdown
---
company_name: 채널코퍼레이션
job_title: Applied AI Engineer
jobplanet_score: 3.9
jobplanet_review_count: 42
fit_score: 72
fit_label: 추천
status: 미지원
pinned: false
tech_stack: [Python, PyTorch, RAG]
...
---

# 채널톡 — Applied AI Engineer

## 1. 기본정보
...
```

`data/` 폴더를 git으로 관리하거나 NAS에 두면 어디서든 동기화됩니다.

## 변경 이력

전체 버전별 변경 내용은 [CHANGELOG.md](CHANGELOG.md)를 참고하세요.
