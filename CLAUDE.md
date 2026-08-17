# Job FitCheck — Claude Code 가이드

채용공고를 LLM으로 분석·저장·비교하는 개인용 웹앱.
FastAPI 백엔드 + Vanilla JS SPA + 로컬 마크다운 파일 저장소 구조.

---

## 빠른 시작

```bash
cp .env.example .env          # API 키 입력
docker compose up --build -d
```

브라우저에서 `http://localhost:8000` 접속 → 비밀번호(`APP_SECRET`) 입력.

---

## API 키 설정

### 필수

| 키 | 용도 | 발급처 |
|---|---|---|
| `GOOGLE_API_KEY` | Gemini API (기본 provider, 무료 티어로 바로 체험 가능) | https://aistudio.google.com/apikey |
| `APP_SECRET` | 로그인 비밀번호 (자유롭게 설정) | 직접 지정 |

### 선택 (추천)

| 키 | 용도 | 발급처 |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API — 더 정확한 분석 품질을 원하면 추천, 설정에서 provider를 Claude로 전환 | https://console.anthropic.com |

### 선택

| 키 | 용도 |
|---|---|
| `OPENAI_API_KEY` | OpenAI GPT 사용 시 (설정에서 provider 전환) |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | 분석 완료 시 텔레그램 알림 |
| `SLACK_WEBHOOK_URL` | 분석 완료 시 슬랙 알림 (Incoming Webhook) |
| `DISCORD_WEBHOOK_URL` | 분석 완료 시 디스코드 알림 (Incoming Webhook) |

> Gemini 키만 있어도 기본 기능은 모두 동작합니다(무료 티어는 모델당 일일 20회 제한). 더 정확한 분석을 원하면 Claude API 사용을 추천합니다.

---

## ⚠️ 핵심 주의사항

### 백엔드 코드 수정 후 반드시 재빌드

```bash
docker compose up --build -d   # 코드 변경 반영
docker compose restart api     # ❌ 이미지가 캐시되어 변경사항 미반영
```

프론트엔드(`frontend/`)는 볼륨 마운트라 새로고침만으로 반영됩니다.

---

## 아키텍처 결정

- **DB 없음**: 회사 정보는 `data/companies/{slug}.md` 마크다운 파일로 저장. 검색·필터는 메모리 내 처리. 데이터가 수백 개 미만인 개인 사용 기준 충분.
- **RAG(선택 기능)는 이 원칙의 예외가 아니라 opt-in 계층**: 핵심 데이터(회사 정보)는 여전히 마크다운 그대로 두고, `RAG_POSTGRES_HOST` 설정 시에만 PostgreSQL+pgvector가 추가로 붙어 자연어 채팅 검색을 제공한다. 미설정 배포는 Postgres 관련 코드가 아예 안 쓰인다(`routers/rag.py`가 `/status` 외 전부 503 가드). 사용자 가이드는 `RAG_GUIDE.md`, 코드 구조는 `backend/rag/README.md`, 개발 이력은 `docs/rag-integration/`(git 미추적) 참고.
- **프로필 히스토리(항상 켜짐)는 SQLite 사용**: 핵심 데이터(회사 정보·프로필)는 여전히 마크다운 파일 그대로 두고, 프로필 스냅샷·회사별 적합도 평가 이력만 `data/app.db`(SQLite, 단일 파일)에 별도 기록한다. RAG의 Postgres와 달리 opt-in이 아니라 항상 켜져 있지만, 별도 서버 프로세스가 아닌 로컬 파일 하나라 "DB 없음"(별도 DB 서버 미의존) 원칙과 어긋나지 않는다. DB 장애 시(`init_db()` 실패) 이력 조회 API는 503을 반환하되 회사 CRUD 등 핵심 기능은 영향 없음(`backend/services/app_db.py`의 `is_healthy()`). `backend/services/app_db.py` 참고.
- **LLM 티어**: Lightweight = 구조화 추출·요약, High = 프로필 추출·적합도 평가·Q&A. 설정 화면에서 모델 수동 변경 가능.
  - Claude 기본: Light=haiku-4-5, High=sonnet-4-6
  - OpenAI 기본: Light=gpt-5-mini, High=gpt-5 (reasoning_effort=medium)
  - Gemini 기본: Light=gemini-3.1-flash-lite, High=gemini-3.5-flash
  - `reasoning_effort`는 gpt-5/gpt-5.x 계열에만 자동 적용. PUT `/api/settings`로 변경 가능.
- **FastAPI 경로 순서 주의**: `/api/companies/timeline`은 반드시 `/api/companies/{slug}` 앞에 등록해야 충돌하지 않음.
- **원자적 파일 쓰기**: `.tmp` → `os.replace()` 순서로 처리해 크래시 시 파일 손상 방지.

---

## 자주 쓰는 명령어

```bash
# 실행
docker compose up --build -d          # 전체 빌드 + 백그라운드 실행
docker compose logs -f api            # 백엔드 로그 실시간 확인
docker compose down                   # 중지

# 로컬 직접 실행 (개발)
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
python3 backend/main.py               # http://localhost:8000
```

---

## 데이터 구조

```
data/
├── companies/        # {회사명}__{직무명}.md + .raw.txt
├── candidate_profile.md
├── eval_criteria.md
├── usage_log.jsonl
├── runtime_settings.json  # provider/모델/알림설정/주간요약 스케줄 — 재시작 후에도 유지
├── app.db             # SQLite — 프로필 스냅샷 + 회사별 적합도 평가 이력 (전체 ZIP/삭제 전 자동 백업에 포함)
├── uploads/          # 업로드 PDF 임시 저장
└── backup/           # 삭제 직전 자동 백업 (최근 5개 유지)
```

`data/` 디렉토리는 git 추적 대상에서 제외됩니다.

RAG(선택 기능)가 켜져 있으면 `rag-postgres` 컨테이너(docker volume, `data/`와 별도)에 청크·임베딩이 저장된다 — `docker compose --profile rag up`으로만 뜬다.
