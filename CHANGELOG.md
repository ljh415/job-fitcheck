# Changelog

## v1.4.2 — 프로필 히스토리 후속 버그 2건 수정 (2026-08-17)

v1.4.0(프로필 히스토리) merge 후 실사용 중 발견된 버그 2건 수정.

**`backend/services/app_db.py`**
- 프로필 스냅샷 소급 백필 누락: 회사 평가 이력(`fit_history`)만 기존 값을
  첫 이력으로 소급 적용했고 프로필 스냅샷(`profile_versions`)은 대응 로직이
  없어서, 기존에 업로드해둔 이력서가 있어도 "이전 버전 0개"로 시작했음 —
  `_backfill_profile_version()` 추가
- Codex 코드리뷰로 추가 발견: "테이블이 비었는지"만으로 판단하면 사용자가
  마지막 스냅샷을 명시적으로 삭제해도 다음 재시작에 되살아남 — SQLite 내장
  `sqlite_sequence`(AUTOINCREMENT 최고값, 행을 다 지워도 기록은 남음)로
  "한 번도 없었음"과 "사용자가 다 지웠음"을 구분하도록 수정

**`frontend/app.js`**
- 평가 이력 토글 버튼(상단)을 눌러도 패널(본문 하단)이 조용히 열려서
  스크롤하지 않으면 안 보이던 문제 — `scrollIntoView()`로 열 때 자동으로
  보이도록 수정

**검증**
- prod 실데이터로 기존 프로필 자동 백필(0→1건) 확인, 삭제 후 재시작해도
  안 되살아나는 것 확인, Playwright로 스크롤 동작 확인
- Codex 코드리뷰 1라운드(중간 등급 1건 발견·수정)

## v1.4.1 — 잡플래닛 평점 조회 매칭 로직 개선 (2026-08-17)

카카오처럼 리뷰가 많은 회사도 잡플래닛 평점이 `not_found`로 잘못 처리되던
버그 수정. RAG merge 후 회귀 테스트 중 발견(2026-08-15), merge와는 무관한
기존 버그.

**`backend/services/jobplanet.py`**
- 원인: `_search_naver()`가 Naver 검색 결과 JSON `"title"` 필드(최대 300자)를
  정규식으로 파싱했는데, Naver가 이 텍스트를 매번 다른 지점에서 잘라버려
  평점 부분이 사라지는 경우가 많았음
