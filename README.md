# Job FitCheck

구직 활동 중 조사한 회사 정보를 LLM으로 자동 파싱·정리·적합도 평가하고, 로컬 마크다운 파일로 아카이빙하는 웹앱.

> 처음이라 Docker나 터미널이 낯서신가요? **[처음 시작하기 가이드](GETTING_STARTED.md)** 를 참고하세요. 아래 내용은 이미 사용해보신 분들을 위한 빠른 참조입니다.

> 🧪 이 브랜치(`rag/main`)에는 GPU 환경이 있는 분들을 위한 실험적 RAG 서브프로젝트가 함께 들어있습니다. main 브랜치의 일반 사용 경험과는 분리되어 있으니, 관심 있으시면 **[backend/rag/README.md](backend/rag/README.md)** 를 참고하세요.

## 주요 기능

- **공고 자동 분석** — URL·텍스트·이미지 입력 지원 (원티드, 리멤버 등)
- **잡플래닛 평점 자동 수집** — 평점·리뷰 수 자동 조회
- **적합도 평가** — 이력서 대비 0~100점, 커스텀 기준 지원
- **Q&A 채팅** — 공고 기반 면접 준비 질문 응답
- **대시보드** — 필터·검색·핀·비교 테이블, 탈락 공고 숨기기
- **지원 현황 관리** — 타임라인·캘린더 뷰, 8가지 상태
- **내보내기** — ZIP·CSV·자동 백업
- **로컬 저장** — DB 없음, 데이터 로컬 유지
- **멀티 LLM** — Claude / OpenAI / Gemini 전환, 비용 추적
- **다크모드** — 우측 상단 토글, 설정값 브라우저에 저장
- **분석 완료 알림** — 텔레그램·슬랙·디스코드 중 설정된 채널로 자동 전송, 알림 내용(강점·갭·잡플래닛 평점·임직원 수) 커스터마이즈 가능
- **주간 지원 현황 요약 알림** — 신규 등록 건수·상태별 현황·방치된 항목(7일 이상 진행 없음)을 원하는 요일·시각(기본 월요일 09:00)에 알림으로 전송 (설정에서 켜기)

## 시작하기

### 1. 환경 설정

```bash
cp .env.example .env
```

`.env` 파일에 API 키 입력:

```env
GOOGLE_API_KEY=AIza...          # 기본 provider, 무료 티어로 바로 체험 가능
APP_SECRET=your-password        # 로그인 비밀번호
ANTHROPIC_API_KEY=sk-ant-...    # 선택 (추천 — 더 정확한 분석 원하면 설정에서 Claude로 전환)
OPENAI_API_KEY=sk-...           # 선택
```

### 2. 실행

```bash
docker compose up --build
```

브라우저에서 `http://localhost:8000` 접속 → 비밀번호(`APP_SECRET`) 입력.

> **Docker 없이 더 가볍게 실행하고 싶다면** `run/start-uv.command`(Mac)/`run/start-uv.bat`(Windows)을 대신 실행하세요. `uv`가 없으면 설치 여부를 물어보고 자동으로 설치한 뒤 이어서 실행합니다(재부팅·가상화 설정 불필요). 첫 실행 시 `.env` 설정도 같이 물어봐서 별도 설정 스크립트가 필요 없습니다. 자세한 순서는 [GETTING_STARTED.md](GETTING_STARTED.md) 참고.

### 3. 첫 사용 순서

1. **설정** 탭 → 이력서/포트폴리오 PDF 업로드 → 프로필 자동 생성
2. **회사 추가** → URL 붙여넣기 또는 공고 텍스트·이미지 입력 → AI 분석
3. **대시보드** → 회사 목록 확인, 상태 변경, 비교, Q&A

## 알림 설정

공고 분석이 끝나면 설정된 채널로 결과를 자동 전송합니다. 텔레그램·슬랙·디스코드 중 원하는 채널만 `.env`에 값을 넣으면 되고, 여러 채널을 동시에 켜도 됩니다(설정 안 된 채널은 자동으로 스킵).

```env
TELEGRAM_BOT_TOKEN=...   # 텔레그램 봇 토큰
TELEGRAM_CHAT_ID=...     # 알림 받을 채팅 ID
SLACK_WEBHOOK_URL=...    # 슬랙 Incoming Webhook URL
DISCORD_WEBHOOK_URL=...  # 디스코드 Incoming Webhook URL
```

