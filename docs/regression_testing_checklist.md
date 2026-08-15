# 전체 앱 회귀 테스트 체크리스트

`feat/rag-integration-plan` → `main` merge 직후 회귀 확인용. anthropic/openai/google-genai/
httpx/pydantic 등 여러 기능이 공유하는 라이브러리 버전이 이번 merge로 함께 올라가서, RAG를
안 쓰는 사용자에게도 영향 범위가 있다. 우선순위(P0/P1/P2) 순서로 실행 — P0가 깨지면 뒤는
진행하지 않는다. RAG 자체 기능 검증은 [rag_testing_checklist.md](rag_testing_checklist.md)
참고(여기서는 링크만, 중복 작성 안 함).

## P0 — 빌드·기동·인증 (실패 시 진행 중단)

### 0. 빌드·기동·라우팅

- [x] `docker compose up --build -d` → API/nginx 정상 기동 (2026-08-14, `f882c00` 기준)
- [x] `GET /api/health` 정상 응답
- [x] 정적 파일(SPA) 정상 로딩 (Playwright)
- [x] `RAG_POSTGRES_HOST` 미설정 상태 → `/api/rag/status` 외 RAG 엔드포인트 503, 나머지
      기능 정상 (`/reindex`·`/ask` 503 확인, `/status`는 `{"enabled":false}`)
- [x] SPA 직접 URL 진입, 새로고침, 브라우저 뒤로가기 정상 동작 (Playwright — `/rag` 직접
      진입 시 대시보드로 정상 리다이렉트)
- [x] uv 직접 실행 경로(`python3 backend/main.py`)로도 기존 기능 정상 — `.venv`를 merge된
      `requirements.txt`로 동기화(cryptography 48.0.1/psycopg/pgvector 등 정상 설치) 후
      Docker와 충돌 없게 8001 포트로 별도 기동. `/api/health`·SPA 정적 서빙·로그인·
      `/api/companies`(90건)·`/api/rag/status`(비활성) 전부 정상, 종료 후 Docker
      서비스(8000)도 영향 없음 확인(2026-08-15)

### 1. 인증

- [x] 올바른 `APP_SECRET`으로 로그인 성공 (Playwright)
- [x] 잘못된 비밀번호 → 로그인 거부(UI) + API 레벨 401 확인
- [x] 토큰 없이 보호된 API 호출 → 401
- [x] 컨테이너 재시작 후에도 기존 세션 정상 동작 — 설정 테스트 중 `docker compose restart
      api`를 2회 실행했는데 재시작 전 발급받은 토큰이 계속 정상 동작함(JWT는 상태 없이
      `APP_SECRET`으로만 검증되는 구조라 재시작 영향 없음, 2026-08-14)

## P1 — 핵심 기능 회귀 (라이브러리 버전 변경 영향권)

### 2. 회사 등록·분석

- [x] URL 입력으로 등록 → 스크래핑(`httpx`) + LLM 분석 정상 — 사용자 제공 무관 URL(원티드
      378976)로 등록 200 확인, 구조화 분석 정상(fit_score 5, 직무 무관성 반영). 테스트 후
      회사 레코드 삭제 + 자동 생성된 백업 zip까지 완전히 제거(2026-08-14)
- [x] 텍스트 붙여넣기로 등록 → 구조화 추출 정상 (`POST /companies/from-text`, Claude,
      2026-08-14 — `fit_score`/`tech_stack`/`strengths`/`gaps` 전부 정상 생성)
- [x] 이미지 업로드로 등록 → OCR/분석 파이프라인 정상 — PIL로 만든 한글 채용공고 이미지
      업로드, OCR+구조화 분석 정상(fit_score 72, tech_stack 정확 추출), 결과물+백업 zip
      완전 삭제(2026-08-14)
- [x] Wanted 동기화 정상 — 사용자 제공 무관 URL(원티드 378976)로 재등록 후 `/sync-wanted`
      호출, 임직원수·웹사이트·매출상태 등 실제 데이터로 정상 갱신 확인. 결과물+백업 zip
      완전 삭제(2026-08-14)