- 별도로 안정적으로 렌더링되는 평점 블록(`class="fds-listitem"`, "평점
  X.X/5 N 참여")을 BeautifulSoup으로 파싱하도록 교체 — 상위 "...기업정보"
  링크에서 회사명을 페어링해 기존 유사도 매칭 로직은 그대로 재사용
- Codex 코드리뷰로 추가 발견: 자기 카드에 링크가 없는 평점 블록이 옆 카드의
  링크를 잘못 가져와 틀린 회사명과 페어링되는 문제 — 조상에 평점 블록이
  2개 이상 있으면 카드 경계를 넘은 것으로 보고 포기하도록 수정
- 파싱 로직을 순수 함수로 분리해 네트워크 없이 fixture로 회귀 검증 가능하게 함

**검증**
- 실제 Naver 요청으로 카카오(이전엔 오탐)·에너닷(소규모)·괄호 붙은 회사명
  (딥파인(DEEP.FINE))·미등록 가상 회사 검증, 실제 회사 등록 API 엔드투엔드 확인
- Codex 코드리뷰 1라운드(중간 등급 1건 발견·수정)

## v1.4.0 — 프로필 스냅샷 & 회사별 적합도 평가 히스토리 (2026-08-17)

이력서를 새로 올리거나 수정할 때마다 그 시점의 프로필을 스냅샷으로 보관하고, 회사별
적합도 평가(최초 등록·재분석·refit)를 덮어쓰지 않고 이력으로 누적한다. 각 평가 이력이
어느 프로필 스냅샷을 기준으로 했는지 연결돼 있어, "사이드 프로젝트를 추가한 뒤 점수가
몇 점 올랐는지" 같은 걸 추적할 수 있다.

**`backend/services/app_db.py`** (신규)
- `data/app.db`(SQLite, 단일 파일) — `profile_versions`(스냅샷+사용자 메모), `fit_history`(회사별 평가 이력) 두 테이블
- RAG의 Postgres는 opt-in이라 핵심 기능이 거기 의존하면 안 된다는 이유로 별도 채택 — 별도 서버 없는 로컬 파일 하나라 "DB 없음" 원칙과 어긋나지 않음
- 기존 회사(이력 없음, `fit_score`만 있음)는 `init_db()`가 앱 시작마다 자동으로 소급 백필(회사별 idempotent, 손상된 회사 파일 하나가 있어도 나머지는 정상 처리)
- DB 장애(권한/손상 등) 시 `is_healthy()`로 상태를 남겨 핵심 기능(회사 CRUD)은 계속 동작하되, 이력 조회 API는 "0건"이 아니라 503으로 명확히 응답

**`backend/routers/profile.py` / `routers/companies.py`**
- 프로필 업로드/수동편집 시 자동 스냅샷(업로드 시 메모 입력 가능, 목록에서 클릭해 나중에 메모 추가/수정도 가능)
- 회사 최초 등록/refill/refit 시 자동으로 평가 이력 추가 — 평가에 실제로 사용한 프로필과 다른 스냅샷이 잘못 연결되지 않도록, 평가 시작 시점에 내용까지 대조해서 스냅샷 id를 고정
- 회사 삭제 시 그 회사의 평가 이력도 함께 삭제(삭제 전 자동 백업엔 남음) — 같은 이름으로 재등록해도 옛 이력이 다시 붙지 않도록

**`backend/export.py`**
- 전체 백업 ZIP·삭제 전 자동 백업에 `app.db` 필수 포함

**`frontend/`**
- 설정 화면에 "이전 버전" 목록(메모 인라인 편집) + 버전 상세 뷰어
- 공고 상세 페이지에 "평가 이력 보기" 토글 + 표(점수 변화량 표시, 이전 버전/이력 상세로 이동) — DB 장애 시 "이력 없음"과 구분되는 오류 표시

**검증**
- Codex 코드리뷰 5라운드(총 9건 발견 전부 해결) + 실제 Docker 컨테이너에서 정상/장애(DB 손상·read-only) 양쪽 시나리오 실측 검증

## v1.3.0 — RAG(Agentic RAG) 채팅 기능 추가, 선택 기능 (2026-08-15)

등록된 채용공고 전체를 근거로 자연어로 질문하는 대화형 채팅 기능. `rag/main` 브랜치에서
별도로 개발·안정화한 뒤 완성된 코드만 `feat/rag-integration-plan`에 재작성해 `main`에
합류시켰다. 기본은 꺼져 있고, `RAG_POSTGRES_HOST` 설정 시에만 켜지는 opt-in 구조 — 기존
"DB 없음"(회사 정보는 마크다운 파일) 원칙은 그대로 유지되고, RAG는 그 위에 얹히는 선택
계층이다.

**`backend/rag/`** (신규 패키지)
- PostgreSQL + pgvector 기반 청킹·임베딩·검색·Agent(tool-use) 파이프라인
- 임베딩 provider: Google(기본, 메인 앱 키 재사용) 또는 개인 GPU 서버(SSH, opt-in) 중 활성 provider 하나만 사용
- Agent가 Claude/OpenAI/Gemini 3개 provider 전부 지원(메인 앱의 현재 provider 설정을 그대로 따라감)
- 회사 등록/편집/삭제, 프로필 수정/업로드 시 자동 재색인 훅 연결(동시 트리거는 락+pending 큐로 직렬화)
- 이력서(프로필) 내용은 `RAG_INCLUDE_PROFILE`(기본 false)을 켜야만 임베딩 API로 전송됨

**`docker-compose.yml` / `docker/Dockerfile`**
- `rag-postgres`(pgvector) 서비스를 `profiles: ["rag"]`로 추가 — `docker compose --profile rag up`으로만 뜸
- `openssh-client` 설치 단계 추가(로컬 임베딩 provider의 SSH 터널용)

**`frontend/`**
- 네비게이션에 RAG 진입 버튼 신규(`GET /api/rag/status`로 조건부 노출), `/rag` 뷰(기술 갭 확인 폼 + 멀티세션 채팅 패널) 추가

**`backend/requirements.txt`**
- `google-genai` 1.2.0→2.15.0, `httpx` 0.27.2→0.28.1, `openai` 1.54.0→1.109.1, `pydantic` 2.9.2→2.13.4 (RAG 쪽 요구사항으로 상향, 기존 기능도 이 버전으로 재검증됨)
- `cryptography<49` 핀 추가 — 49부터 macOS Intel(x86_64) wheel 배포가 중단돼 Rust 빌드 환경 없이는 설치가 안 되는 문제 방지

**문서**
- `RAG_GUIDE.md` 신설(사용자 가이드), `backend/rag/README.md`(코드 구조), `.env.example`/`docker-compose.yml`에 로컬 임베딩 provider 설정법(SSH 키 볼륨 마운트 등) 추가

**검증**
- Codex 코드리뷰 5차(총 23건 발견, 22건 해결) + merge 후 전체 앱 회귀 체크리스트(`docs/regression_testing_checklist.md`, 54개 항목) + RAG 전용 체크리스트(`docs/rag_testing_checklist.md`, 25개 항목)로 실사용 시나리오 실측 검증
- merge와 무관한 기존 버그 1건(Jobplanet 평점 매칭 로직) 발견·기록, 후속 개선 예정

## v1.2.4 — provider/모델/알림설정이 재시작 후에도 유지되도록 수정 (2026-07-21)

**`backend/config.py`**
- provider·모델 오버라이드·알림 설정·주간요약 스케줄이 서버 재시작 시 `.env` 기본값으로 리셋되던 문제 수정 — `data/runtime_settings.json`에 원자적으로 저장(`.tmp` → `os.replace()`)하고 앱 시작 시 불러오도록 변경. 기존 "PoC라 리셋되는 게 의도된 동작"이라는 설명은 실제로는 이 상태만 영속화를 안 해놨던 것뿐이라, 다른 데이터(회사·프로필 등)와 동일한 원자적 파일 쓰기 패턴을 그대로 적용
- `data/`는 이미 git 미추적 대상이라 이 파일도 자동으로 추적 제외됨

dev/prod 양쪽에서 provider를 변경한 뒤 컨테이너를 재시작(재빌드 아님)해 값이 유지되는지, 회사 데이터가 안전한지 실제로 확인.

## v1.2.3 — backend 서비스 모듈 패키징 (2026-07-21)

기능 변경 없음 — `usage_tracker.py`/`pdf_parser.py`/`scraper.py`/`jobplanet.py`를 `backend/services/`로 이동.
서로 코드 공유는 없지만 "라우터에서 호출되는 단일 서비스 모듈"이라는 공통 역할로 분류. `config.py`/`models.py`/`storage.py` 등 앱 전역에서 쓰이는 핵심 모듈은 최상위에 그대로 유지.

**호출부 import 수정**
- `llm/anthropic.py`, `llm/openai.py`, `llm/gemini.py`, `routers/settings.py`: `import usage_tracker` → `from services import usage_tracker`
- `routers/companies.py`: `import scraper` → `from services import scraper`, `from jobplanet import ...` → `from services.jobplanet import ...`
- `routers/profile.py`: `import pdf_parser` → `from services import pdf_parser`

dev/prod 양쪽 Docker 재빌드 후 앱 임포트로 라우트 36개 동일 여부 재확인, `/api/usage`·`/api/companies`(scraper/jobplanet 임포트 체인 경유) 실제 요청으로 검증.

## v1.2.2 — backend 모듈 구조 리팩터링 (2026-07-21)

기능 변경 없음(API 경로·동작 전부 동일) — `main.py` 1개 파일(1400여 줄)에 몰려있던 코드를 역할별로 분리.

**`backend/notify/`** (신규 패키지, 기존 `notify.py`/`notify_format.py`/`discord.py`/`slack.py`/`telegram.py`를 이동)
- `llm/` 패키지와 동일한 패턴으로 알림 채널 코드를 하나로 묶음

**`backend/auth.py`** (신규)
- JWT 발급/검증, 인증 미들웨어, `POST /api/login`

**`backend/export.py`** (신규)
- 전체 데이터 ZIP 백업/export 공용 유틸(`routers/settings.py`의 `/api/export/zip`, `routers/companies.py`의 삭제 전 자동 백업에서 공유)

**`backend/routers/`** (신규 패키지)
- `settings.py` — 헬스체크, provider/모델 설정, 평가 기준, 사용량
- `profile.py` — 후보자 프로필(PDF 업로드 → LLM 추출)
- `companies.py` — 회사 CRUD, 회사 추가 파이프라인(`_process_company`), 주간 요약, 원티드 동기화
- `qa.py` — Q&A SSE 스트리밍

**`backend/prompts.py`**
- `_evaluate_fit_system()`을 `main.py`에서 이동해 `evaluate_fit_system()`으로 공개 함수화 (프롬프트 선택 로직이 프롬프트 정의와 같은 파일에 있는 게 더 자연스러움)

**`backend/main.py`**
- 1436줄 → 53줄. `FastAPI` 앱 생성, 미들웨어·라우터 등록, 정적 파일 마운트만 남김

라우트 32개 전부 경로·순서(특히 `/api/companies/compare`·`/api/companies/timeline`이 `/api/companies/{slug}`보다 먼저 등록되는 순서) 동일하게 유지 검증. dev/prod 양쪽 Docker 재빌드 후 로그인·설정·회사 목록/상세/타임라인/비교·인증 미들웨어·이미지 업로드 거부까지 실제 컨테이너에 요청을 보내 확인.

## v1.2.1 — public 전환 전 보안 점검 3건 수정 (2026-07-21)

저장소를 private → public으로 전환하기 전 실시한 코드 리뷰에서 발견된 경미한 보안 이슈 수정.

**`backend/main.py`**
- 이미지 업로드 시 압축폭탄(decompression bomb) 방지 — `_resize_image()`에서 픽셀 수(가로×세로) 상한(1,600만)을 넘는 이미지를 열기 전에 거부. 기존에는 업로드 파일 바이트 크기만 제한하고 압축 해제 후 메모리 사용량은 무방비였음
- 로그인 비밀번호 비교를 `hmac.compare_digest()`로 변경 — 기존 문자열 `!=` 비교는 상수시간이 아니라 이론적으로 타이밍 공격에 노출됨
- `GET /api/models` 실패 시 예외 메시지를 클라이언트에 노출하지 않고 서버 로그로만 남김 — Gemini는 API 키를 URL 쿼리 파라미터(`?key=...`)로 전달해, 요청 실패 시 `httpx` 예외 문자열에 키가 그대로 포함되어 클라이언트 응답으로 노출되던 문제

## v1.2.0 — Docker 없이 `uv`로 실행하는 대안 경로 추가 (2026-07-20)

**`backend/main.py`**
- `frontend/` 폴더가 존재할 때만 FastAPI가 직접 정적 파일을 서빙하도록 조건부 마운트 추가 — Docker 이미지에는 `frontend/`가 없어 기존 동작(nginx가 서빙) 그대로 유지되고, 로컬/uv 실행 시에만 활성화됨
- 인증 미들웨어 적용 범위를 `/api/*`로 제한 — 정적 파일까지 인증이 걸려 로컬 실행 시 화면 자체가 401로 막히던 문제 수정 (Docker는 nginx가 애초에 정적 파일을 백엔드로 안 넘겨 영향 없었음)

**`run/`** (스크립트 재구성)
- `start-uv.command`/`start-uv.bat` 신규 추가 — Docker 없이 `uv run --python 3.12 --with-requirements backend/requirements.txt backend/main.py`로 실행. `uv` 미설치 시 설치 여부를 묻고(Y/n, 기본 Y) 자동 설치 후 이어서 실행
- `setup.command`/`.bat` 제거 — 각 `start-*` 스크립트가 `.env` 없으면 자체적으로 초기 설정(API 키·비밀번호 입력)까지 처리해 더블클릭 한 번으로 축소
- `start.command`/`.bat` → `start-docker.command`/`.bat`, `stop.command`/`.bat` → `stop-docker.command`/`.bat`로 이름 통일 (uv 경로와 대칭되도록)

**`GETTING_STARTED.md`**
- `uv` 실행을 기본 경로로, Docker는 "격리된 환경이 필요할 때"용 대안으로 전면 재구성 (7단계 → 5단계로 축약)
- 실제 스크린샷 9장 반영 (다운로드, API 키, 실행 화면, 로그인, 대시보드 결과 + Windows 전용 보안 경고 3종)
- Windows에서 뜨는 "게시자 확인 안 됨" 경고·방화벽 허용 창에 대한 안내 추가 (실제 Windows 테스트로 발견)
- 볼드(`**`)와 한글 조사가 바로 붙을 때 CommonMark 강조 규칙상 렌더링이 깨지던 버그 4곳 수정 (`marked`로 실제 렌더링 검증)

실제 Windows 환경에서 처음부터 끝까지 실행 테스트 완료 (uv 설치 → 초기 설정 → 서버 실행 → 로그인 → 회사 분석까지 확인).

## v1.1.10 — Windows에서 `.env` API 키가 인식 안 되던 버그 수정 (2026-07-20)

**`backend/config.py`**
- `.env` 파일에 BOM(Byte Order Mark)이 붙어 저장돼도 `GOOGLE_API_KEY` 등 첫 번째 키를 정상 인식하도록 `env_file_encoding`을 `utf-8`→`utf-8-sig`로 변경 — PowerShell `Set-Content -Encoding UTF8`이 BOM을 붙이는데, 이를 `utf-8`로 읽으면 첫 키 이름 앞에 보이지 않는 문자가 붙어 매칭이 깨지고 API 키가 빈 값으로 처리되던 문제

**`run/setup.bat`**
- `setlocal enabledelayedexpansion` 누락으로 같은 블록 안에서 입력받은 키 값을 곧바로 치환할 때 옛 값(빈 값)을 참조하던 지연 확장 버그 수정, `.env` 재작성 시 주석의 한글이 깨지던 인코딩 문제도 함께 수정

실제 Windows 환경에서 재현·검증 완료 (uv 실행 방식 개발 중 발견).

## v1.1.9 — 분석 진행 중 표시 배너 + provider/reasoning_effort 경쟁조건 후속 수정 (2026-07-20)

**`backend/main.py`, `frontend/index.html`, `frontend/app.js`, `frontend/style.css`**
- 회사 분석 진행 중 표시 배너 추가 — `_track_in_progress` 데코레이터로 진행 건수를 서버에 기록하고 `GET /api/analysis-in-progress`로 조회, 프론트가 7초 주기로 폴링해 네비게이션 바에 "N건 분석 중..." 배너 표시. 페이지를 이동해도 유지되어 실수로 같은 분석을 중복 제출하는 것을 방지

Codex 리뷰(`docs/review-w-codex/review_w_codex_2026-07-20_v1.1.8.md`)에서 발견된 후속 이슈 3건 수정:

**`backend/main.py`**
- 이미지 분석(OCR)과 뒤이은 회사 분석 단계가 서로 다른 provider를 쓸 수 있던 경쟁조건 수정 — `add_from_image()`에서 스냅샷을 한 번 떠서 OCR 호출과 `_process_company()` 양쪽에 전달
- 프로필 생성 1·2단계 사이 OpenAI reasoning_effort가 바뀔 수 있던 경쟁조건 수정 — 프로필 업로드도 스냅샷을 떠서 두 호출에 동일하게 전달
- 진행 배너 카운터가 URL 스크래핑·이미지 OCR 구간을 못 세던 문제 수정 — 카운터를 `_process_company()`가 아니라 `add_from_text`/`add_from_url`/`add_from_image`/`refill` 네 API 진입점 자체로 옮겨, 요청 전체 구간을 포함하도록 확장

## v1.1.8 — LLM provider 전역 상태 경쟁조건 수정 (2026-07-16)

**`backend/llm/router.py`**
- `LLMSnapshot`(provider/모델/reasoning_effort 고정 스냅샷) + `capture_snapshot()`/`light_from_snapshot()`/`high_from_snapshot()` 추가 — 회사 분석 파이프라인 1회 실행 도중 설정이 바뀌어도 그 실행 안에서는 시작 시점의 provider로 일관되게 처리

**`backend/llm/base.py`, `backend/llm/openai.py`, `backend/llm/anthropic.py`, `backend/llm/gemini.py`**
- `extract_structured`/`complete`에 `reasoning_effort` 파라미터 추가(OpenAI만 실제 사용, 나머지는 시그니처 일치용 no-op)

**`backend/main.py`**
- `_process_company()`, `refit_company()` 시작 시점에 `capture_snapshot()` 호출, 이후 모든 provider/모델 재조회를 스냅샷 참조로 교체 — 분석 도중 설정 화면에서 provider를 바꿔도 회사 1건의 결과(구조화 추출/본문 생성/적합도 평가)가 서로 다른 provider로 섞이지 않음

## v1.1.7 — 모바일 적합도 체크 테이블 레이아웃 개선 (2026-07-16)

**`frontend/style.css`**
- `fit-check-table`의 데스크톱 고정폭(220px/90px)이 모바일(≤768px)에서 근거 칸을 과도하게 압박하던 문제 수정 — 항목/충족여부/근거를 3:2:4 비율로 재조정

**`frontend/app.js`**
- 충족 현황 테이블의 "충족 여부" 칸에서 이모지(✅/❌) 뒤에 줄바꿈을 넣고 라벨("충족"/"미충족")은 `white-space: nowrap`으로 글자 단위 줄바꿈 없이 표시

## v1.1.6 — Gemini 기본 provider 전환 + 원클릭 설치 스크립트 + 저장소 정리 (2026-07-15)

**`backend/config.py`**
- 기본 provider를 Claude에서 Gemini로 변경 — 무료 티어로 별도 결제 없이 바로 체험 가능. 더 정확한 분석을 원하면 Claude API 키를 추가하고 설정에서 전환하도록 안내

**`.env.example`, `CLAUDE.md`, `README.md`, `GETTING_STARTED.md`**
- Gemini(`GOOGLE_API_KEY`)를 필수/기본 키로, Claude(`ANTHROPIC_API_KEY`)를 "선택(추천)"으로 재정렬. Gemini 무료 티어 관련 안내 문구도 "보조 비교용"에서 "기본, 품질 원하면 Claude 추천"으로 수정

**`run/`** (신규 폴더)
- `setup.command`/`setup.bat` — 더블클릭으로 Gemini/Claude API 키·로그인 비밀번호를 입력받아 `.env`를 자동 생성(터미널 명령어 불필요)
- `start.command`/`start.bat` — `docker compose up --build` 래퍼
- `stop.command`/`stop.bat` — `docker compose down` 래퍼

**`docker/`** (신규 폴더)
- 저장소 루트의 `Dockerfile`, `nginx.conf`를 이동, `docker-compose.yml`의 `build`/`volumes` 경로 갱신

**`GETTING_STARTED.md`**
- Docker Desktop 설치 중 계정 로그인/스킵 옵션 안내 추가
- API 키 발급 순서를 Gemini(기본, 무료) 우선 → Claude(선택, 추천) 순으로 재구성
- `run/` 폴더 스크립트 기반 흐름을 기본 경로로, 터미널 직접 사용법은 별도 선택 섹션으로 분리
- 스크린샷 자리 표시 9곳 + `assets/guide/` 캡처 목록 추가

## v1.1.5 — 완전 초보자용 시작 가이드 추가 (2026-07-15)

**`GETTING_STARTED.md`** (신규)
- Docker/터미널을 처음 접하는 사람 기준의 셀프호스팅 단계별 가이드(Docker Desktop 설치 → 프로젝트 다운로드 → 터미널 사용법 → API 키 발급 → 실행 → 첫 사용, 문제 해결 표 포함). 지인 공개 배포를 앞두고 작성

**`README.md`**
- 상단에 초보자 가이드 링크 추가, 기존 README는 사용 경험이 있는 사람 대상 빠른 참조로 위치 정리

## v1.1.4 — MIT 라이선스 추가 (2026-07-15)

**`LICENSE`** (신규)
- 향후 오픈소스 공개를 앞두고 MIT 라이선스 채택. README에 라이선스 섹션 추가

## v1.1.3 — Q&A 채팅 마크다운 렌더링 수정 (2026-07-15)

**`frontend/app.js`**
- `marked.setOptions({ breaks: true })` 추가 — 단일 줄바꿈이 그냥 공백으로 합쳐지던 문제 수정
- Q&A 채팅 버블(`appendBubble`)에 `markdown-body` 클래스 누락 수정 — 다른 마크다운 렌더링 위치와 달리 이 클래스가 없어서 헤더·리스트·문단 여백이 전부 0으로 표시되던 문제
- `**"강조"**뒤텍스트`처럼 닫는 `**` 바로 앞이 따옴표이고 뒤에 공백 없이 글자가 이어지면 CommonMark 강조 판정 규칙상 볼드로 인식되지 않던 문제 — marked 파싱 전에 `**` 쌍을 직접 `<strong>`으로 치환하도록 수정 (한국어 LLM 응답에서 자주 발생하는 패턴)

**`frontend/style.css`**
- `.qa-bubble.user`에 `white-space: pre-wrap` 추가 — 여러 줄로 입력한 질문의 줄바꿈이 사라지던 문제 수정

## v1.1.2 — Claude-Codex 인계 워크플로우 + 리뷰 문서 정리 (2026-07-15)

**`AGENTS.md`** (신규)
- Claude가 주 구현자, Codex는 계획·리뷰 및 Claude 사용량 제한/긴급 시 인계 담당이라는 워크플로우 문서화
- 작업 시작 시 `CLAUDE.md`/`docs/TODO.md`/git 상태/최근 리뷰 문서를 스스로 확인하도록 명시, 리뷰 요청 시 diff·커밋 로그·CHANGELOG 정도만 넘기면 충분하다는 원칙 추가

**`docs/review-w-codex/`**
- 저장소 루트에 흩어져 있던 Codex 리뷰 문서 5개를 한 디렉토리로 정리. 단, 이 문서들은 기존부터 의도적으로 git 미추적 대상이라 `.gitignore`에 등록(실수로 한 번 커밋됐던 이력은 이후 히스토리에서 완전히 제거함)

**`.agents/skills/codex-review-report/`**
- Codex가 리뷰 보고서를 버전별 Markdown으로 저장하는 자체 스킬 정의를 git 추적 대상으로 추가, 저장 경로를 `docs/review-w-codex/` 기준으로 갱신

## v1.1.1 — Codex 코드 리뷰 발견 8건 수정 (2026-07-15)

> `review_w_codex_2026-07-14_v1.0.5.md` 반영, `fix/analysis-timeout-300s` 브랜치

**`backend/main.py`**
- 신규 공고 분석(URL/텍스트/이미지) 타임아웃을 120초 → 300초로 상향 — 기본 OpenAI(gpt-5, effort=medium) 조합이 실제로 150~180초 걸려 504가 발생하던 문제. nginx 일반 API 제한(300초)과 통일
- `/api/companies/compare`가 쉼표 구분 문자열 대신 반복 query parameter(`slugs=a&slugs=b`)를 받도록 변경 — slug에 쉼표가 있으면 두 회사로 잘못 분리되던 문제 해결
- 삭제 전 백업(`_save_backup_zip`) 실패 시 삭제 API가 500을 반환하도록 변경(fail-closed) — 기존엔 백업 실패해도 경고 로그만 남기고 삭제가 계속 진행됨

**`backend/storage.py`**
- `delete_company()`의 백업 실패 예외 흡수(swallow) 제거 — 위 fail-closed 동작의 근거

**`backend/telegram.py`**
- `client.post()` 응답에 `raise_for_status()` 추가 — 잘못된 chat ID, 인증 실패, rate limit 등으로 텔레그램이 4xx/5xx를 반환해도 감지되지 않던 문제 수정

**`frontend/app.js`**
- slug를 사용하는 모든 API 호출 경로(핀 토글·상태변경 전 조회·상세 조회·다중 삭제)에 `encodeURIComponent()` 누락분 적용 — 직무명에 `#`,`,`,`&` 등 URL 예약문자가 있으면 상세/핀/삭제/비교가 실패하던 문제
- Q&A 전송 시 저장된 히스토리 전체가 아닌 최근 40개만 보내도록 수정 — 21회 완료 후 프론트가 40개 제한을 넘겨 계속 422가 발생하던 문제
- 비교 화면 체크박스가 5개를 초과하면 방금 선택한 항목을 자동 해제하고 안내 — 기존엔 6개 이상 선택해도 비교 화면 진입 후에야 422 발생
- 타임라인 제외 라벨(`EXCLUDED_LOG_LABELS`)에 "재분석 완료" 추가 — refill 이벤트가 지원 이벤트로 잘못 노출되던 문제
- UTC 기준 `toISOString().slice(0,10)` 대신 로컬 타임존 기준 `localDateString()` 헬퍼로 교체(상태 로그 날짜, 캘린더 오늘 표시, 백업/CSV 파일명)

**`docker-compose.yml`**
- API 컨테이너에 `TZ=Asia/Seoul` 환경변수 추가 — 한국 시간 자정~오전 9시 사이 `date.today()`가 UTC 기준으로 하루 전 날짜를 반환하던 문제 해결

## v1.1.0 — 메신저 알림 기능 개선 (Phase 7) (2026-07-15)

**채널 확장**
- 슬랙·디스코드 Incoming Webhook 알림 채널 추가(`backend/slack.py`, `backend/discord.py` 신설). 텔레그램과 동일하게 설정돼 있으면 전송, 없으면 스킵하는 독립 토글 구조이며 `backend/notify.py`가 `asyncio.gather`로 3채널 병렬 전송

**알림 내용 커스터마이즈**
- 설정 화면에 강점 요약(기본 ON)·갭 요약(기본 ON)·잡플래닛 평점(기본 OFF)·임직원 수(기본 OFF) 체크박스 추가, 4개 모두 끄면 기본 메시지만 전송
- `strengths`/`gaps` 등급 표기를 (강/중/약)·(상/중/하)로 다르게 쓰던 것을 (상/중/하)로 통일하고, `prompts.py`에 강점 등급 판정 기준 신규 추가

**채널별 포맷팅**
- 알림 재료(회사명/직무/점수/라벨/강점/갭/잡플래닛/임직원수)를 dict로 조립하고 `backend/notify_format.py`의 `build_message()` 공통 빌더가 텔레그램(HTML `<b>`)·슬랙(`*mrkdwn*`)·디스코드(`**markdown**`)별 굵게 문법만 주입해 재사용
- 분석 완료 메시지 레이아웃을 압축(헤더 한 줄 합침, 강점/갭 쉼표 나열)

**주간 지원 현황 요약 알림**
- `_weekly_summary_loop()` 백그라운드 태스크(새 스케줄러 의존성 없이 `asyncio.sleep` 기반)가 설정된 요일·시각(기본 월요일 09:00)에 신규 등록 건수·상태별 현황·방치된 항목(7일 이상 진행 없음)을 요약해 발송
- 설정 화면에 온/오프 토글 + 요일·시각 선택 UI 추가, "분석 완료 알림" 섹션과는 트리거 방식이 달라 별도 섹션으로 분리

**기타**
- 설정 페이지 체크박스가 세로로 쌓여 빈 공간이 커지던 CSS 버그 수정(`label` 전역 `flex-direction:column` 상속 문제)
- `.env.example`/README에 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`/`SLACK_WEBHOOK_URL`/`DISCORD_WEBHOOK_URL` 및 "알림 설정" 섹션 신규 문서화

## v1.0.5 — README 사용 비용 표기 정정 (2026-07-14)

**`README.md`**
- "실제 사용 비용" 절 제목·문구를 "사용 비용 추정치"로 정정하고, 표의 금액이 각 Provider 콘솔의 실제 청구 내역이 아니라 `usage_tracker.py`의 공개 단가표 × 토큰 수로 계산한 추정치임을 명시하는 안내 문구 추가. 무료 티어 Gemini 키로 호출해도 usage_log에는 유료 단가 기준 비용이 그대로 계산·기록된다는 것을 확인한 뒤, "실측"이라는 표현이 과장이었음을 인지해 정정

## v1.0.4 — Gemini 429 오류 메시지 정확화 + 실제 사용 비용 실측 갱신 (2026-07-13)

**`backend/llm/gemini.py`**
- `_raise()`가 429/RESOURCE_EXHAUSTED 발생 시 실제 원인과 무관하게 항상 "무료 티어 요청 한도 초과"로 고정 출력하던 버그 수정 — 유료(prepay) 계정에서도 무료 티어 문구가 나가 혼선 발생. google-genai `APIError`가 `e.message`에 담고 있는 원본 메시지를 확인해, "prepay" 포함 시 선불 크레딧 소진 안내로, 그 외 429는 원본 메시지를 함께 노출하도록 수정

**`README.md`**
- "실제 사용 비용" 표 실측 갱신 — 공고 1건 분석(Claude/OpenAI/Gemini 기본 설정 실측치) + 적합도 재평가(refit) 표(OpenAI gpt-5 $0.0550, Gemini 3.5-flash $0.0595) 반영

**`docs/TODO.md`**
- 위 429 메시지 버그 수정 항목, OpenAI gpt-5 504 타임아웃 관찰 항목 추가

## v1.0.3 — Gemini 3.5 Flash / 3.1 Flash-Lite 단가 추가 (2026-07-13)

**`backend/usage_tracker.py`**
- `PRICING` dict에 `gemini-3.5-flash`(입력 $1.50 / 출력 $9.00), `gemini-3.1-flash-lite`(입력 $0.25 / 출력 $1.50) 단가 추가 — 유료 티어 Standard 기준, per 1M tokens. 두 모델이 이미 기본 모델로 설정돼 있었으나 단가 미등록으로 비용 계산이 누락되던 상태였음

## v1.0.2 — Gemini 기본 모델 갱신 + reasoning_effort 설정 반영 버그 수정 (2026-07-13)

**`backend/config.py`**
- Gemini 기본 모델을 Phase 2 실험 결론(Exp4, `docs/phase2_gemini_experiment.md`)에 맞춰 갱신 — Light `gemini-2.5-flash-lite`→`gemini-3.1-flash-lite`, High `gemini-2.5-flash`→`gemini-3.5-flash`. 이전 v0.16.x~v1.0.x는 실험 전 베이스라인이 그대로 남아있었음(프롬프트만 v7.4로 반영되고 모델 기본값은 누락)

**`backend/llm/openai.py`**
- `_reasoning_effort_kwarg()`가 런타임 오버라이드 없을 때 `config.py`의 `openai_reasoning_effort` 기본값을 보지 않고 하드코딩된 `"medium"`으로 fallback하던 버그 수정 — 죽어있던 `get_reasoning_effort()` 헬퍼(`config.py`)를 사용하도록 교체

**`README.md`, `CLAUDE.md`**
- Gemini 기본 모델 표기를 위 변경에 맞춰 갱신

## v1.0.1 — Gemini 429(요청 한도 초과) 처리 정확화 (2026-07-13)

**`backend/llm/gemini.py`**
- `_raise()`/`_is_retryable()`이 참조하던 `getattr(e, "status_code", None)` 수정 — google-genai `APIError`엔 `status_code` 속성이 없어(`code`가 맞음) 항상 `None`이 되는 죽은 코드였고, 지금까지는 문자열 매칭(`"RESOURCE_EXHAUSTED" in msg` 등)으로만 우연히 동작 중이었음
- 429도 재시도 대상에 포함(`_retry_plan()` 신설) — 분당 한도 초과는 20초 대기 후 1회만 재시도(일일 한도 초과라면 재시도해도 무의미하므로 오래 붙들지 않음), 503류는 기존대로 5초/10초 간격 최대 2회
- 재시도 로그를 "429(요청 한도 초과)"/"일시 오류"로 구분 표기, 최종 실패 메시지도 "Gemini 무료 티어 요청 한도(분당/일일)를 초과했습니다..."로 구체화

**`frontend/app.js`**
- `streamQA()`가 429/503 응답에서 백엔드가 보낸 `err.detail`을 읽지 않고 버린 뒤 "연결 실패 (HTTP 429)"로만 표시하던 버그 수정 — 이제 실제 안내 메시지가 채팅 버블에 그대로 표시됨

**`README.md`**
- 접속 안내 포트 오타 수정 (`http://localhost` → `http://localhost:8000`)
- 다크모드 기능 항목 추가
- Gemini RPM 자동 재시도 설명을 실제 동작(20초 대기 후 1회)에 맞게 수정

## v1.0.0 — Codex 리뷰 발견 3건 수정 (2026-07-13)

**`backend/scraper.py`**
- SSRF 방어 우회 가능성(DNS 리바인딩) 수정: `_assert_public_url()`이 DNS를 검증한 뒤 `client.stream()`이 별도로 다시 DNS를 조회하던 구조라, 검증과 연결 사이에 DNS 응답이 바뀌면 검증은 통과하고 실제로는 차단 대상 IP로 연결될 수 있었음
- `_resolve_public_address()`(검증에 사용한 IP 반환) + `_pin_to_ip()`(URL 호스트를 해당 IP로 교체)로 재구성해 검증·연결에 동일한 주소를 사용하도록 수정. Host 헤더와 TLS SNI(`extensions={"sni_hostname": ...}`)는 원래 호스트명으로 유지해 가상호스팅·인증서 검증 정상 동작

**`backend/llm/gemini.py`**
- `stream()`이 청크를 이미 출력한 뒤 재시도 가능한 오류가 발생하면 요청을 처음부터 다시 시작해 응답이 중복 출력되던 버그 수정
- `yielded_any` 플래그로 첫 청크 출력 이전 실패에만 재시도하도록 변경 (이후 실패는 즉시 에러 반환)

**`frontend/app.js`**
- 재분석/내보내기 드롭다운의 바깥 클릭 감지 리스너가 `{ once: true }`로 등록되어, 패널이 열린 상태에서 패널 내부를 클릭(항목 미선택)하면 리스너가 그대로 사라져 이후 바깥을 클릭해도 패널이 닫히지 않던 버그 수정
- `once` 옵션 제거, 바깥 클릭이 실제로 감지됐을 때만 리스너 제거하도록 변경

## v0.16.9 — refill/refit 지원 상태 로그 세분화 (2026-07-13)

**`backend/main.py`**
- `_append_status_log()` 신설: "지원 상태 로그" 섹션 끝에 오늘 날짜로 새 항목을 추가(섹션 번호 무관, 없으면 새로 생성)
- `refit_company()`(적합도만 재평가)가 지금까지 로그를 전혀 남기지 않던 문제 수정 — "적합도 재평가 완료" 항목 추가
- `_process_company()`의 "재분석 완료" / "분석 완료" 문구 판정을 `preserved_log_entries` truthy 여부가 아니라 `existing_slug` 존재 여부(refill 호출인지)로 변경 — 기존 로그가 없거나 파싱 실패 상태에서 refill해도 문구가 정확히 기록됨
- 결과적으로 신규 분석/전체 재분석(refill)/적합도만 재평가(refit) 세 가지가 로그에 각각 다른 문구로 구분 기록됨

**`frontend/app.js`**
- `EXCLUDED_LOG_LABELS`에 `'적합도 재평가 완료'` 추가 — 신규 분석/등록과 마찬가지로 순수 배경 분석 메타데이터로 취급해 타임라인에는 노출하지 않음 (상세페이지 로그 섹션에는 계속 기록됨)

## v0.16.8 — API 입력 크기/개수 제한 부족 수정 (2026-07-13)

**`backend/models.py`**
- `FromTextRequest.text` 100,000자, `QAMessage.text` 20,000자, `QARequest`/`MultiQARequest.question` 2,000자, `history` 40개, `MultiQARequest.slugs` 5개(프론트 비교 뷰의 기존 5개 제한과 동일)로 상한 추가

**`backend/main.py`**
- `_MAX_UPLOAD_FILES`(10개), `_MAX_PDF_BYTES`(30MB), `_MAX_IMAGE_BYTES`(15MB) 상수 추가
- `upload_profile()`(PDF)·`add_from_image()`(이미지)에 개수 초과 시 400, 크기 초과 시 413 검사 추가
- 상한선은 정상 사용의 정확한 한도를 추정한 값이 아니라, 실제 관측치(기존 공고 원문 최대 7.3KB 등) 대비 수배~수십 배 여유를 둔 안전판 — 정상 사용은 걸리지 않고 실수·이상 입력만 차단

**`backend/scraper.py`**
- `_safe_get()`을 스트리밍(`client.stream()` + `aiter_bytes()`) 방식으로 재작성해 응답을 받는 도중 `_MAX_RESPONSE_BYTES`(10MB) 초과 시 즉시 중단 (Content-Length가 없거나 거짓인 응답도 실제 수신 바이트 기준으로 차단)
- 재구성한 `httpx.Response`에 원본 `content-encoding`/`content-length` 헤더를 그대로 넘기면, 이미 압축 해제된 바이트를 다시 압축 해제하려다 실패하는(`DecodingError`) 회귀 버그를 발견해 함께 수정 (재구성 시 두 헤더 제거)
- curl로 Q&A/다중Q&A/from-text 각 제한 초과 시 422 확인, PDF·이미지 11개 업로드 시 400, 16MB 이미지 업로드 시 413 확인, 정상 범위 업로드·실제 원티드 URL 스크래핑 회귀 없음 확인

## v0.16.7 — Q&A 대화 히스토리 미유지 버그 수정 (2026-07-13)

**`backend/models.py`**
- `QAMessage`(role/text) 모델 추가, `QARequest`/`MultiQARequest`에 `history: list[QAMessage] = []` 필드 추가

**`backend/main.py`**
- 단일회사 Q&A(`/api/companies/{slug}/qa`)와 비교 Q&A(`/api/companies/qa`) 모두 매 요청을 독립 단일 메시지로만 LLM에 전달해, "그럼 연봉은?" 같은 후속 질문이 이전 대화 맥락 없이 처리되던 문제 수정
- `_build_qa_messages()` 신설: 히스토리를 user/assistant 교대 메시지로 구성하고 컨텍스트(후보자 프로필+회사 정보)는 첫 메시지에만 포함, 연속 동일 role은 하나로 병합해 역할 교대를 항상 보장

**`frontend/app.js`**
- `sendQA()`가 기존 `qaHistory[currentSlug]`를 요청에 함께 전송하도록 수정
- `sendCompareQA()`는 기존에 대화 히스토리를 아예 저장하지 않던 문제도 함께 발견 — 뷰 진입 시 초기화되는 메모리 내 `compareQaHistory` 배열 추가로 같은 방식 적용
- curl로 임의 암호코드를 1턴에 알려주고 2턴에 되묻는 시나리오로 히스토리 미포함 시(맥락 없음)/포함 시(정확히 기억) 차이를 직접 검증 완료

## v0.16.6 — slug/URL 저장형 XSS 방어 (2026-07-12)

**`frontend/app.js`**
- 회사명·slug에 단일 인용부호(`'`)가 포함되면 `onclick="navigate('detail','${slug}')"` 같은 이중 인용부호 HTML 속성 안의 단일 인용부호 JS 문자열을 깨고 임의 스크립트를 주입할 수 있던 저장형 XSS 수정
- `renderPinnedSection()`, `renderMainTable()`, 상세화면 status-select에서 inline `onclick`/`onchange`에 값을 직접 문자열로 삽입하던 방식을 `data-slug`/`data-action` 속성 + `#app` 위임 이벤트 리스너(`click`/`change`)로 전환 — 값이 단일 컨텍스트(HTML 속성)에만 들어가므로 기존 `escHtml()`만으로 안전
- 부수 발견: `renderTimelineList()`, `renderCalendar()`의 `onclick="navigate('detail', ${JSON.stringify(slug)})"`가 `JSON.stringify()`의 이중 인용부호 결과와 외부 HTML 속성(역시 이중 인용부호)이 충돌해 슬러그 내용과 무관하게 항상 파싱이 깨지던 기존 버그도 같은 방식(`data-slug`)으로 함께 수정 — 캘린더/타임라인 클릭 시 상세화면 이동이 정상 동작하게 됨
- `cal-chip`의 불필요한 `event.stopPropagation()` 제거 (부모 요소에 별도 클릭 핸들러 없음을 확인)
- jsdom으로 `evil'"</script><img src=x onerror=alert(1)>` 페이로드를 slug/회사명에 넣어 파싱 이탈 여부·각 클릭/변경 핸들러 호출 여부를 시뮬레이션 검증 완료

## v0.16.5 — URL 스크래핑 SSRF 방어 (2026-07-12)

**`scraper.py`**
- `_safe_get()` 신설: 요청 전 및 redirect마다 스킴(http/https만 허용)·호스트·DNS 목적지 검사, loopback/private/link-local/reserved/multicast 주소로의 요청 차단
- `fetch_url_text()`, `fetch_wanted_facts()`, `_fetch_wanted_company()`의 모든 외부 요청을 `_safe_get()`으로 교체
- `is_wanted_host()` 신설: `main.py`의 원티드 URL 검사를 부분 문자열 검사(`"wanted.co.kr" not in url`)에서 정확한 hostname 매칭으로 교체 (`evil.com/?x=wanted.co.kr` 등 우회 차단)
- dev 환경에서 내부 IP·file 스킴·클라우드 메타데이터 주소·도메인 위장 URL 차단 및 정상 원티드 동기화 동작 모두 검증

## v0.16.4 — 다중 삭제 시 백업 ZIP 파일명 충돌 수정 (2026-07-12)

**`_save_backup_zip`** (`backend/main.py`)
- 백업 파일명이 초 단위 타임스탬프만 사용해, 같은 초에 여러 회사를 삭제하면 뒤에 생성된 백업이 앞선 백업을 덮어써 삭제 직전 데이터가 사라지던 문제 수정
- 타임스탬프에 마이크로초를 추가해 파일명 충돌 방지
- dev 환경에서 동시 삭제 재현 테스트로 검증 완료 (충돌 없이 두 스냅샷 모두 보존)

## v0.16.3 — refill 전체 재분석 시 사용자 데이터 초기화 버그 수정 (2026-07-12)

**`_process_company`/`refill_company`** (`backend/main.py`)
- 전체 재분석(refill) 시 `status`/`pinned`/`tags`/`application_source`/`created_at`이 LLM 재추출 결과로 덮여 초기화되던 문제 수정 — 기존 frontmatter 값을 그대로 유지
- 본문 재조립 시 `## N. 지원 상태 로그` 섹션의 기존 날짜별 이력이 통째로 사라지던 문제 수정 — 기존 로그를 파싱해 새 항목 위에 이어붙임
- `prompts.py` 상단 docstring의 마크다운 섹션 번호 설명 오류 정정 (실제 코드와 불일치)
- dev 환경에서 refill 실행 후 보존 대상 필드·로그 이력 전부 검증 완료

## v0.16.2 — Gemini 프롬프트 v7.4 + 공용 스키마 앵커 제거 (2026-07-10)

**Gemini 전용 적합도 평가 프롬프트 v6 → v7.4** (`backend/prompts.py`)
- step 0 신설: 요건 테이블 전수 작성 강제 — "공고에 항목이 N개면 테이블 행도 N개"
- 🔲(불명확) 항목도 갭에 포함 — 채용 관점에서 불명확은 리스크
- step 5 신설: 주요 업무·팀 소개 섹션까지 갭 발굴 대상 확장 (✅ 판정 항목의 세부 범위 미경험도 별도 갭)
- step 6 재설계: 셀프체크를 3범주(①테이블 ❌·🔲 ②주요 업무 갭 ③비기술 갭) 순차 대조로 변경
- 심각도 "분류" 기준과 갭 "포함" 기준 분리 명시
- 체크리스트 점수: v7.4 = 31/39 (v7.5는 30/39로 롤백)

**공용 스키마 사실성 앵커 제거** (`backend/prompts.py`, 전 provider 공용)
- `revenue_status` 예시 `'2023년 매출 200억'` 제거 — 원문에 없는 연도를 출력에 주입하는 앵커 (Gemini 단독 테스트 3회 모두 재현 → 제거 후 0회)
- "원문 표현 그대로 기재, 원문에 없는 연도·출처 추가 금지" 지시 추가
- `strengths` 예시의 실제 회사명·기술명 익명화 (`[기술 A]`/`OO회사` 형식)

**적합도 리포트 이중 헤더 버그 수정** (`backend/main.py` 4곳)
- 모델이 출력한 `## 4. 적합도 리포트` 헤더 제거 시 `re.sub` 후 `.strip()` 순서라, 출력이 개행으로 시작하면 `^` 앵커가 빗나가 헤더 중복 출력 → strip을 먼저 적용

**검증 결과**
- 1단계(추출)·2단계(본문) Gemini light 단독 검증: 3사×3회 완전성·형식 합격
- Gemini refill 엔드투엔드 3사 합격: 연도 앵커 재발 0건, 무근거 매출 표현 0건, v7.4 템플릿 준수 (88/78/52점)
- 공용 프롬프트 변경 교차 검증: OpenAI 무결(원문 100% 보존), Claude는 haiku 고유 연도 날조 1건 관찰(프롬프트 무관, 기록만)

---

## v0.16.1 — Gemini 전용 프롬프트 + 적합도 평가 최적화 (2026-07-09)

**Gemini 전용 적합도 평가 프롬프트 (v5)**
- `EVALUATE_FIT_SYSTEM_GEMINI` 신규 추가 (`backend/prompts.py`)
  - 유사 기술·상위 카테고리로 대체 판정 금지 (step 1)
  - 비기술 요건(경력 연수·근무지·도메인) 별도 체크 스텝 (step 4)
  - 갭 완전성 지시: 심각도 무관 전수 보고
- `_evaluate_fit_system()` gemini 분기 추가 (`backend/main.py`)

**Gemini extract_structured 안정화**
- `temperature=0.3` 설정 (`backend/llm/gemini.py`): 갭 탐지 stochasticity 감소
- `location_check → gaps` 자동 브릿지 (`backend/main.py`, Gemini 전용)
  - location_check가 "조건부/미달"인데 gaps에 근무지 항목 없으면 자동 보정
  - Claude/OpenAI에는 미적용

**Gemini complete() 개선**
- Section 5 (종합 의견) 작성 지시 명시: `## 4. 적합도 리포트` ~ `## 5. 종합 의견` 전체 생성
- 모델 변경 로깅 dedup 처리

**프롬프트 튜닝 실험 결과 (NHN Cloud 기준)**
- v1(4/5, Judge B) → v2(2/5 퇴보) → v5+bridge(5/5 달성, 60점)
- 핵심 개선: G4 근무지 탐지율 16% → 100%, G2 Hadoop/ELK 안정화

---

## v0.15.0 — OpenAI 전용 프롬프트 분리 및 reasoning_effort 확장 (2026-07-07)

**OpenAI 전용 적합도 평가 프롬프트 신설**
- `EVALUATE_FIT_SYSTEM_OPENAI` 추가 (`backend/prompts.py`): OpenAI용 갭 판정 전용 시스템 프롬프트
  - `[근거 명시 원칙]`: ✅ 판정 시 이력서의 회사명·프로젝트명+기술명 출처 필수 명시 → 근거 없는 ✅ 남발 방지
  - ❌/🔲 판정 원칙 및 금지 규칙 OpenAI 특화 강화
- `EVALUATE_FIT_SYSTEM` 원복: 공유 프롬프트에서 [갭 판정 기준] 제거 — Claude는 원래부터 갭 판정 정확도가 높아 불필요
- `_evaluate_fit_system()` 헬퍼 추가 (`backend/main.py`): Provider(Claude/OpenAI)에 따라 자동으로 맞는 시스템 프롬프트 선택

**reasoning_effort 지원 범위 확장**
- `_reasoning_effort_kwarg()` 조건 단순화 (`backend/llm/openai.py`): `model.startswith("gpt-5")`로 변경
  - 기존: `gpt-5` 본체 + `gpt-5.x` 점 계열만 지원 (하이픈 계열 제외)
  - 변경: `gpt-5-mini`, `gpt-5-nano` 포함 — `high` effort에서 실제 추론 활성화 확인 (D-3 실험)
  - `low`/`medium` effort에서는 reasoning_tokens=0이나 파라미터 수용, `high`에서 추론 활성화

**실험 결과 요약 (Phase D LLM Judge)**
- Best 구성: Claude는 원본 프롬프트 유지, OpenAI는 `EVALUATE_FIT_SYSTEM_OPENAI` v6 채택
- 갭 판정 정확성은 프롬프트 기여가 크고, 근거 구체성·전략 실용성은 모델 능력 기여가 큼

---

## v0.14.0 — OpenAI 모델 기본값 업데이트 및 reasoning_effort 지원 (2026-07-06)

- **OpenAI 기본 모델 변경**: High=`gpt-5`, Light=`gpt-5-mini` (Phase 1 실험 결과 반영, 비용 -35%)
- **reasoning_effort 지원**: `gpt-5`/`gpt-5.x` 계열에 자동 적용, 기본값 `medium`
- **settings API 확장**: `openai_reasoning_effort` 필드 GET/PUT 지원
- **max_completion_tokens 자동 상향**: reasoning 모델에서 출력 공간 부족 방지 (extract: 16384, complete: 8192)
- **LLM 단가 업데이트**: gpt-5.x 전 계열 비용 추적 (`usage_tracker.py`)

---

## v0.13.1 — 적합도 평가 정확도 개선 (2026-07-06)

**적합도 평가 프롬프트 개선**
- **strengths 스키마 강화**: 각 항목을 특정 프로젝트·회사에서의 단일 경험으로 서술하도록 명시 — 서로 다른 회사/프로젝트 경험을 "멀티모달" 같은 통합 역량으로 묶는 오류 방지
- **다중 프로젝트 합산 금지 지시 추가**: EVALUATE_FIT_SYSTEM에 "통합 처리·단일 파이프라인 등으로 묶어 표현 금지, 각 프로젝트 개별 나열" 명시
- **잡플래닛 평점 평가 기준 추가**: 점수 산정 우선순위 5번에 잡플래닛 평점 항목 추가 (3.0 미만 감점, 2.5 미만 대폭 감점)
- **핵심 근거 작성 지침 강화**: 필수요건 미충족 항목 우선 포함 지시, 프로젝트 합산 금지 명시
- **어필 포인트 형식 변경**: 강점·경험 키워드 → 특정 프로젝트명·회사명 기반으로 서술하도록 변경
- **하이라이트 범위 확장**: "종합 의견 서술 중" → "종합 의견·핵심 근거·지원 전략 전반"으로 확대

**refit 자기참조 편향 수정**
- `refit_company`에서 `company_json`으로 기존 `strengths`·`gaps`·`fit_score`·`fit_label` 제외 — 이전 평가 결과가 다음 평가 입력으로 재활용되어 편향 발생하던 버그 수정

---

## v0.13.0 — 잡플래닛 안정성·마크다운 테이블 레이아웃·프롬프트 개선 (2026-07-06)

**잡플래닛 수집 안정성**
- **영문 괄호 제거**: `딥파인(DEEP.FINE)` 같은 `display_name`에서 괄호 내 영문을 제거 후 검색·매칭 (`jobplanet.py` `_normalize()` + `fetch_jobplanet_score()`)
- **refill 시 기존 점수 재사용**: `_process_company(existing_slug=...)` 호출 시 기존 `jobplanet_score`가 있으면 네이버 재검색 생략 (`main.py`)

**마크다운 테이블 레이아웃**
- **섹션 1·2 항목 열 고정**: JS로 렌더링 후 `h2` 텍스트 `1.`·`2.` 감지 → 다음 `<table>`에 `info-table` 클래스 부여 → `table-layout: fixed; 첫 열 130px`
- **충족 현황 테이블 열 고정**: `h3` 텍스트에 "충족 현황" 포함 시 → `fit-check-table` 클래스 → 항목 220px / 충족 여부 90px(가운데 정렬) / 근거 나머지

**프롬프트 개선**
- **`revenue_status` 스키마**: "확인됨"/"미확인" 단순 상태값 대신 실제 수치·설명 추출 (예: "2023년 매출 200억", "흑자 전환"). 구체적 정보 없으면 null
- **`stability` 안정성 판단 기준**: 최신(당해·전년도) 매출 수치가 명확할 때만 반영, 오래된 데이터는 제외
- **본문 템플릿**: `매출현황` 행 — 구체적 수치 없으면 생략 지시 추가
- **채용 절차 포맷**: `**채용 절차**\n서류전형 → ...` (줄바꿈 미적용) → `**채용 절차**: 서류전형 → ...` (한 줄 인라인) 으로 수정

---

## v0.12.0 — 다크모드·프롬프트 재설계·프로필 편집기 (2026-07-06)

**UI / UX**
- **CSS 변수 리팩토링**: 하드코딩된 색상 전체를 CSS custom properties로 교체 (다크모드 기반 작업)
- **다크모드**: `[data-theme="dark"]` 변수 오버라이드, 토글 버튼(🌙/☀️), `localStorage` 저장. highlight.js 테마도 연동
- **마크다운 split 에디터**: 편집 탭에서 좌측 textarea + 우측 실시간 미리보기. highlight.js 코드 하이라이트 적용
- **모바일 반응형**: 768px 브레이크포인트 기반 레이아웃 전환. 내보내기 드롭다운 모바일 오버플로우 수정
- **프로필 수동 편집 UI**: "직접 편집" 버튼으로 split 에디터 토글, `PUT /api/profile` 저장 (frontmatter 유지)
- **상태칩 크기 통일**: `.status-select` 패딩·폰트 사이즈를 `.status-chip`과 동일하게 조정

**프롬프트 재설계**
- **GENERATE_BODY 포맷 고정**: 3섹션 출력 형식 명시
  - 섹션 1 기본정보: 표(회사명·근무지·경력요건·고용형태·분야·연봉)
  - 섹션 2 회사 규모/안정성: 표(임직원·투자단계·누적투자금·매출·잡플래닛·안정성) + `> 📝` 판단근거
  - 섹션 3 공고내용: 기술스택 인라인코드 상단 배치, 주요업무/필수요건/우대요건/복리후생/채용절차
- **EVALUATE_FIT 재설계**: 점수 산정 우선순위 7항목 명시, 점수→라벨 매핑 테이블, fit_report_body 6섹션 포맷 확정
  - 자격요건 충족 현황 표 / 우대사항 충족 현황 표 / 직무 적합도 분석 표 / 종합 평가 / 평가 근거 / 지원 전략
- **benefits·hiring_process 필드 추가**: `CompanyFrontmatter`에 복리후생·채용절차 배열 필드 추가. 추출 스키마·마크다운 생성 반영
- **EXTRACT_PROFILE 개선**: `skills` 단일 배열 → `tech_skills`·`domains`·`soft_skills` 3분리. `domains`는 회사명이 아닌 프로젝트 내용 기반으로 판단하도록 명시. `summary` 필수 포함 항목(경력연수·주요직무·핵심기술·도메인) 가이드 구체화. 여러 파일 종합 판단 안내 추가
- **전체 시스템 프롬프트 할루시네이션 방지**: 채용공고·기업 정보·지원자 프로필에 명시된 내용만 사용하도록 모든 SYSTEM 프롬프트에 명시. 소스별로 맥락에 맞게 표현 분리

---

## v0.11.0 — 타임라인·캘린더 뷰·SSE 재연결 (2026-07-04)

**지원 현황 타임라인 + 캘린더**
- **`GET /api/companies/timeline`**: 상태 로그 파싱 → 전 회사 이력 반환. `_parse_status_log(body)` 헬퍼 추가 (FastAPI 경로 순서 주의: `/{slug}` 이전에 등록)
- **타임라인 뷰**: 월별 그룹, 상태 도트, 회사명·직무명·적합도·지원 상태 표시
- **캘린더 뷰**: 7컬럼 그리드, 날짜 셀에 2줄 칩(회사명 + 직무명), `+N` 더보기 표시
- **상태별 요약 카드**: 타임라인 상단에 지원 중·서류통과·면접·최종·탈락·보류 카운트
- **탭 전환**: 타임라인 / 캘린더 탭 전환, "종료 보기" 토글로 탈락·지원마감 포함/제외
- **필터링 기준**: `ACTIVE_STATUSES`(지원·서류통과·인터뷰·최종·보류) + `CLOSED_STATUSES`(탈락·지원마감)만 표시 — 분석 완료·미지원 제외. "분석 완료"·"등록" 로그 라벨도 제외
- **동일 날짜 중복 제거**: 같은 회사·날짜 중 가장 최신 상태 하나만 표시 (`seen` Set 기반)

**내비게이션**
- **URL 라우팅**: `/timeline` 경로 추가 — `viewToUrl()` / `parseUrl()` 업데이트
- **뒤로가기 버튼**: 타임라인 뷰에 `← 목록` 버튼, 대시보드 `<h2>` 옆에 `타임라인 →` 버튼 추가 (back-btn 스타일 통일)
- **네비게이션 바**: 타임라인 항목 제거 (대시보드 헤더 버튼으로 대체)

**SSE 재연결**
- **`streamQA(fetchFn, bubble)`**: fetch + consumeSSE를 최대 2회 재시도 (지수 백오프). 네트워크 오류 시 "재연결 중..." 메시지 표시. AbortError(사용자 이탈)는 별도 처리 불필요

---

## v0.10.0 — ZIP 백업·내보내기 드롭다운·프로필 아코디언 (2026-07-03)

**데이터 백업**
- **ZIP 내보내기**: `GET /api/export/zip` — 필수 4종(회사 분석·원문·프로필·평가 기준) 고정, PDF·비용 로그 선택 포함. 체크박스 UI
- **삭제 시 자동 백업**: 회사 삭제 직전 `data/backup/backup_YYYYMMDD_HHMMSS.zip` 저장, 최근 5개 롤링 유지

**UI 통합**
- **내보내기 드롭다운**: 설정 페이지 `내보내기 ▾` 버튼 하나로 ZIP·전체 CSV·프로필 MD 내보내기 통합. 기존 프로필 내보내기 버튼·📦 섹션 제거
- **프로필 업데이트 아코디언**: 프로필 미등록 시 펼침, 등록 후 접힘. 타이틀 클릭으로 토글

---

## v0.9.0 — 프로필 생성 개선·내보내기·연봉 평가 기준 (2026-07-03)

**프로필 생성 구조 개선**
- **2단계 분리**: tool use(구조화 필드 추출)와 complete(마크다운 본문 생성)을 분리. tool use의 장문 산문 생성 한계(`profile_body` 빈 문자열 반환)를 근본 해결
- **3소스 통합**: PDF 원문 + 구조화 추출 JSON + 추가 메모를 모두 본문 생성 프롬프트에 전달
- **5섹션 구성**: 기본 정보 / 경력 요약 / 주요 프로젝트 / 핵심 역량 / 추가 메모 요약
- **max_tokens 파라미터화**: `complete()` 메서드에 `max_tokens` 인자 추가 (기본 4096). 프로필 본문 생성은 8192 사용
- **잘림 감지 로그**: `stop_reason == max_tokens` 시 WARNING 출력

**프로필 UI 개선**
- **업로드 영역 카드 분리**: `📤 프로필 업데이트` 카드로 미리보기 영역과 시각적 구분
- **max_tokens 입력 필드**: 업로드 버튼 옆에 숫자 입력 (기본 8192, 1024 단위)
- **내보내기 버튼**: 섹션 h3 우측 고정. 프로필 없을 때 비활성, 등록 후 활성화. `GET /api/profile/export` 마크다운 다운로드
- **가이드 텍스트**: 자동 추출 항목(희망 근무지·연봉·경력 연수 등) 목록과 추가 설명란 안내

**적합도 평가 기준 개선**
- **연봉 평가 비대칭화**: 공고 연봉이 희망 최소 연봉 이상이면 가산, 미명시·미달은 감점 없음. 현실적으로 대부분의 공고가 연봉 미명시임을 반영

---

## v0.8.0 — 비교 테이블 개선·핀 필터 (2026-07-03)

**비교 테이블**
- **기술스택 칩 표시**: 콤마 텍스트 → 파란 pill 형태로 시각적 구분
- **강점/갭 스크롤**: max-height 160px + overflow-y scroll — 내용이 많아도 레이아웃 유지
- **잡플래닛 리뷰 수 병기**: 평점 옆에 `4.0 (810건)` 형태로 신뢰도 참고 가능
- **경력 요구 행 추가**: `experience_required` 필드 비교 가능
- **최고값 하이라이트**: 적합도·잡플래닛 평점 최고값 셀 녹색 배경 (`highlight-best`)
- **균등 열 너비**: `table-layout: fixed` 적용 — 내용 길이에 관계없이 열 폭 균등
- **비교 뷰 너비 확장**: `max-width: none`, `min-width: 800px` — 회사 수에 따라 자연스럽게 확장

**UI 개선**
- **핀 필터**: 대시보드 테이블 헤더 📌 클릭으로 즐겨찾기만 필터링 (`filterPinnedOnly` 토글)
- **버튼 레이아웃**: 비교 보기·선택 삭제 버튼을 필터 행에서 하단 행(`toolbar-sub`)으로 이동 — 필터 줄 레이아웃 안정화
- **프롬프트 개선**: 강점/갭 출력 형식 명시 — `(강) 항목명 - 근거 한 줄` 형태로 강도 표시가 항상 앞에 오도록

---

## v0.7.0 — 에러 처리 강화·CSV 내보내기 (2026-07-02)

**에러 처리**
- **스크래핑 오류 세분화**: `httpx.TimeoutException` / `HTTPStatusError` / 기타 네트워크 오류를 각각 구분해 사용자 친화적 메시지 반환
- **LLM API 오류 fallback**: `LLMAPIError` 공통 예외 클래스 추가 — 인증 실패(401)·rate limit(429)·서버 오류(503) 별도 처리. Anthropic·OpenAI 모든 메서드 적용
- **PDF 추출 실패 처리**: `PDFExtractError` — 암호화·손상 PDF 열기 실패 시 422 + 안내 메시지 (기존: 500 오류)

**기능 추가**
- **CSV 내보내기**: `GET /api/companies/export/csv` 엔드포인트. 대시보드 `↓ CSV` 버튼으로 전체 회사 데이터 다운로드. UTF-8 BOM 포함 (Excel 한글 호환). 리스트 필드(`tech_stack`, `strengths`, `gaps` 등)는 ` | ` 구분

**UI**
- **대시보드 툴바 레이아웃**: 제목 좌측 고정, 필터/정렬은 우측 상단 행, `탈락 보기`·`↓ CSV`는 우측 하단 행으로 분리

---

## v0.6.0 — 보안 강화·안정성 개선·slug 구조 변경 (2026-07-02)

**보안**
- **JWT 인증**: 로그인 응답을 JWT 토큰으로 변경 (PyJWT). 30일 만료, 서버 재시작 후에도 유효
- **Path traversal 방어**: slug 기반 경로를 `_safe_company_path()`로 검증 — `../` 탈출 차단
- **PDF 파일명 sanitization**: `Path(filename).name`으로 디렉토리 컴포넌트 제거
- **DOMPurify XSS 방어**: LLM 출력 HTML 삽입 시 `DOMPurify.sanitize()` 적용
- **편집 폼 필드 제한**: `fit_score`·`fit_label` PUT API에서 수정 불가 처리

**기능 개선**
- **slug 구조 변경**: `{회사명}__{직무명}` 형식으로 통일 — 같은 회사 다른 포지션 충돌 방지. 기존 파일 일괄 마이그레이션
- **파이프라인 타임아웃**: `from-url`·`from-text`·`from-image` 120초 초과 시 504 응답
- **진행 표시 UI**: 회사 추가 중 프로그레스 바 + 단계별 메시지 + 경과 시간 표시
- **Q&A 스트리밍 토큰 집계**: 스트림 완료 후 usage 기록 (Anthropic `get_final_message()` / OpenAI `include_usage`)
- **사용 이력 스크롤**: 최대 높이 고정, 데이터가 적으면 자동 축소, 헤더 sticky
- **누적 비용 표시**: usage 합계가 표시 항목과 불일치하던 문제 수정 — "누적 N건" 안내 추가

**안정성**
- **`list_companies()` 캐시**: 디렉토리 mtime 기반 캐시 — 변경 없으면 파일 재읽기 생략
- **SSE dangling reader 수정**: `navigate()`·`popstate` 시 활성 SSE reader `cancel()` 처리
- **AudioContext 싱글톤**: toast 호출마다 새 인스턴스 생성 → 전역 재사용
- **slug URL 인코딩**: API 호출 경로에 `encodeURIComponent(currentSlug)` 일괄 적용

**UI 추가**
- **Q&A 탭 회사 헤더**: Q&A 탭 진입 시 상단에 회사명·포지션명 표시
- **적합도 호버 패널**: Q&A 탭 우측 `📊 적합도 보기` 버튼 호버 시 패널 표시 — 적합도 점수·요구 스킬·강점·약점. JS 기반 hover 유지로 패널 내 스크롤 가능
- **상태 변경 자동 핀/언핀**: "지원" 상태로 변경 시 자동 핀, "미지원·탈락·보류·지원마감"으로 변경 시 자동 언핀

---

## v0.5.0 — 즐겨찾기·이미지입력·비용추적·UI개선 (2026-07-01)

**기능 추가**
- **이미지 업로드 공고 추가**: 스크린샷 여러 장 업로드 → OCR → 분석. 서버에서 1568px 자동 다운샘플링 (Pillow)
- **즐겨찾기(핀)**: 대시보드 상단 카드 섹션. `pinned` frontmatter 필드, `/pin` 토글 엔드포인트
- **탈락 공고 숨기기**: 탈락·지원마감 기본 숨김, "탈락 보기" 토글, `localStorage` 상태 유지
- **LLM 비용 추적**: `usage_tracker.py` — JSONL 로그 + 모델별 단가. 설정 화면에서 사용 이력 조회
- **브라우저 히스토리**: History API(`pushState`/`replaceState`)로 뒤로가기·앞으로가기·URL 직접 접근 지원
- **스크래핑 안내**: 새 회사 추가 탭 위에 원티드·리멤버(URL) vs 사람인·잡코리아(텍스트/이미지) 구분 안내

**UI 개선**
- 새 회사 추가: "텍스트 붙여넣기" + "직접 입력" 탭 통합 → "텍스트 입력" (회사명·직무명 필수 입력, 텍스트 없으면 수동 저장)
- 편집 폼: 저장 버튼 상단 배치, 섹션 분리(기본정보/공고URL/회사정보/태그/내용)
- 브랜드명(`display_name`) UI 제거 → 회사명 하나로 통일 (백엔드 필드는 유지)
- 적합도 점수·라벨 편집 제거 → LLM 전용, 지원 상태만 수동 편집 가능

---

## v0.4.0 — 필터링·보안·커스텀 평가 기준 (2026-06-28)

**기능 추가**
- **커스텀 평가 기준**: 설정 페이지에서 자유 텍스트로 평가 기준 입력 → 적합도 평가 LLM 프롬프트에 자동 주입 (`data/eval_criteria.md` 저장)
- **리멤버 커리어 스크래핑**: `career.rememberapp.co.kr` URL 자동 파싱 (`__NEXT_DATA__` JSON)
- **대시보드 복합 필터**: 지원 상태·적합도 점수·검색어·정렬 동시 적용 (`applyFilters()`)
- **지원마감 상태 추가**: `status` 8번째 옵션
- **상세 페이지 상태 드롭다운**: 목록뿐 아니라 상세 페이지에서도 인라인 상태 변경 가능
- **공고 원문 링크 강조**: 배지 스타일로 가시성 개선
- **URL 중복 감지**: 동일 URL 재등록 시 409 + 기존 항목으로 자동 리다이렉트

**버그 수정 / 안정화**
- Q&A 한국어 IME 중복 입력 버그 (`e.isComposing` 체크)
- SSE 오류 전파 (`_make_sse()` try-except → `error` 이벤트)
- `refill` 재분석 시 새 파일 생성 버그 (`existing_slug` 파라미터)
- Claude thinking 블록 파싱 오류 (`hasattr(block, "text")` 체크)
- LLM 클라이언트 싱글톤 캐시 (매 요청마다 재생성 방지)
- XSS 방어: `escHtml()` · `safeHref()` 헬퍼 전면 적용
- 일괄 삭제 `Promise.allSettled` (부분 실패 피드백)

---

## v0.3.0 — UX 개선 + Docker MSA (2026-06-19)

- **원문 저장**: 회사 추가 시 `{slug}.raw.txt`로 원문 공고 텍스트 보관 → refill 재분석 품질 개선
- **일괄 삭제**: 대시보드 목록에서 체크박스 선택 후 일괄 삭제 지원
- **인라인 상태 변경**: 목록에서 지원 상태를 드롭다운으로 즉시 변경
- **Docker MSA**: 단일 컨테이너 → nginx(프론트 서빙) + api(백엔드) 분리
- **FastAPI lifespan 마이그레이션**: deprecated `on_event` 제거
- **Dockerfile**: 불필요한 `libpango` 의존성 제거

---

## v0.2.0 — 안정화 및 기능 보완 (2026-06-18)

- **Wanted 스크래퍼 수정**: `pageProps.job` → `pageProps.initialData` 구조 변경 대응 (직무명 오추출 버그 수정)
- **잡플래닛 수집 개선**: DuckDuckGo → Naver `site:jobplanet.co.kr` 쿼리로 전환 (소규모 회사도 검색 가능, DuckDuckGo rate limit 우회)
- **회사명 오탐 방지**: prefix 기반 유사도 매칭 (`_match_score`) 도입
- **새 필드 추가**: `jobplanet_review_count`, `salary_min`, `salary_max`, `salary_note`, `application_source`
- **LLM 스키마**: `jobplanet_score` 추출 제거 (별도 스크래핑으로 분리), 연봉 필드 추가
- **저장소**: `exclude_none=True` (null 필드 미저장), 원자적 파일 쓰기(`os.replace`)
- **로깅**: `main.py` 단계별 로그 (`[1/4] ~ [4/4]`), `storage.py` 파싱 실패 경고
- **섹션 번호 충돌 수정**: LLM은 1~3 생성, `main.py`가 4(적합도)/5(로그) 추가
- **외부 접근**: `host=127.0.0.1` → `host=0.0.0.0`으로 변경

---

## v0.1.0 — 초기 구현

- FastAPI 백엔드 + Vanilla JS SPA (빌드 없음)
- LLM 추상화 레이어 (Claude / OpenAI, Lightweight / High 티어)
- PDF 이력서·포트폴리오 업로드 → High 티어 LLM으로 프로필 추출
- URL / 텍스트 붙여넣기 / 수동 입력으로 회사 추가
- Wanted `__NEXT_DATA__` JSON 파싱 (JS 렌더링 없이 스크래핑)
- 잡플래닛 평점 자동 수집 (검색 엔진 스니펫 파싱)
- 후보자 프로필 기반 적합도 평가 (0~100점, 강점/갭)
- Q&A 스트리밍 (단일/다중 회사)
- 비교 뷰 (사이드바이사이드 테이블)
- Docker Compose 배포 환경
