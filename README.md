# Job FitCheck

구직 활동 중 조사한 회사 정보를 LLM으로 자동 파싱·정리·적합도 평가하고, 로컬 마크다운 파일로 아카이빙하는 웹앱.

## 주요 기능

- **공고 자동 분석** — URL·텍스트·이미지 입력 지원 (원티드, 리멤버 등)
- **잡플래닛 평점 자동 수집** — 평점·리뷰 수 자동 조회
- **적합도 평가** — 이력서 대비 0~100점, 커스텀 기준 지원
- **Q&A 채팅** — 공고 기반 면접 준비 질문 응답
- **대시보드** — 필터·검색·핀·비교 테이블, 탈락 공고 숨기기
- **지원 현황 관리** — 타임라인·캘린더 뷰, 8가지 상태
- **내보내기** — ZIP·CSV·자동 백업
- **로컬 저장** — DB 없음, 데이터 로컬 유지
- **멀티 LLM** — Claude / OpenAI 전환, 비용 추적

## 시작하기

### 1. 환경 설정

```bash
cp .env.example .env
```

`.env` 파일에 API 키 입력:

```env
ANTHROPIC_API_KEY=sk-ant-...
APP_SECRET=your-password       # 로그인 비밀번호
OPENAI_API_KEY=sk-...          # 선택 (Claude만 써도 됨)
```

### 2. 실행

```bash
docker compose up --build
```

브라우저에서 `http://localhost` 접속 → 비밀번호(`APP_SECRET`) 입력.

### 3. 첫 사용 순서

1. **설정** 탭 → 이력서/포트폴리오 PDF 업로드 → 프로필 자동 생성
2. **회사 추가** → URL 붙여넣기 또는 공고 텍스트·이미지 입력 → AI 분석
3. **대시보드** → 회사 목록 확인, 상태 변경, 비교, Q&A

## LLM 모델

작업 성격에 따라 자동으로 적절한 모델을 선택합니다.

| 작업 | Claude | OpenAI | Gemini |
|---|---|---|---|
| 공고 구조화·요약 | claude-haiku-4-5 | gpt-5-mini | gemini-2.5-flash-lite |
| 프로필 추출·적합도 평가·Q&A | claude-sonnet-4-6 | gpt-5 | gemini-2.5-flash |

설정 화면에서 모델을 수동으로 변경할 수 있습니다.

## 변경 이력

[CHANGELOG.md](CHANGELOG.md)
