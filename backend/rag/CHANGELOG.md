# RAG 서브프로젝트 Changelog

`rag/main` 브랜치 전용 — main 브랜치엔 없다. 버전 번호는 루트 `CHANGELOG.md`(앱 버전, `v1.x.x`)와
겹치지 않게 별도 네임스페이스(`rag-v0.x.y`)를 쓴다. `rag/main`은 main에 절대 merge하지 않는 영구
서브프로젝트 브랜치다(2026-07-24 확정, `docs/rag-project-plans/00_meta/STATUS.md`의 "브랜치 전략" 참고) —
버저닝도 git 태그 없이 이 파일의 버전 번호만으로 관리한다.

상세 이력·설계 논의는 `docs/rag-project-plans/00_meta/HISTORY.md`(git 미추적)에 exhaustively
기록돼 있음 — 이 파일은 그걸 압축한 요약.

## rag-v0.19.6 — `query_router.py` 옛 라우팅 레거시 삭제 (2026-07-30)

`QUERY_TYPES`/`CLASSIFY_SYSTEM`/`CLASSIFY_TOOL_*`/`UNANSWERABLE_MESSAGE`/`classify_query()`/
`answer_query()` — Phase 1~4의 "질문을 7종으로 분류 → 고정 함수 실행" 라우팅 시스템 전체를
실제로 삭제(그동안은 주석으로만 미사용 표시, 2026-07-29 사용자 지침으로 보류돼 있었음).
2026-07-28 Agent(tool-use) 전환 이후 `/api/rag/ask`가 더 이상 호출 안 하고, 이 파일 밖에서도
아무 참조가 없음을 grep으로 재확인 후 삭제 — `import json`/`light_from_snapshot`/
`rag.answer`의 4개 함수/`rag.postgres.gap`의 3개 함수/`TRACKED_SKILLS` import도 이 삭제로
전부 불필요해져 같이 제거. `_judge_topic_postings_local()`(`method="local"`)과는 다르게 미래
용도가 없어 지움 — 그건 공고 데이터가 늘어나면 재테스트용으로 계속 유지(`STATUS.md` "향후
탐색 아이디어" 참고). 컴파일 확인 + 재빌드 후 `POST /api/rag/ask` 실제 호출로 회귀 없음 확인.

## rag-v0.19.5 — `search_chunks()` top_k=None 지원(전체 순위) (2026-07-30)

Codex 3차 리뷰(`rag-v0.19.4` 재검증)에서 발견된 Low 1건 — `judge_topic_postings(method="local")`의
직무 필터가 벡터 top-60 컷오프 뒤에 적용돼, 관련 공고가 61위 밖에 있으면 통째로 후보에서
빠질 수 있었음. 사용자가 "60이 하드코딩 아니냐"고 지적, 이 corpus는 청크가 183개뿐이라 굳이
상한을 둘 이유가 없다는 데 동의해 `search_chunks()`(`retrieval.py`)에 `top_k=None`(LIMIT
없이 전체 반환) 지원을 추가하고 `_judge_topic_postings_local()`이 이를 쓰도록 변경. 다른
호출부(`gap.py`/`hybrid.py`/`evaluate.py`)는 전부 명시적 숫자를 넘기므로 회귀 없음(컴파일+실제
`judge_topic_postings(method="local", job_role="백엔드")` 호출로 확인).

## rag-v0.19.4 — Codex 2차 리뷰 반영: 5건 수정 (2026-07-29)

`rag-v0.19.3` 수정사항을 다시 Codex에 리뷰 요청 → Medium 3건·Low 2건 추가 발견 → 전부 코드로
재검증 후 수정.

- **[Medium] `LocalEmbeddingProvider.close()` lock leak**: `wait(timeout=5)`가
  `TimeoutExpired`를 던지면 락 해제 코드에 도달 못 해 이후 모든 local 요청이 프로세스 재시작
  전까지 영구 실패할 수 있었음 — `finally`로 감싸고 타임아웃 시 `kill()` 폴백 추가.
- **[Medium] `rag-test.html`이 history를 안 잘라서 20턴 넘으면 영구 422**: 백엔드 40개 제한
  (`rag-v0.19.3`)을 추가했는데 프론트가 여전히 대화 전체를 보내서, 그 이후 모든 질문이 계속
  422가 나던 회귀. 메인 앱 QnA(`app.js`)와 동일하게 `.slice(-40)` 적용.
- **[Medium] 재색인 취소 시 워커 스레드는 안 멈춤**: `_reindex_in_progress` 플래그가 async
  coroutine의 `finally`에서 풀렸는데, 클라이언트 연결 끊김 등으로 coroutine이 cancel되면
  `to_thread`로 넘어간 실제 스레드는 계속 도는 채로 플래그만 먼저 풀려 새 요청과 겹칠 수
  있었음 — 플래그 해제를 실제 동기 작업(`_run_reindex_sync`) 안 `finally`로 옮겨 스레드 수명과
  묶음.
- **[Low] `judge_topic_postings(method="local")`가 여전히 `job_role` 무시**: `rag-v0.19.3`에서
  LLM 경로만 고치고 이 비교용 경로는 안 건드렸음 — 같은 함수 시그니처를 공유하는 이상 계약
  일관성을 위해 `_judge_topic_postings_local()`에도 `job_role` 필터 추가.
- **[Low] `normalize_skill()`이 매칭 실패 시 공백 안 지운 원본 반환**: `" RAG "`가 그대로 남고,
  공백만 있는 입력도 truthy라 빈 입력 가드를 우회할 수 있었음 — strip된 문자열을 fallback으로
  반환하도록 수정.

실측: `" Observability "`(공백 포함) → `Observability`로 정규화, `method=exact/matched=28`
확인. 일반 회귀 없음(`gap-check` 정상 200).

## rag-v0.19.3 — Codex 서브프로젝트 전반 리뷰 반영: 5건 수정 (2026-07-29)

RAG 전용 Codex 세션(`019f8e0c-...`)에 `backend/rag/` 전체 범위 리뷰 요청 → Medium 5건·Low 1건
발견 → 전부 코드로 직접 재검증(허락 없이 그대로 반영 안 함) → 5건 확정 수정(1건은 기존
`gpu_infra_plan.md`가 이미 계획한 더 큰 작업과 겹쳐 별도 논의로 보류).

- **[Medium] `list_matching_postings`에서 `job_role` 무시**: `list_postings()`가 `skill`이 있으면
  `job_title`을 아예 안 보는 `if/elif` 구조였음(query_router.py) — "백엔드 직무 중 AWS 공고"가
  전체 AWS 공고를 반환할 수 있었음. `skill`+`job_title` 동시 필터로 수정, `judge_topic_postings()`
  체인(자유 텍스트 주제)에도 `job_role` 파라미터 신설. 실측: AWS 전체 25건 → `job_role="백엔드"`
  적용 후 2건으로 정상 필터링 확인.
- **[Medium] `TRACKED_SKILLS` 대소문자 구분**: `skill in TRACKED_SKILLS`가 dict 키 조회라
  `"observability"`(소문자)는 정확 집계 대신 `DEMAND_CANDIDATE_MAX=25` 상한이 걸린 추정 경로로
  빠져 정답 28건을 구조적으로 못 채웠음. `rag/skills.py`에 `normalize_skill()` 추가, `gap.py`의
  `market_demand_hybrid()`/`assess_gap()`과 `agent.py`의 `list_matching_postings` 라우팅에 적용.
  실측: `Observability`/`observability` 둘 다 `method=exact, matched=28`로 동일하게 확인.
- **[Medium] `/api/rag/ask`의 `history` 미검증**: `list[dict]`라 role/길이 제약이 전혀 없어 긴
  대화가 결국 Anthropic context 한도로 영구 실패하거나, 잘못된 role/content가 503으로 오인
  처리될 수 있었음. 메인 앱 `QARequest`/`QAMessage` 패턴처럼 `AskMessage`(role: Literal,
  content: max_length=20000) + `history: max_length=40` 추가. 실측: 잘못된 role·41개 초과 history
  둘 다 422로 정상 거부 확인.
- **[Medium] `/api/rag/reindex` 동시 실행 방지 없음**: `chunk_embedding`에
  `UNIQUE(chunk_id, provider, model, dimensions)` 제약이 있어 재색인 두 개가 겹치면
  `UniqueViolation`으로 500이 날 수 있었음. `main.py`가 uvicorn 단일 프로세스(workers 미지정)라
  in-process 플래그로 처리 — 이미 진행 중이면 409 반환. 실측: 동시 요청 2건 → 정확히 하나는 409,
  나머지 하나만 실제 진행(google+local 성공) 확인(2회 재현).
- **[Low] Agent 요청 실패 시 `request_id` 로그 누락**: `answer_query_agent()`가 `run_agent()`에서
  예외가 나면 `_log_agent_call()`까지 못 가서 `usage_log.jsonl`엔 남는 `request_id`가
  `rag_agent_log.jsonl`엔 없어 조인이 끊겼음. `try/finally` 구조로 바꿔 성공/실패 둘 다
  `_log_agent_call()`이 항상 실행되도록 수정(실패 시 "(요청 실패 — 로그만 남김)" 표시).
- **[Medium, 부분 완화] Local provider 고정 포트(8500) 동시 접근 충돌**: 실제 확인됨(재색인
  동시성 테스트 중 우연히 재현: google 단계 성공 후 local 단계에서 SSH 터널 즉시 종료로 503).
  새 버그가 아니라 `docs/rag-project-plans/00_meta/gpu_infra_plan.md`가 이미 "상시 SSH 터널로
  교체"로 계획해둔 이슈(상태: 미구현) — 오늘 재색인 웹 트리거로 노출 빈도만 늘었을 뿐. 정석
  해결(상시 공유 터널)은 인프라 작업이라 이번엔 보류하고, `rag/embed/local.py`에 프로세스 전역
  락만 추가해 완화.
  **1차 시도(blocking 락) 실패 사례**: `threading.Lock().acquire()`를 그대로 걸었더니, 이
  provider가 `asyncio.to_thread` 없이 async 핸들러 안에서 직접 생성되는 구조라 락 대기가 uvicorn
  단일 이벤트 루프 전체를 멈춰버림 — 로그인 등 완전히 무관한 요청까지 응답 불능이 되는 걸 실제로
  재현·확인(컨테이너 재시작으로 복구). `acquire(blocking=False)`로 즉시 실패하는 방식으로
  재설계해 이 회귀는 해소. 다만 `LocalEmbeddingProvider.__init__()` 자체가 `time.sleep()` 폴링을
  쓰는 기존 동기 구조(이벤트 루프 블로킹 이슈, `async def gap_check()` 항목과 같은 원인)라 두
  요청이 사실상 순차 처리되고, 앞 터널이 막 닫힌 직후엔 OS가 포트를 바로 안 놓아줘서 곧바로 이어
  재시도하면 여전히 503이 날 수 있음(몇 초 뒤엔 정상) — "서버가 멈추거나 이상하게 죽는 것"은
  막았지만 "동시 접근이 항상 매끄럽게 성공"까지는 아직 보장 못 함.

## rag-v0.19.2 — Agent 도구 중복 호출 버그 수정 + 입력 검증 (2026-07-29)

"Agent 개발 관련 개선점" 요청으로 코드 재검토 중 발견. 처음 제기된 2건 중 1건(`get_market_demand`+
`assess_skill_gap` 중복)은 `assess_skill_gap`의 도구 설명이 이미 시장 수요를 함께 반환한다고
명시해 실제 위험이 낮다고 판단해 기각, 나머지 2건은 재현·수정.

- **`assess_skill_gap`→`generate_action_plan_for_skill` 중복 판정**: `generate_action_plan_for_skill`
  도구 설명이 "이미 assess_skill_gap으로 확인한 뒤에 쓰세요"라고 순서를 안내해서, 한 턴 안에 같은
  기술로 둘 다 부르면 `assess_gap()`(LLM 판정 포함)이 두 번 실행됐음 — `rag-v0.18.2`의
  `get_all_gaps()` 캐시와 같은 패턴으로 `get_skill_gap()` 캐시 추가. 실제 질문("Kubernetes 경험
  부족한데 행동 계획 짜줘")으로 재현 확인(Claude가 정확히 이 순서로 도구를 호출함), 수정 후
  에러 없이 정상 동작 확인.
- **`list_matching_postings` 빈 입력 가드**: `topic`이 스키마상 required지만 빈 문자열도 통과되는
  tool-use 특성상, Claude가 topic/job_role 둘 다 비워 보내면 `judge_topic_postings(topic="")`로
  떨어져 빈 주제로 불필요한 LLM 판정 호출이 될 수 있었음 — 신뢰 경계 입력 검증 추가.

## rag-v0.19.1 — `rag-test.html` 채팅 전환 버그 수정 (2026-07-29)

질문을 전송한 뒤 응답이 오기 전에 다른 채팅으로 이동했다 돌아오면 그 질문 자체가 안 보이던 버그.
전송 즉시 `localStorage`에 저장하지 않고 DOM에만 임시로 붙여뒀던 게 원인 — `switchChat()`이 저장된
내용으로 화면을 통째로 갈아치우며 사라졌음. 응답 도착 시에도 지금 보고 있는 채팅과 무관하게 항상
화면을 덮어쓰던 부수 버그도 같이 발견.

- 질문을 전송 즉시 `pending: true`로 저장 → 채팅을 옮겨 다녀도 질문+로딩 표시가 유지됨
- 응답 도착 시 그 질문이 속한 채팅을 지금 보고 있을 때만 화면 갱신(다른 채팅 보는 중이면 데이터만
  저장하고 화면은 그대로)
- 요청 실패 시 pending 항목 제거(재시도 가능하게), 새로고침으로 응답을 못 받는 pending 항목은
  페이지 로드 시 정리(`cleanupPendingMessages()`)
- Playwright로 두 시나리오(전송 후 이동·복귀, 다른 채팅 보는 중 응답 도착) 검증

## rag-v0.19.0 — 재색인 웹 트리거 (2026-07-29)

`reindex.py`가 완전히 CLI 전용이라 실제 사용자는 재색인을 트리거할 방법이 없던 문제 해결. 자동
트리거(공고 변경 API 훅)는 하지 않기로 확정 — 수동 버튼 하나로 충분하다는 원래 설계 원칙 유지.

- `POST /api/rag/reindex`(`routers/rag.py`) 신규: google+local 둘 다 프로필 포함해서 대칭 실행.
  `reindex.run()`이 동기 함수라 `asyncio.to_thread`로 감싸 이벤트 루프 블로킹 방지. stdout은
  캡처하지 않고 컨테이너 stdout으로 그대로 흘려보내 기존 `docker compose logs -f api` 디버깅 흐름
  유지.
- `ReindexRequest.include_profile_google` 필드 제거 — Google이 이미 유료 티어로 확정돼 있어
  프로필 제외 안전장치가 불필요해짐.
- `rag-test.html`에 `🔄 재색인` 버튼 연결 — 클릭 시 API 비용 발생 가능성 confirm 확인 후 호출.
- curl(로그인 토큰)·Playwright 클릭 테스트로 실제 동작 확인, `docker logs`로 google→local 순서
  실행까지 확인.

## rag-v0.18.2 — Agent 코드 리뷰 수정 + 병렬화 + 전수 검증 완료 (2026-07-29)

`rag-v0.18.1` 수정 뒤 직접 코드 리뷰(사용자 요청)로 3건 발견·전부 수정:

- [높음] 프론트 "provider: google" 표시가 임베딩 provider 선택값인데 마치 어떤 LLM이 답했는지처럼
  보이던 문제 → "임베딩 provider: google · 판정/답변: Claude 고정"으로 수정.
- [중간] `run_agent()`의 도구 실행이 try/except 없이 호출돼, 도구 하나가 실패하면 전체 요청이 죽던
  문제 → try/except로 격리, `is_error: true` tool_result로 Claude에게 전달.
- [중간] `assess_all_gaps_summary`+`generate_sequenced_plan_for_priority_gaps`가 한 턴에 같이
  호출되면 같은 13개 기술이 두 번 판정되던 문제 → 요청 단위 캐시(`get_all_gaps()`) 추가.

**병렬화**: `assess_all_gaps()`를 `asyncio.gather()`로 병렬화(13개 기술 동시 판정, 순차 대비 체감
3~4배). Anthropic API 응답 헤더로 이 계정의 실제 분당 한도(요청 10,000/토큰 1,200만)를 확인하고,
공유 DB 커넥션 동시 접근도 순차/동시 결과 비교 스크립트로 안전성을 실측 확인한 뒤 세마포어 없이 적용
(중간에 세마포어+재시도 로직을 사용자 승인 없이 임의로 얹었다가 지적받고 재시도 로직만 되돌림).

**전수 검증**: 5개 카테고리(단일 도구/복합 도구/멀티턴/답변 불가/함정형) 15문항 — 14/15 명확 통과,
1건("나 이직해도 될까?")은 Agent 결함이 아니라 테스트 질문 선정 문제(스킬/시장 데이터로 부분 답변
가능한 애매한 질문을 "완전히 답 불가" 예시로 잘못 고름). Agent의 사실 왜곡·근거 없는 확답 사례 없음.

## rag-v0.18.1 — Agent 도구 provider 일관성 수정 + 토큰 낭비 제거 (2026-07-29)

전수 검증 중 `assess_all_gaps_summary`(13개 기술 순차 판정) 반복 호출로 Gemini 무료 티어 일일 한도를
넘겨 발견. `assess_gap`/`market_demand_hybrid`/`judge_topic_postings`/`generate_action_plan`/
`generate_sequenced_plan`의 판정 LLM 호출이 "Claude 먼저" 결정과 달리 메인 앱 provider 설정(dev는
Gemini)을 그대로 따라가고 있었음 — Agent 전환 시 오케스트레이션 루프만 Claude로 고정하고 재사용한
함수들 내부까지는 확인 안 한 게 원인.

- 위 함수들에 `llm: tuple[LLMProvider, str] | None = None` 파라미터 추가. `None`이면 기존처럼 메인 앱
  설정을 따르고(`/api/rag/gap-check` 등 기존 호출부 회귀 없음), Agent 도구 실행부만
  `(AnthropicProvider(), settings.claude_high_model)`을 명시적으로 넘겨 임베딩 외 모든 판정을 Claude로
  고정.
- 별도로 함께 발견: `assess_all_gaps_summary`가 기술마다 이력서 원문 발췌(`excerpts`)를 그대로 포함해
  반환, 13개 기술 분량이 쌓이면 Claude 호출 입력 토큰이 4~9만까지 치솟음(실측 42,798/39,297/88,306
  토큰) — provider와 무관한 별개 낭비라 `_without_excerpts()`로 같이 제거.
- 실측: 수정 후 동일 질문("내 gap 중에 우선순위 높은 거 알려줘") 재실행 시 13회 판정 전부
  `claude-sonnet-4-6`(Gemini 0건), 최종 종합 호출 입력 토큰 42,798→6,951로 감소.

## rag-v0.18.0 — 아키텍처 전환: 질문 분류→고정 함수 → Agent(tool-use) (2026-07-28)

Phase 1의 "질문을 7종 중 하나로 강제 분류 → 그 유형에 고정된 함수 하나만 실행" 구조가, 실사용 중
"내가 RAG를 안하면 많이 불리할까?" 질문에 완전히 무관한 답(`action_plan` 리포트 그대로 나열)을 내놓는
게 발견돼 구조 자체를 재검토. 기존 함수 7개를 Claude tool-use 도구로 노출하고 Claude가 질문마다
스스로 판단해 조합하는 **Agentic RAG** 구조로 전환.

- `rag/postgres/agent.py`(신규): `AGENT_SYSTEM`+`TOOL_DEFS`(7개)+`_make_tool_executor()`+
  `answer_query_agent()`. 새 비즈니스 로직 없이 기존 함수(`market_demand_hybrid`/`assess_gap`/
  `assess_all_gaps`/`list_postings`/`judge_topic_postings`/`compare_postings`/`generate_action_plan`/
  `generate_sequenced_plan`)를 도구로 재배선.
- `llm/base.py`/`llm/anthropic.py`: `run_agent()` 추가(Anthropic만 실제 구현, 최대 6회 도구 호출 루프,
  `tool_choice` 미지정으로 도구 선택을 Claude 자신에게 위임).
- `routers/rag.py`: `/api/rag/ask`가 `answer_query_agent()` 호출, `AskRequest`가 `method`/
  `session_state` 대신 `history: list[dict]`로 교체.
- `rag-test.html`: 새 응답 형태(`{answer, tool_calls}`)에 맞춰 렌더링 전면 수정, 도구 호출 트레이스를
  `<details>`로 노출, `history` 기반 멀티턴 전송.
- 실측: 실패 사례 재현 후 `get_market_demand`+`assess_skill_gap` 자동 호출로 "결론: 크게 불리하지
  않습니다"로 시작하는 종합 답변 확인(이전엔 무관한 리포트만 나왔음).

## rag-v0.17.0 — Phase 4: 멀티턴 대화 + 채팅 세션 UI (2026-07-28)

메인 앱 Q&A(`routers/qa.py`)는 raw 대화 히스토리 배열(최대 40개)을 매번 통째로 재해석하는 방식인데,
RAG는 몇 개 필드(skill/job_role/query_type)만 이어받으면 되는 구조라 그대로 베끼지 않고 **작고 고정된
세션 상태 객체**로 설계(배열처럼 안 자람, 매 턴 덮어써짐).

- `query_router.py`: `classify_query()`가 `session_state`를 받아 "그중에서/왜?" 같은 참조 표현이면
  이전 skill/job_role을 이어받고, 새 주제가 명확하면 교체하도록 `CLASSIFY_SYSTEM`에 규칙 추가.
  `answer_query()`가 매 턴 실제로 쓰인 조건을 `session_state`로 응답에 포함
- `routers/rag.py`: `AskRequest`에 `session_state: dict | None` 필드 추가
- `rag-test.html`: 채팅 세션 여러 개를 `localStorage`에 저장·전환하는 UI(드롭다운 + "새 채팅" 버튼),
  메인 앱 Q&A 탭과 같은 채팅 버블 레이아웃(`qa-messages`/`qa-input-row` 재사용 — 메시지 영역 내부
  스크롤 + 입력창 하단 고정)으로 재구성
- 실측: Redis 세션 상태를 준 채로 "그중에서 몇 개야?"(스킬 언급 없음) → market_aggregate로 정확히
  이어받아 분류됨. 완전히 다른 주제(AWS) 질문 시 이전 조건 정확히 교체(carry-over 오작동 없음) 확인
- 구현 중 CSS 버그 2건 발견·수정: 전역 `select { width: 100% }`가 입력행 select를 전체 폭으로
  늘려버린 문제(scoped `width: auto`로 해결), `scrollIntoView()`가 내부 스크롤이 아니라 페이지
  스크롤만 움직여 최신 메시지가 입력창에 가려지던 문제(`scrollTop = scrollHeight`로 해결)

## rag-v0.16.1 — 프로필 재색인 시 provider 드리프트 자동 방지 (2026-07-28)

이 dev DB에서 로컬(Jina) provider의 후보자 프로필 임베딩이 통째로 비어있는 걸 발견 — 2026-07-23에는
google·local 둘 다 11개씩 있었는데(Plan B 2단계 검증 기록), 그 사이 누군가 `reindex --provider google
--include-profile`을 실행했을 때 프로필 청크 해시 변경이 감지돼 **모든 provider의 기존 임베딩이 함께
삭제되고 google만 다시 채워진** 것으로 추정(`populate_candidate_profile_chunks()`가 provider와 무관하게
`document_chunk`를 공유하기 때문). 이 상황 자체는 이미 stdout 경고로 안내되고 있었지만
(`reindex.py`의 `_warn_other_providers_stale()`), CLI 출력 한 줄이라 놓치기 쉬웠고 실제로 놓쳐서
방치됐음.

- `reindex.py`: 프로필 청크 변경이 감지되면(`profile_changed`) 지정한 provider 하나만이 아니라
  **등록된 모든 provider로 자동 재임베딩**하도록 수정. 특정 provider 인프라가 그 순간 불가능해도
  (예: 3050Ti SSH 터널 다운) 그 provider만 실패로 격리하고 나머지는 계속 진행 — 재시도 방법을 안내하는
  경고만 남기고 전체 재색인은 안 막음
- 즉시 조치: `reindex --provider local --include-profile` 실행해 로컬 프로필 임베딩 11개 복구,
  `CANDIDATE_EVIDENCE` 10/10 재확인(로컬 provider 원래 기준과 정확히 일치)

## rag-v0.16.0 — Phase 3: 답변에 판정 근거·표본 범위 노출 (2026-07-28)

- `JUDGE_CANDIDATES_TOOL_SCHEMA`(`rag/gap.py`)를 `relevant_numbers`(정수 배열)에서 `relevant`
  (번호+근거 객체 배열)로 확장 — LLM이 각 공고를 관련 있다고 판단한 근거(짧은 인용/요약)를 같이 반환.
  이 스키마를 공유하는 두 호출부(`market_demand_hybrid()`, `judge_topic_postings()`) 모두 반영
- `posting_list`(method="llm") 응답에 `evidence` 필드 추가, 프론트(`rag-test.html`)에 "판정 근거"
  컬럼으로 표시
- `market_aggregate` 응답에 정확매칭/LLM 추정 판정 구분 문구 추가(추정일 때 후보 개수·전체 미검토
  안내 포함) — `single_skill_gap`(`renderGapCard`)에 이미 있던 패턴을 재사용
- `all_gaps`/`action_plan`: 백엔드는 이미 `excerpts`/`reasoning`을 갖고 있었는데 프론트 렌더링에서
  스킬명·근거수준만 보여주고 근거 텍스트를 안 보여주고 있었던 것 발견·수정(`renderSkillEvidenceCard()`)
- 회귀 확인: `CANDIDATE_EVIDENCE` 10개 기준 Google provider로 9/10 일치(1건은 `TRACKED_SKILLS`
  정확매칭 경로의 프로필 판정 차이로, 오늘 변경한 `JUDGE_CANDIDATES_*` 스키마와 무관). 로컬(Jina)
  provider는 이 dev DB에 프로필 임베딩이 없어 비교 불가(기존 상태, 오늘 변경과 무관)

## rag-v0.15.0 — Phase 2: 서술형 주제 검색을 전체원문 LLM 판정으로 교체 (2026-07-28)

`posting_list`의 자유 텍스트 주제(`TRACKED_SKILLS` 밖) 검색이 임베딩+FTS 후보 필터링(`_candidate_postings`)
단계에서 정답을 통째로 놓치는 사례가 실측으로 확인됨(예: "TVING"이 25건 후보 목록에 아예 없었음).
화이트닝(naive+완전 SVD covariance whitening)·cross-encoder reranker(bge-reranker-v2-m3)도 함께
실측했으나 둘 다 이 corpus에서는 순위 품질을 개선하지 못해 기각. 최종적으로 corpus 규모가 작다는 점에
착안해 후보를 점수로 미리 거르지 않고 전체 원문을 LLM 판정에 그대로 넘기는 방식을 채택.

- **신규** `query_router.py`의 `judge_topic_postings()`: `method="llm"`(기본값, 공고 전체 원문을
  `JUDGE_CANDIDATES_SYSTEM`으로 판정) / `method="local"`(벡터 검색 top-15 그대로 반환, LLM 호출 없는
  비교·실험용)
- `JUDGE_CANDIDATES_SYSTEM`(`rag/gap.py`)에 "주요업무 우선, 우대사항 키워드 단독 언급만으로 인정
  금지" 규칙 추가 — 전체원문 판정 시 우대사항의 부수적 키워드 언급만으로 오탐(예: 이커머스 우대사항의
  "결제")이 발생하던 문제 해결
- `answer_query()`의 `posting_list` 분기가 `TRACKED_SKILLS` 여부로 기존 정확매칭/`judge_topic_postings()`로
  갈리도록 수정, `AskRequest`에 `method` 필드 추가
- 실측(자연어 평가셋 5문항, 정답지는 검토 깊이 통일 후 대폭 보정): 기존 25건 후보 방식 대비 Q5
  recall 0.43→1.00, Q6 recall 0.41→0.95. 비용은 건당 약 $0.132(gemini-3.5-flash 기준, 기존 대비
  늘지만 개인용 앱 기준 무시 가능)

## rag-v0.14.0 — 대화형 근거 기반 RAG Phase 1: 질문 이해와 라우팅 (2026-07-27)

자연어 질문(`POST /api/rag/ask`)이 7종류로 분류·라우팅되도록 신규 구현. 처음엔 "지금 있는 함수 종류"에
맞춰 6개로 분류하려 했으나, "유연한 챗봇에 필요한 능력"을 먼저 정하는 방향으로 재검토 — 없던 능력(공고
목록 조회, 공고 비교)도 이번에 새로 만듦.

- **신규** `backend/rag/postgres/query_router.py`: `classify_query()`(Lightweight 티어, 7종 분류 —
  단일 기술 Gap/전체 Gap/시장 수요 통계/행동 계획/공고 목록 조회/공고 비교/답변 불가), `answer_query()`
  (라우팅), `list_postings()`/`compare_postings()`(순수 SQL, LLM 호출 없음)
- **공고 비교용 구조화 필드 추가**: `posting` 테이블에 `tech_stack`/`benefits`/`stability`/
  `employee_count`/`investment_stage`/`jobplanet_score`/`fit_score`/`strengths`/`gaps` 9개 컬럼 —
  `ingest_postings()`가 이미 메모리에 읽어둔 frontmatter에서 그대로 뽑아옴(새 파싱 없음)
- **신규 API** `POST /api/rag/ask`: 기존 `/gap-check`의 conn/embed_provider 생성·정리·예외 처리 패턴을
  그대로 재사용(Codex 리뷰로 다듬어진 부분이라 새로 설계하지 않음)
- `frontend/rag-test.html`에 자연어 질문 입력 섹션 추가, `query_type`별 렌더링 분기
- 검증: 분류 정확도 7/7(실패했던 두 질문 포함, "몇 개"↔"어디어디" 핵심 구분 정확), 7종 전부 curl
  end-to-end 확인(`posting_comparison`은 실제 corpus 회사 2곳으로), Playwright 브라우저 렌더링 확인,
  기존 `/gap-check` 회귀 없음
- **발견(범위 밖)**: `all_gaps`/`action_plan`은 13개 기술 순차 LLM 호출 구조라 45~54초 소요 실측
  확인 — 기존에 알려진 지연시간 이슈와 동일 원인, 이번 범위 밖

## rag-v0.13.5 — Codex 5차 재리뷰 2건 추가 수정 (2026-07-24)

rag-v0.13.4의 2건 수정을 재검토 요청 — 둘 다 요청 범위에서 해결 확인, conn 중복 close도 없음. 신규 Medium 1건·Low 1건 발견.

- **[Medium] RAG 라우터가 `LLMAPIError`를 안 잡아서 LLM 장애가 500으로 반환됨**: `assess_gap()`/`generate_action_plan()`이 쓰는 LLM provider(Claude/OpenAI/Gemini 텍스트 생성)의 인증 실패·rate limit·서버 오류는 `LLMAPIError`로 래핑되는데(`routers/companies.py` 등 기존 라우터가 이미 쓰는 패턴), `routers/rag.py`엔 이 except가 없었음 — `except LLMAPIError as e: raise HTTPException(e.status_code, ...)` 추가(자체 status_code 그대로 반영)
- **[Low] `ingest_run()` 내부(`ingest_postings`/`ingest_skill_alias`/`ingest_candidate_evidence`) 실패 시 conn이 호출부에 반환 안 돼 정리 안 됨**: `reindex.py`의 `_run_with_conn()` 분리와는 별개의 더 안쪽 경계 — `ingest.run()`이 만든 연결이니 실패 시 자체적으로 닫고 재발생하도록 수정
- 이제까지 총 5차 재검토, 누적 수정: High 1건(경고로 관리하는 정책으로 수용)·Medium 8건·Low 5건. async 이벤트루프 이슈만 의도적으로 미해결 상태 유지(문서화, 위 rag-v0.13.4 참고)

## rag-v0.13.4 — Codex 4차 재리뷰 2건 추가 수정 + async 이벤트루프 이슈 문서화 (2026-07-24)

- **[Medium] Google API 5xx 오류가 여전히 500으로 샘**: `except genai_errors.ClientError`가 4xx만 잡았음 — `ClientError`/`ServerError`는 둘 다 `APIError`의 하위 클래스인데(코드로 직접 확인) 상위 클래스로 안 잡아서 5xx는 누락. `APIError`로 확장
- **[Low] `reindex.py`의 `prune_deleted_postings()`/`populate_posting_chunks()`/`schema_conn`이 여전히 try 밖에 있어서 그 구간 실패 시 conn 미종료** — 전체를 `_run_with_conn()`으로 분리해 바깥 try/finally로 감싸고, `schema_conn`도 개별 try/finally로 정리
- **[신규 발견, Medium, 의도적으로 보류] `async def gap_check()`가 동기 DB/Google SDK/httpx 호출을 그대로 실행해 이벤트 루프를 막을 수 있음** — FastAPI는 `async def` 안에서 부른 일반 함수를 스레드풀로 자동 이동시켜주지 않음(공식 문서 확인). 특히 Local provider는 최대 600초 타임아웃이라 그 동안 같은 워커가 막힐 수 있음. **사용자 결정: 지금은 개인용 단일 사용자 툴이라 영향이 작아 문서화만 하고 넘어감** — 실제 다중 사용자 서비스로 확장할 때 재검토(`docs/rag-project-plans/00_meta/STATUS.md` "향후 탐색 아이디어" 참고)

## rag-v0.13.3 — Codex 3차 재리뷰 4건 추가 수정 (2026-07-24)

rag-v0.13.2의 4건 수정을 다시 재검토 요청 — 4건 모두 의도한 경로에 반영됨을 확인, 신규 Medium 2건·Low 2건 발견.

- **[Medium] Google 임베딩 API 오류가 500으로 샘**: `google.genai.errors.ClientError`(429 재시도 소진, API 키 오류 등)가 `RuntimeError`/`httpx.HTTPError`/`psycopg.Error` 어디에도 안 걸려 그대로 500으로 샜음(LocalEmbeddingProvider와 비대칭) — `except genai_errors.ClientError` 추가, 503 매핑
- **[Medium] `except psycopg.Error`가 너무 넓어서 실제 버그도 503으로 가려질 수 있었음**: `psycopg.Error`는 연결 실패뿐 아니라 SQL 문법·제약조건 위반 같은 프로그램 버그도 포함 — `psycopg.OperationalError`(연결 계열만)로 좁힘. 응답 메시지도 내부 정보(호스트·스키마) 노출 없이 일반화
- **[Low] 경고 문구가 최초 색인에서도 "사라졌습니다"로 부정확하게 표시됨**: 신규/변경 양쪽에 다 맞는 "생성되지 않았습니다"로 수정
- **[Low] `hnsw_eval.py`/`reindex.py`의 CLI 자원 정리가 일부 경로에서 불완전**: provider 생성 실패 시 `conn`이 안 닫히거나, `hnsw_eval.py`는 정상 종료 경로에도 `conn.close()`가 없었음(smoke 검증도 try 밖) — 둘 다 프로세스 종료로 대부분 회수되는 CLI라 Low였지만 `routers/rag.py`와 일관되게 수정
- 요청한 4건(provider 경고, DB 연결 503, HNSW N+1 제외, 자원 독립 정리) 전부 해결 확인됨

## rag-v0.13.2 — Codex 재리뷰 3건 추가 수정 (2026-07-24)

rag-v0.13.1에서 반영한 6건 수정을 다시 Codex에 재검토 요청 — 5건은 해결 확인, High 1건은 "경고만 추가됐고 실제 문제는 남아있다"는 지적, 추가로 Medium 2건·Low 1건 신규 발견. 전부 재현·확인 후 수정.

- **[High, 부분 재발견] 다른 provider 경고가 삭제 이후 조회라 누락될 수 있었음**: `reindex.py`의 경고 로직이 `populate_posting_chunks()`가 이미 옛 임베딩을 삭제·커밋한 뒤 `SELECT DISTINCT provider FROM chunk_embedding`으로 "남아있는" provider를 조회했는데, 변경된 posting이 어떤 provider의 유일한 출처였다면 그 provider는 이미 삭제돼 조회에 안 잡히고 경고도 안 나옴. DB 상태 조회 대신 `PROVIDERS` 레지스트리 자체와 비교하도록 수정(항상 정확). 프로필 재임베딩 시에도 같은 경고 추가(기존엔 없었음)
- **[Medium] DB 연결 실패가 503으로 안 잡힘**: `get_connection()` 실패는 `psycopg.OperationalError`(→`psycopg.Error`)인데 `RuntimeError`/`httpx.HTTPError`만 잡고 있어서 500으로 샜음 — `except psycopg.Error` 추가
- **[Medium] HNSW 지연시간 측정에 N+1 posting 조회가 섞여 있었음**: 원격 임베딩은 이미 타이머 밖으로 뺐지만, `ranked_postings_by_score()`의 chunk_id별 개별 SQL 조회가 여전히 타이머 안에 있었음 — posting 매핑을 반복 루프 밖으로 이동, 벡터 검색 SQL 자체만 측정(15~20ms → 10~13ms로 더 정확해짐, 결론은 동일: 여전히 HNSW 미사용)
- **[Low] `embed_provider.close()` 실패 시 `conn.close()`가 건너뛰어질 수 있었음**: `LocalEmbeddingProvider.close()`의 SSH 터널 종료 대기가 타임아웃 예외를 던지면 그다음 줄이 실행 안 됨 — 각 자원을 독립적으로 정리하도록 수정(하나가 실패해도 나머지는 정리됨)
- 새 SQL injection·CHECK/HNSW 컬럼 오류는 발견되지 않음. `rebuild_schema()` 자체의 원자성은 안전하나 이후 적재·청킹·임베딩은 여전히 별도 커밋(원래 알던 트레이드오프, 이번에도 그대로 인지하고 넘어감)

## rag-v0.13.1 — Codex 코드 리뷰 6건 수정 + Stage 4 HNSW 결론 정정 (2026-07-23)

Plan B Stage 2~6(2496d96..HEAD) 전체를 Codex에 리뷰 요청, 실제 재현으로 6건 전부 확인 후 수정.

- **[정정] rag-v0.11.0의 "HNSW recall 동일/지연시간 소폭 개선" 결론은 근거가 없었음.** `EXPLAIN`으로 확인해보니 그때 "hnsw 모드"라고 표시한 질의도 실제로는 HNSW 인덱스를 전혀 안 쓰고 `Bitmap Heap Scan`(UNIQUE 인덱스)을 썼다 — `enable_seqscan=off`는 힌트일 뿐 강제가 아니고, 이 corpus 규모에서 provider/model/source_type 필터를 거친 후보군(수십 건)에는 플래너가 HNSW보다 일반 인덱스+정렬을 항상 더 싸다고 판단함. `hnsw_eval.py`를 다시 작성해 (1) 매 반복마다 원격 임베딩을 다시 호출하던 것을 질문당 1회로 수정(기존 500ms/190ms는 대부분 임베딩 API 시간이었음, 실제 DB 지연은 15~20ms), (2) `EXPLAIN`으로 실제 스캔 방식을 매번 확인해 출력, (3) 필터 없는 최소 조건 질의로 HNSW 인덱스 자체는 정상 동작함을 별도 확인. **정정된 결론**: 이 corpus 규모+질의 형태에서는 HNSW가 실제로 한 번도 안 쓰였고, exact/hnsw 두 "모드"가 사실상 같은 계획이라 recall이 같은 게 당연했음 — 데이터가 늘어난 뒤 재측정 필요성이 여전히 유효함
- **[High] 단일 provider 재색인이 다른 provider 임베딩을 삭제** — 공고 내용이 바뀌면 그 청크의 모든 provider 임베딩이 삭제되는데(청크가 새 id로 재생성돼서), CLI에서 선택한 provider 하나만 다시 채워 다른 provider는 그 공고에 대해 조용히 비게 됨(실측 재현 확인). 완전 자동 수정 대신, 다른 provider들이 존재하면 재색인 후 명시적 경고 메시지 출력하도록 수정(`reindex.py`)
- **[Medium] `CREATE TABLE IF NOT EXISTS`는 스키마 변경(Stage 2→4→6)을 자동 반영 안 함** — `schema.py`에 `rebuild_schema()`/`--rebuild-schema` CLI 플래그 추가, 기존엔 매번 수동으로 테이블 드롭
- **[Medium] `routers/rag.py`가 Postgres 커넥션을 안 닫음** — `conn.close()`를 `finally`에 명시적으로 추가(GC의 `__del__`에 의존하던 상태, idle in transaction 커넥션 누적 위험)
- **[Medium] `chunk_embedding.vector_1536`/`vector_1024` 조합을 DB가 강제 안 함** — 잘못된 조합(둘 다 NULL, 둘 다 채워짐, 차원 불일치) 삽입을 막는 CHECK 제약 추가, 실제로 위반 삽입이 거부됨을 확인
- SQL injection: 발견 없음. `prune_deleted_postings()`의 `posting_raw`만 지우고 `candidate_profile` 보존은 의도된 동작. SQLite 순수 함수 재사용은 안전함 — 리뷰에서 문제 없음으로 확인된 3건은 수정 없이 종료

## rag-v0.13.0 — Plan B 5단계: 증분 색인과 운영 (2026-07-23)

- **삭제 동기화 버그 발견·수정**: 공고 원문(`.raw.txt`)이 삭제되면 `posting`/`posting_skill`은 재적재 시 정리됐지만, `document_chunk`/`chunk_embedding`은 그 posting을 더 이상 순회하지 않아 고아 행으로 영원히 남는 문제 발견. `rag/postgres/chunks.py`에 `prune_deleted_postings()` 추가, `reindex.py`에서 `ingest_run()` 직후 호출
- 증분 재임베딩(해시 비교)·색인 실패 재처리(단일 트랜잭션 커밋이라 부분 손상 없음)·모델 버전 전환(UNIQUE 제약이 model까지 포함해 신구 모델 공존 가능)은 기존 코드로 이미 충족 — 실제 시나리오(부분 삭제 후 재실행, 합성 구버전 모델 삽입)로 검증만 하고 코드 변경 없음
- 삭제 동기화 검증: 공고 파일 1개를 임시로 옮겨 `reindex.py` 실행 → 고아 행 0건 확인 → 파일 복구 → 재실행으로 원상 복구(posting 70건) 확인
- `pg_dump`/`pg_restore`로 백업·복구 실측 — DB 드롭 후 복구해도 건수(posting 70/posting_skill 212/chunk 194/embedding 388) 정확히 일치, pgvector 익스텐션도 정상 복구
- SQLite(`rag/gap.py` 등)의 동일한 삭제 동기화 갭은 안 고침 — Plan A 기준선 재현용 동결 스크립트라 범위 제외

## rag-v0.12.0 — Plan B 6단계: Career Gap 답변 + UI를 Postgres로 전환 (2026-07-23)

- `document_chunk.text_tsv`(tsvector generated column + GIN 인덱스) 추가 — Postgres 전문검색, SQLite FTS5와 달리 INSERT/UPDATE마다 DB가 자동 갱신해 수동 rebuild 로직이 필요 없음
- `rag/postgres/fts.py`(`search_fts`) — `websearch_to_tsquery`로 자유 텍스트 검색(SQLite `fts5_literal()` 같은 수동 이스케이프 불필요)
- `rag/postgres/gap.py`/`answer.py` — `rag/gap.py`/`answer.py` 포팅. DB 안 건드리는 순수 함수(`_recognized_scope`, `generate_action_plan`, `format_report`, `rank_priority_gaps`, `summarize_strengths`, `generate_sequenced_plan`)는 새로 안 만들고 원본에서 그대로 import
- `routers/rag.py`가 SQLite 대신 Postgres 파이프라인을 호출하도록 전환 — 실제 서비스 흐름(`POST /api/rag/gap-check`)이 Postgres로 완전히 전환됨
- 검증: `CANDIDATE_EVIDENCE` 10/10 일치, GP-01/GP-06/AC-06 집계 리포트가 `01b` 기대값과 일치, 실제 API 호출(exact/estimated, google/local 전 조합)로 end-to-end 확인
- SQLite 코드(`rag/gap.py` 등)는 삭제하지 않고 Plan A 기준선 재현용 "동결 스크립트"로 유지(서비스 경로에서만 제외)

## rag-v0.11.0 — Plan B 4단계: HNSW 인덱싱 + RRF 하이브리드 검색 (2026-07-23)

- `chunk_embedding.vector`(고정 차원 없음) → `vector_1536`(Google)/`vector_1024`(Local) 두 컬럼으로 분리, 각각 HNSW partial index 추가(provider별 컬럼 분리, provider가 2개뿐이라 테이블 분리보다 diff가 작음)
- `rag/postgres/hnsw_eval.py` — `enable_seqscan`/`enable_indexscan` GUC로 exact/HNSW 모드를 강제 전환해 비교. 이 corpus(청크 194개)는 너무 작아 P@5/R@10은 완전히 동일(01g에서 예측한 그대로), 지연시간은 HNSW가 소폭 빠름(Google 524→515ms, Local 192→183ms — 이 규모에선 노이즈 수준일 수 있음)
- `rag/postgres/hybrid.py`(`rrf_search`) — `posting_skill` 정확 매칭 + pgvector 검색을 RRF(k=60)로 결합. `evaluate_hybrid.py`로 벡터 단독과 비교했으나, **12개 평가 질문 전부 정확 매칭 채널 = ground truth라 결과가 항상 유리하게 나옴을 명시** — 이 평가는 RRF 결합 로직 자체가 올바른지 확인하는 배관 점검이지 실전 하이브리드 효과 검증이 아님(recall이 정확 매칭 건수로 캡핑되는 패턴으로 확인)
- 범위 제외(명시): `skill_alias`를 임베딩 클러스터링으로 채우는 재설계 — 별도 논의로 남김

## rag-v0.10.0 — Plan B 3단계: pgvector exact search + 검색 품질 평가 (2026-07-23)

- `rag/postgres/retrieval.py` — pgvector `<=>` 연산자로 SQL 안에서 정렬·top_k까지 처리(SQLite판의 BLOB pack/unpack+파이썬 코사인 계산 제거)
- `rag/postgres/verify.py` — 2단계 집계(공고 수·기술별 건수·교집합) 검증, 기대값은 `rag.verify_step2`에서 재사용, 17개 항목 전부 일치
- `rag/postgres/evaluate.py` — `rag.evaluate`의 질문 세트·지표 함수(순수 파이썬)를 그대로 재사용해 Google/Local exact search만 재실행. **결과가 Plan A(SQLite) 기준선과 소수점 둘째 자리까지 정확히 일치**(Google 0.68/0.33, Local 0.65/0.42) — 포팅 정확성 확인
- FTS5/하이브리드 비교는 이번 범위에서 제외(Stage 4 몫)

## rag-v0.9.0 — Plan B 1~2단계: 승계 확인 + PostgreSQL+pgvector 저장소 구축 (2026-07-23)

- Plan B 착수 — `docs/rag-project-plans/02_structured_career_intelligence_rag.md`(설계)와 `01g_plan_a_summary.md`(확정 결정) 기준으로 SQLite→PostgreSQL+pgvector 이전을 6단계로 나눠 진행, 이번엔 1~2단계만
- 1단계(승계 확인): 신규 작업 없음 — `01d` 평가 수치, 청킹 규칙, 임베딩 모델(Google 1536차원/Local 1024차원) 그대로 기준선으로 승계
- 2단계(저장소 구축): `backend/rag/postgres/` 서브패키지 신설(`schema.py`/`db.py`/`ingest.py`/`chunks.py`/`pipeline.py`/`reindex.py`) — 기존 SQLite 코드(`rag/ingest.py` 등)는 그대로 두고 Postgres를 별도 저장소로 나란히 구축(gap.py/answer.py/evaluate.py는 Stage 6까지 SQLite 유지)
- `docker-compose.dev.yml`(미추적)에 `pgvector/pgvector:pg17` 서비스 추가
- `python3 -m rag.postgres.reindex --provider google/local --include-profile`로 전체 재색인 검증 — posting 70건/posting_skill 212건/candidate_evidence 10건/chunk 194건(공고183+프로필11), google(1536차원)·local(1024차원) 임베딩 각 194건으로 SQLite 기존값과 전부 일치 확인
- FTS5/하이브리드 검색·HNSW 인덱싱은 이번 범위에서 제외(Stage 4 몫) — `chunk_embedding.vector`는 provider별 차원이 섞여 있어 고정 차원 없는 `vector` 타입으로 둠(인덱싱 전략은 Stage 4에서 결정)

## rag-v0.8.0 — Plan A 종료 + 테스트 UI + 시장 수요 하이브리드 확장 (2026-07-23)

- `01g_plan_a_summary.md`로 Plan A(1~8단계) 전체 결정·평가수치·한계·코드 인벤토리 통합
- RAG 테스트 UI(`routers/rag.py` + `frontend/rag-test.html`) — provider(google/local)를 요청마다 선택 가능한 gap-check 화면. Docker에 `openssh-client` 설치 추가(컨테이너 안에서 SSH 터널이 필요해서)
- 시장 수요 계산 하이브리드화 — 13개 고정 기술(`TRACKED_SKILLS`)은 기존 `posting_skill` 정확 매칭 유지, 그 외 자유 키워드는 임베딩+FTS5로 후보 공고를 모아 LLM이 개별 판정. 순수 임베딩 유사도(절대 임계값/순위 기반 개수/centering/elbow 탐지) 4가지를 다 실측했으나 이 corpus(비슷한 직군 공고들)에서는 유사도 크기가 "관련 있음/없음"을 구분 못 한다는 게 확인돼(Redis 3건과 Python 40건이 거의 같은 분포) 이 방식으로 결정. UI/리포트에 "추정치"로 명시 구분

## rag-v0.7.0 — 7단계: 답변 생성 (2026-07-23)

- `answer.py`: `generate_action_plan()`(gap별 구체적 활동+남길 증거+완료조건 — 막연한 토이 프로젝트 금지 원칙 반영), `generate_sequenced_plan()`(여러 gap을 하나의 순서·중단조건 계획으로, `01b` AC-06 스타일)
- GP-01(우선순위 gap 랭킹)/GP-06(전체 강점 요약)/AC-06(순서 계획) 집계 리포트가 `01b` 기대값과 정확히 일치 확인

## rag-v0.6.0 — 6단계: Gap 및 행동 엔진 (2026-07-23)

- `gap.py`: 시장 수요는 SQL, 근거는 임베딩 검색, 판정은 LLM — 3단 분리 아키텍처(`01_lean_evidence_first_rag.md` 원칙)
- `CANDIDATE_EVIDENCE`(10개 기술 정답지) 기준 판정 프롬프트를 3차례 개선 끝에 10/10 달성
  - 1차: "이름이 문자 그대로 등장해야 함" 규칙 → IaC는 고쳤지만 Observability가 새로 깨짐(추상 개념이라 이름 자체가 안 나옴)
  - 2차: "기능적 동일성" 규칙으로 일반화 → Observability 복구, IaC가 다시 흔들림
  - 3차(최종): 판정 LLM에게 `TRACKED_SKILLS`의 동의어 범위를 참고 정보로 전달(`_recognized_scope()`) — 특정 기술명을 프롬프트에 하드코딩하지 않고 이미 있는 데이터로 경계를 명확히 전달해 해결
- 집계 로직 추가: `assess_all_gaps()`(전체 기술 순회), `rank_priority_gaps()`/`summarize_strengths()`(순수 코드 필터링, LLM 미관여) — 기술 하나짜리 질문만 다루던 기존 구조로는 GP-01/GP-06처럼 여러 기술을 종합하는 질문에 애초에 답할 수 없었던 걸 해결

## rag-v0.5.0 — 5단계: 검색 정식 평가 (2026-07-23)

- `evaluate.py`: `01b`의 EX(정확 기술명)+SY(동의어) 12개 질문으로 FTS5/Google/Local Precision@5·Recall@10 비교
- 결과: FTS5 0.75/0.41(정확 기술명 최강, 동의어는 원문과 다른 표기면 0까지 하락), Google 0.68/0.33, Local(Jina) 0.65/0.42(Recall은 FTS5·Google도 능가)
- FTS5 구축 중 버그 발견·수정: external content 방식(`content='document_chunk'`)이 `MATCH`를 항상 빈 결과로 반환 — 독립형 가상 테이블로 교체

## rag-v0.4.0 — 4단계: 로컬 임베딩 (2026-07-23)

- 3050Ti(RTX 3050 Ti Mobile, 4GB VRAM) + WSL2에 CUDA 12.4, FastAPI 추론 서버 구축. dev 서버와 SSH 터널로 연결(WSL2 systemd 자동 시작 + Windows portproxy 자동 갱신으로 재부팅 후에도 수동 개입 없이 유지되도록 정비)
- 로컬 임베딩 모델 5개 실측 비교:
  - `Alibaba-NLP/gte-multilingual-base` — 커스텀 토크나이저가 vocab 범위 벗어난 토큰 ID를 만들어 CUDA `device-side assert`로 탈락
  - `intfloat/multilingual-e5-base` — 정상 동작, 한때 채택
  - `BAAI/bge-m3` — 정상 동작하지만 가장 낮은 점수로 탈락
  - `jinaai/jina-embeddings-v5-text-small` — **최종 채택**(P@5 0.65/R@10 0.42로 e5-base·BGE-M3·FTS5·Google Recall 전부 능가). Qwen3 기반 decoder+LoRA라 4GB VRAM에서 배치 크기 4 필요
  - `jhgan/ko-sroberta-multitask`(한국어 전용) — "한국어 corpus엔 한국어 전용 모델이 유리하지 않겠냐"는 가설로 시도했으나 오히려 가장 낮은 점수(STS 튜닝 모델이라 비대칭 검색엔 안 맞음)
- `LocalEmbeddingProvider`는 3050Ti 서버의 `/health` 응답(모델명·차원)을 매번 대조 검증 — 모델 교체 시 조용히 잘못된 벡터가 섞이는 사고 방지(Codex 리뷰 권고 반영)

## rag-v0.3.1 — Google 임베딩 task_type 버그 수정 (2026-07-22)

- Codex 리뷰로 발견: `gemini-embedding-2`는 `task_type` config를 지원하지 않는데(API가 조용히 무시), 기존 코드가 이 값을 쓰고 있어서 문서/질의 구분 없이 임베딩되고 있었음
- 공식 포맷(문서 `"title: ... | text: ..."`, 질의 `"task: search result | query: ..."` prefix)으로 재작성, 기존 183개 임베딩 삭제 후 재생성, 검색 스모크 테스트로 품질 저하 없음 확인

## rag-v0.3.0 — 3단계: Google 임베딩 기준선 (2026-07-22)

- `gemini-embedding-2`(1536차원)로 공고 청크 183개 임베딩. 청킹 규칙: 빈 줄 기준 문단 분리, 청크당 최대 1,200자, overlap 없음
- `EmbeddingProvider` 추상 인터페이스로 리팩터(`llm/base.py` 패턴 차용하되, RAG는 "활성 provider 하나 선택"이 아니라 "여러 provider 비교"가 목적이라 라우터는 두지 않음)
- 증분 재임베딩(문서 해시 비교로 안 바뀐 공고는 스킵) 구현 — 재실행할 때마다 전체 재임베딩되던 낭비 수정
- 실행 진입점을 `embed_google.py`에서 `run_embedding.py --provider google`로 통합(provider 이름이 진입점 파일명에 박혀있던 것 정리)

## rag-v0.2.0 — 2단계: 공고 데이터 정규화 (2026-07-22)

- SQLite 스키마(`posting`/`posting_skill`/`skill_alias`/`candidate_evidence`) 신설
- `skills.py`: `TRACKED_SKILLS`(정확 기술명 6개+동의어 그룹 6개+IaC — Plan A 검증용 정답지, 프로덕션 메커니즘 아님), `CANDIDATE_EVIDENCE`(프로필 기반 근거 판정 10개 정답지)
- `verify_step2.py`로 17개 항목(전체 공고 수 1+기술 집계 13+교집합 3)을 원문 grep과 전부 대조 검증

## rag-v0.1.0 — Plan A 로드맵 수립 (2026-07-21)

- Plan A(검색 기준선, SQLite) → Plan B(PostgreSQL+pgvector 주 구현) → Plan C(Qdrant·reranker·GraphRAG 비교) 3단계 로드맵 확정
- 서브프로젝트 브랜치 전략 확정: `rag/main`을 절대 main에 merge하지 않는 영구 브랜치로 운영(주기적으로 main → rag/main 방향으로만 병합), 결과 불확실한 곁가지 실험은 `rag/<실험명>` 형제 브랜치로 분리
- 평가 세트 초안 작성 — corpus 스냅샷(공고 70건) SHA-256 해시로 고정, 정확 기술명·동의어·집계·개인 gap·행동 계획·답변 불가 6개 유형 36개 질문
- (계획 문서 자체는 `docs/rag-project-plans/`에 있으며 git 미추적 — 이 단계는 코드 커밋 없음)