기본 알림에는 회사명·직무·적합도 점수가 포함되며, **설정** 탭에서 아래 항목을 추가로 켜고 끌 수 있습니다.

- 강점 요약 / 갭 요약 (기본 ON)
- 잡플래닛 평점 / 임직원 수 (기본 OFF)
- 주간 지원 현황 요약 — 요일·시각 직접 선택 가능(기본 월요일 09:00), 기본 OFF

채널별로 굵게 표시 문법(텔레그램 HTML `<b>`, 슬랙 `*mrkdwn*`, 디스코드 `**markdown**`)을 자동 적용해 각 앱에서 헤더가 굵게 보입니다.

## LLM 모델

작업 성격에 따라 자동으로 적절한 모델을 선택합니다.

| 작업 | Claude | OpenAI | Gemini |
|---|---|---|---|
| 공고 구조화·요약 | claude-haiku-4-5 | gpt-5-mini | gemini-3.1-flash-lite |
| 프로필 추출·적합도 평가·Q&A | claude-sonnet-4-6 | gpt-5 | gemini-3.5-flash |

설정 화면에서 모델을 수동으로 변경할 수 있습니다.

### Gemini 무료 티어 (기본 provider)

기본 provider는 Gemini입니다 — **Google AI Studio 무료 티어**로 별도 결제 없이 바로 체험할 수 있습니다. [Google AI Studio](https://aistudio.google.com/apikey)에서 API 키를 발급받아 `GOOGLE_API_KEY`에 설정하세요.

**무료 티어 제한 (2026-07 기준):**
- 모델당 일일 20회 호출 (프로젝트 × 모델 단위 별도 카운트)
- 모델이 다르면 할당량 독립 (예: 2.5-flash 소진돼도 3.5-flash 사용 가능)
- 분당 요청 수(RPM) 제한 있음 — 429 에러 발생 시 20초 대기 후 1회 자동 재시도, 그래도 실패하면 한도 초과 안내와 함께 즉시 실패 처리됨

> 일일 호출 횟수가 제한적이고 분석 품질도 Claude가 더 정확한 편이라, 무료로 가볍게 시작하고 싶다면 Gemini 그대로, 더 정확한 분석을 원하면 `ANTHROPIC_API_KEY`를 추가하고 설정 화면에서 provider를 Claude로 전환하는 것을 추천합니다.

### 사용 비용 추정치

공고 하나를 넣었을 때 Provider별로 대략 얼마가 나가는지를 정리한 표입니다. 공고 1건 분석은 Light 모델(구조화 추출·본문 생성) + High 모델(적합도 평가) 호출을 합친 값입니다.

> **주의**: 아래 금액은 각 Provider 콘솔에서 실제 청구 내역을 조회한 값이 아니라, 호출당 사용된 토큰 수 × 앱에 등록된 공개 단가표를 곱해 계산한 **추정치**입니다(`usage_tracker.py` 기준). 실제 청구액과 다를 수 있습니다 — 정확한 금액은 각 Provider의 결제 대시보드(OpenAI Usage, Anthropic Console, Google AI Studio 등)에서 직접 확인하세요.

**공고 1건 분석**

| Provider | 모델 조합 (Light + High) | 건당 평균 비용 |
|---|---|---|
| Claude | haiku-4-5 + sonnet-4-6 | $0.1109 (63건) |
| OpenAI | gpt-5-mini + gpt-5 | $0.0623 (9건) |
| Gemini | 3.1-flash-lite + 3.5-flash | $0.0584 (8건) |

**적합도 재평가 (refit)**

| Provider | 모델 (High) | 건당 평균 비용 |
|---|---|---|
| Claude | sonnet-4-6 | $0.1027 (18건) |
| OpenAI | gpt-5 | $0.0550 (10건) |
| Gemini | 3.5-flash | $0.0595 (7건) |

**프로필 생성**

| Provider | 모델 (High) | 건당 평균 비용 |
|---|---|---|
| Claude | sonnet-4-6 | $0.1386 (8건) |

## 변경 이력

[CHANGELOG.md](CHANGELOG.md)

## 라이선스

[MIT](LICENSE)