- [x] Jobplanet 검색 연동 정상(파이프라인 자체는 정상, **기존 버그 발견**) — 핏펫·카카오로
      각각 텍스트/URL 등록 테스트, 둘 다 `jobplanet_score: null`("not_found"). 원인
      직접 재현: 카카오는 실제로 잡플래닛 후보가 검색되지만(리뷰 1,307건), Naver 스니펫이
      길면 평점 숫자 앞에서 잘려 정규식이 매치를 못 하고, 평점이 포함된 스니펫(예:
      "카카오모빌리티")은 회사명 유사도 0.43 < 임계값 0.5라 후보에서 탈락 — 검색·호출
      경로 자체는 정상 동작(200 OK, 후보 파싱까지 성공), 매칭 로직의 기존 한계.
      **`backend/services/jobplanet.py`는 이번 merge에서 전혀 수정되지 않음(diff 없음
      확인) — merge로 생긴 회귀 아니라 기존 버그, 여기서는 정상 동작 확인용으로만 기록,
      별도 수정은 이 체크리스트 범위 밖.** 테스트 회사·백업 zip 완전 삭제, provider는
      OpenAI→Claude로 원복(2026-08-15)
- [x] refill(정확히는 refit, 재평가) 정상 — `POST /{slug}/refit` 200, 재평가 결과 갱신 확인
- [x] provider 3종(Claude/OpenAI/Gemini) 중 키가 있는 것 각각 최소 1회 위 동작 확인 —
      등록/평가는 Claude로 실행, OpenAI/Gemini는 provider 전환 + `/api/models` + QA 1회로
      SDK 동작만 확인(아래 4번과 겹쳐서 등록을 반복하지 않음)

### 3. 프로필·적합도 평가

- [x] 프로필 PDF 업로드 → 파싱 정상 — 백업 후 더미 이력서 PDF 업로드, LLM 구조화 추출
      정상(이름/기술스택/경력 등) 확인, 즉시 원본 백업으로 복원 후 md5 일치 확인
      (2026-08-14). **부수 발견**: 업로드는 컨테이너(root)가 파일을 쓰기 때문에
      `data/candidate_profile.md` 소유자가 root로 바뀜 — 호스트 사용자(jhlee) 권한으로는
      직접 덮어쓰기 불가, 복원 시 `sudo cp` 필요했음(버그 아니라 기존 Docker 볼륨 마운트의
      알려진 특성)
- [ ] 프로필 수동 편집 저장 정상 — 미실행, 사용자 판단으로 생략(업로드 경로는 확인됐고
      PUT 왕복은 리스크 낮음, 2026-08-15)
- [x] 프로필 있는 상태에서 적합도 평가 정상 — 위 2번 등록 시 실제 프로필(휴레이포지티브·
      비브스튜디오스 경력)이 반영된 strengths/gaps 생성 확인
- [x] 프로필 없는 상태에서 적합도 평가 시 안내 정상(에러 아님) — `data/candidate_profile.md`
      임시 백업 후 제거 → 등록 시 `fit_score: null`, `strengths/gaps: []`로 정상 처리(크래시
      없음), 즉시 원본 복원 후 체크섬(md5) 일치 확인(2026-08-14)
- [x] 프로필 재업로드 후 갱신된 내용 기준으로 평가 반영 확인 — 백업 → 더미 이력서(Dummy
      Corp) 재업로드 → 새 테스트 회사 등록 시 strengths가 전부 "Dummy Corp" 경력 근거로
      생성됨(실제 프로필 아닌 새 프로필 반영 확인) → 테스트 회사+백업 zip 삭제, 원본
      프로필 복원 후 체크섬 일치 확인(2026-08-14)
- [x] `GET /api/profile/status`, `GET /api/profile` 정상 응답(읽기 전용 확인)

