# Changelog

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
