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
├── uploads/          # 업로드 PDF 임시 저장
└── backup/           # 삭제 직전 자동 백업 (최근 5개 유지)
```

`data/` 디렉토리는 git 추적 대상에서 제외됩니다.