### 4. 기존 Q&A (SSE 스트리밍)

- [x] 질문 전송 → 스트림 시작·완료 정상 (Claude/OpenAI/Gemini 3종 모두 `data: ...` →
      `data: [DONE]`로 정상 종료 확인)
- [x] 스트리밍 도중 취소 정상 — 클라이언트가 1.5초만에 연결 강제 종료(curl `--max-time`),
      서버 로그에 에러/traceback 없음, 직후 `/api/health` 200 정상(2026-08-14)
- [x] 멀티턴 history 문맥 반영 정상 — 서버는 history를 저장하지 않고 요청마다 프론트가
      실어보내는 구조(조회 API 없음, 항목명을 "저장·조회"에서 수정)임을 확인. 1턴에서
      번호 매긴 답변을 받고 2턴에 history로 실어 "2번째 항목만 다시 말해줘" 요청 →
      1턴 답변의 실제 2번 항목을 정확히 반환, 멀티턴 컨텍스트 정상 반영 확인(2026-08-14)
- [x] provider 3종 중 키가 있는 것 각각 최소 1회 확인 (Claude/OpenAI/Gemini 전부 200 +
      정상 스트림)

### 5. 설정

- [x] provider 전환 정상 반영 (claude→openai→gemini→claude, 매번 `{"status":"ok"}` 확인)
- [x] 모델 목록 조회 정상 (`GET /api/models?provider=` 3종 전부 200, 목록 반환)
- [x] 모델 수동 override 저장 정상 (`claude_high_model` 변경·반영 확인)
- [x] `reasoning_effort`(gpt-5 계열) 설정 반영 확인 (`medium`→`high` 저장·반영 확인)
- [x] 알림 설정 저장 정상 (`notify_jobplanet_rating` 토글 확인)
- [x] 주간 요약 스케줄 설정 저장 정상 (`weekday`/`time` 변경 확인)
- [x] 컨테이너 재시작 후 위 설정들이 `runtime_settings.json`에서 복원됨 — `docker compose
      restart api` 후 5개 항목 전부 유지 확인. 테스트 후 `runtime_settings.json` 백업본으로
      복원, 체크섬 일치 + API 응답으로 원래 값(medium/notify_jobplanet_rating=false/
      weekday=0 등) 재확인(2026-08-14)

### 6. 알림

- [x] 텔레그램 테스트 메시지 발송 정상 — 회사 등록(알림테스트) 트리거로 실제 발송,
      API 200 + 사용자가 폰으로 수신 확인(2026-08-14)
- [x] 슬랙 테스트 메시지 발송 정상 — dev `.env`의 `SLACK_WEBHOOK_URL`을 prod에 임시로
      가져와 검증(API 200, 사용자 수신 확인), 테스트 후 즉시 원복하고 prod는 기존대로
      텔레그램만 유지(2026-08-14)
- [x] 디스코드 테스트 메시지 발송 정상 — 같은 방식(API 204, 사용자 수신 확인), 같은 이유로
      prod에는 유지 안 함
- [x] prod 기본 상태는 텔레그램만 — `.env` 원복 후 md5 체크섬 일치 확인, 슬랙/디스코드는
      dev 전용으로 유지

### 7. RAG 비활성 상태에서 CRUD 훅 부작용 없음

이번 merge로 회사·프로필 CRUD 경로에 RAG 재색인 훅이 새로 추가됐다 — RAG를 안 쓰는
사용자에게 이 훅이 조용히 실패하거나 지연을 유발하지 않는지가 핵심.

- [x] 회사 등록/편집/refit/삭제 각각 실행 → 정상 동작, 응답 지연 없음 (Wanted 동기화는
      미실행)
- [x] 프로필 편집·업로드 → 정상 동작 — 3번 카테고리 테스트에서 이미 확인(PDF 업로드 2회,
      RAG 훅 관련 에러 없음)
- [x] API/컨테이너 로그에 Postgres 연결 시도나 관련 오류 없음 (`docker compose logs`로
      확인, RAG/error/traceback 관련 로그 없음)

## P2 — UI·관리 기능·RAG

### 8. 회사 관리 SPA

`frontend/app.js`에 RAG 관련 코드가 다수 추가되고 공용 라우팅·초기화 흐름도 같이
수정됐으므로, RAG 화면만이 아니라 기존 SPA 전반을 확인한다.

- [x] 회사 목록·상세 조회 정상 — Playwright로 목록 78건(전체 90건 중 탈락/마감 12건은
      기본 숨김, 정상) 표시, 회사 클릭 시 상세 화면 정상 렌더링, 콘솔 에러 없음(2026-08-15)
- [x] 검색·필터·정렬 정상 — "백엔드" 검색 78→2건, 고득점 필터 78→7건, 적합도순 정렬 후
      78,72,72...62,62 내림차순 스크린샷으로 확인
- [x] 핀 고정/해제 정상 — 실제 회사 1곳으로 미고정→고정→미고정 토글, 원래 상태로 정확히
      복원됨 확인
- [x] 비교 화면 정상 — 회사 3곳 선택 후 비교 화면에서 표로 나란히 정상 렌더링(기술스택/
      강점/갭 등 전부 표시)
- [x] 타임라인 정상 — 정상 진입, 실제 데이터가 대부분 미지원 상태라 "지원한 회사가
      없습니다" 빈 상태 정상 표시(탈락 10건 카운트는 정상 표시)
- [x] `/rag` 화면 방문 후 기존 화면(목록/상세 등)으로 돌아왔을 때 상태 이상 없음 — 브라우저
      뒤로가기·인앱 링크 클릭 둘 다로 목록 복귀 확인(78건 그대로), 이후 상세 화면 진입도
      정상, 콘솔 에러 없음(2026-08-15)
- [x] 모바일 화면 폭에서 주요 버튼 정상 노출·동작 — 390px 폭에서 목록 상단 툴바 정상
      스택, 표는 `.table-wrap`의 `overflow-x: auto`로 표 영역만 가로 스크롤(페이지 전체는
      `body.scrollWidth`=뷰포트 폭과 일치, 안 밀림) — 의도된 반응형 구조 확인. 상세 화면
      탭·버튼도 겹침 없음

### 9. 백업·내보내기

- [x] ZIP 내보내기 → 압축 정상, `.md`/`.raw.txt`/프로필/평가 기준 포함 확인 — 200, 701KB,
      회사 `.md` 92개(90+@)·`.raw.txt` 90개·`candidate_profile.md`·`eval_criteria.md`
      전부 포함 확인(2026-08-15)
- [x] CSV 내보내기 정상 — 200, 91줄(90개 회사+헤더), 필드 정상 이스케이프 확인
- [x] 프로필 export 정상 — 200, 336줄 실제 내용 확인
- [x] 회사 삭제 시 `data/backup/`에 자동 백업 생성 확인(최근 5개 유지) — 테스트 회사 3개를
      연속 등록·삭제해서 백업 4→7개까지 만든 뒤 **정확히 5개로 회전**(가장 오래된 2개
      자동 삭제) 확인. 백업은 삭제 대상 회사만이 아니라 그 시점 전체 데이터를 담는
      진짜 스냅샷이라 삭제하지 않고 그대로 보존
- [x] RAG Postgres 데이터는 export에 없어도 정상 — `build_export_zip()`이
      `companies_dir`/프로필/평가기준만 담당하고 RAG 관련 코드를 아예 참조하지 않음을
      코드로 확인, 별도 실행 테스트 불필요

### 10. RAG (선택 기능)

- [x] [rag_testing_checklist.md](rag_testing_checklist.md) 전체 항목 실행 — 25개 중 24개
      통과, 1개(로컬 provider 성공 전환)는 개인 GPU 서버가 꺼져있어 미실행(실패 시나리오는
      확인 완료, 관련 문서 공백은 해결)
