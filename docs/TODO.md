# TODO — Job FitCheck 개발 계획

> 상태: ✅ 완료 · 🔧 진행중 · ⬜ 예정 · 💡 아이디어

---

## Phase 0 — 초기 세팅 (완료)

- ✅ 프로젝트 구조 설계 및 디렉토리 생성
- ✅ FastAPI 백엔드 + 모든 API 엔드포인트
- ✅ LLM 추상화 레이어 (Claude / OpenAI 전환 지원)
- ✅ 작업별 모델 티어 전략 (Lightweight / High)
- ✅ 마크다운 파일 기반 저장소 (python-frontmatter)
- ✅ URL 스크래핑 (Wanted `__NEXT_DATA__` 파싱 포함)
- ✅ PDF 텍스트 추출 (pdfplumber)
- ✅ 후보자 프로필 PDF 업로드 → LLM 자동 추출
- ✅ 적합도 평가 (이력서 vs 공고 비교, 0~100점)
- ✅ Q&A 채팅 (단일 / 다중 회사, SSE 스트리밍)
- ✅ Vanilla JS SPA 프론트엔드 (5개 뷰)
- ✅ Docker / Docker Compose 설정
- ✅ 잡플래닛 평점 자동 수집 (Naver/DuckDuckGo 스크래핑)
- ✅ 텔레그램 알림 (분석 완료 시 푸시)
- ✅ 토스트 알림 + 완료음 (Web Audio API)
- ✅ Q&A 대화 기록 localStorage 영구 저장
- ✅ 프롬프트 캐싱 (Q&A 컨텍스트 — Anthropic Haiku/Sonnet)
- ✅ nginx 프록시 타임아웃 설정 (300s — LLM 처리 시간 대응)
- ✅ README, TODO 작성
- ✅ Wanted 기업 정보 자동 수집 (임직원 수·매출·평균연봉·상장구분 — company 페이지 dehydrateState 파싱)
- ✅ 원티드 동기화 버튼 (편집 폼 source_url → `/sync-wanted` 엔드포인트)
- ✅ 상세 페이지 기업 현황 행 (investment_stage, revenue_status, jobplanet_score, 공고 원문 링크)
- ✅ 대시보드 목록 공고 원문 링크 (source_url 있는 회사)
- ✅ JS 렌더링 감지 버그 수정 (전체 HTML 길이 → 추출 본문 길이 기준)

---

## Phase 1 — POC 검증 (완료)

- ✅ API 키 설정 후 첫 end-to-end 테스트
  - ✅ 이력서 PDF 업로드 → 프로필 생성 확인
  - ✅ 텍스트 붙여넣기 → 회사 추출 + 적합도 평가 확인
  - ✅ Wanted URL 입력 → 스크래핑 + 분석 확인
  - ✅ Q&A 스트리밍 응답 확인
- ✅ Claude / OpenAI 전환 테스트 (동일 공고 결과 비교) — 프롬프트 튜닝 완료 후 진행
- ✅ 프롬프트 품질 검토 및 튜닝
  - ✅ 추출 누락 필드 보완 — `benefits`·`hiring_process` 필드 추가
  - ✅ 적합도 평가 기준 세분화 — 사용자 커스텀 기준 자유 입력 지원 (`/api/eval-criteria`)
  - ✅ GENERATE_BODY 출력 포맷 고정 — 3섹션 형식 명시 (기본정보 표·안정성 표+판단근거·공고내용 인라인코드)
  - ✅ EVALUATE_FIT 재설계 — 점수 우선순위 7항목(잡플래닛 평점 추가), 라벨 기준 테이블, fit_report_body 6섹션 포맷
  - ✅ EVALUATE_FIT strengths 스키마 강화 — 특정 프로젝트·회사 단일 경험으로 서술, 다중 프로젝트 합산 금지
  - ✅ EVALUATE_FIT 할루시네이션 방지 강화 — 경험 합산 금지("통합 처리·단일 파이프라인" 표현 금지), 각 프로젝트 개별 나열
  - ✅ EVALUATE_FIT 핵심 근거 지침 — 필수요건 미충족 항목을 커스텀 기준보다 우선하여 반드시 포함
  - ✅ EXTRACT_PROFILE 프롬프트 튜닝 — skills 3분리(tech_skills·domains·soft_skills), summary 가이드 구체화, 다중 파일 안내
  - ✅ GENERATE_PROFILE_BODY 프롬프트 튜닝 — 할루시네이션 방지 강화
  - ✅ Q&A 프롬프트 튜닝 — 현행 유지 (충분함)
  - ✅ 전체 시스템 프롬프트 할루시네이션 방지 통일
  - ✅ revenue_status 스키마 개선 — 실제 수치·설명 추출, 단순 확인만 가능하면 null
  - ✅ stability 판단 기준 — 최신(당해·전년도) 매출 데이터만 반영
- ✅ 에러 케이스 처리
  - ✅ 스크래핑 실패 시 안내 메시지 — httpx 예외 세분화 (TimeoutException / HTTPStatusError / 기타)
  - ✅ LLM API 오류 시 fallback 응답 — `LLMAPIError` 공통 예외 (인증·rate limit·서버 오류 → 사용자 친화적 메시지)
  - ✅ PDF 텍스트 추출 실패 케이스 — `PDFExtractError` (암호화·손상 PDF → 422 + 안내 메시지)

---

## Phase 2 — 기능 보완

### 스크래핑 확장
- ✅ 리멤버 커리어 지원 — `__NEXT_DATA__` JSON 파싱 (JS 렌더링 불필요)
- ✅ 이미지 업로드 지원 — 스크린샷 여러 장 → OCR → 분석 (사람인·잡코리아 등 JS 렌더링 사이트 대응)
- 💡 잡코리아·사람인 Playwright 지원 — 보류 (이미지 업로드로 대체 가능)

### 데이터 관리
- ✅ 대시보드 상태 + 적합도 점수 필터링 (검색·상태·점수·정렬 통합 `applyFilters()`)
- ✅ 지원 상태 변경 시 `지원 상태 로그` 섹션에 날짜 자동 기록
- ✅ 지원마감 상태 추가 (`status` Literal + UI 드롭다운)
- ✅ URL 중복 감지 → 409 응답 + 기존 항목으로 자동 리다이렉트
- ✅ 회사 데이터 CSV 내보내기 — 대시보드 `CSV` 버튼, `/api/companies/export/csv` 엔드포인트 (UTF-8 BOM, Excel 호환)
- ✅ 전체 데이터 백업 (zip 다운로드) — `GET /api/export/zip` (include_pdf·include_log 선택), 삭제 시 `data/backup/backup_YYYYMMDD_HHMMSS.zip` 자동 백업 (최근 5개 롤링)
- ✅ 내보내기 드롭다운 UI — 설정 페이지 `내보내기 ▾` 버튼으로 ZIP·전체 CSV·프로필 MD 통합

### 프로필 관리
- ✅ PDF 드래그&드롭 업로드 — 드롭존 UI, 복수 파일 지원
- ✅ 추가 설명 입력란 — 이력서에 없는 희망 조건·비공식 이력 입력 (localStorage 영속)
- ✅ 프로필 본문 생성 개선 — tool use(구조화)와 complete(산문)을 2단계로 분리, 3소스(PDF+추출JSON+메모) 통합
- ✅ 프로필 내보내기 — `GET /api/profile/export`, 내보내기 드롭다운에 통합 (프로필 없을 때 비활성)
- ✅ max_tokens 입력 UI — 업로드 영역에 숫자 필드(기본 8192), 잘림 감지 WARNING 로그
- ✅ 업로드 영역 카드 분리 + 아코디언 — `📤 프로필 업데이트` 카드, 프로필 없을 때 펼침·있을 때 접힘
- ✅ 자동 추출 항목 가이드 텍스트 — 희망 근무지·연봉 등 미기재 시 추가 설명란 안내
- ✅ 프로필 수동 편집 UI 개선 — 마크다운 split 에디터 (직접 편집 버튼 + 실시간 미리보기)

### 적합도 평가
- ✅ 커스텀 평가 기준 입력 — 설정 페이지에서 자유 텍스트 입력, LLM 프롬프트에 자동 주입 (`data/eval_criteria.md`)
- ✅ 연봉 평가 기준 비대칭화 — 공고 연봉이 희망 이상이면 가산, 미명시·미달은 감점 없음

---

## Phase 3 — UX / UI 개선

- ✅ 회사 추가 시 진행 단계 실시간 표시 — 프로그레스 바 + 단계별 메시지 + 경과 시간 표시 (120초 타임아웃 연동)
- ✅ 드래그&드롭 PDF 업로드 — 프로필 업로드 드롭존 UI
- ✅ CSS 변수 리팩토링 — 하드코딩된 색상 전체를 CSS custom properties로 교체 (다크모드 선행 작업)
- ✅ 다크모드 — CSS 변수 기반 테마 전환 + 토글 버튼 + localStorage 저장
- ✅ 모바일 반응형 UI
- ✅ 마크다운 편집기 개선 (코드 하이라이트, 미리보기 split)
- ✅ 즐겨찾기(핀) 카드뷰 — 대시보드 상단 핀된 회사 카드 섹션
- ✅ 상태 변경 자동 핀/언핀 — "지원" 시 자동 핀, "미지원·탈락·보류·지원마감" 시 자동 언핀
- ✅ Q&A 탭 회사 헤더 + 적합도 호버 패널 — 점수·요구 스킬·강점·약점, JS hover로 스크롤 가능
- ✅ 탈락 공고 숨기기 — 탈락·지원마감 기본 숨김, 토글로 하단 표시
- ✅ 브라우저 히스토리 — 뒤로가기·앞으로가기·URL 직접 접근 (History API)
- ✅ LLM 비용 추적 — 설정 화면 사용 이력, 작업별 토큰·비용 표시
- ✅ 핀 필터 — 대시보드 테이블 헤더 핀 아이콘 클릭으로 즐겨찾기만 표시
- ✅ 대시보드 버튼 레이아웃 — 비교 보기·선택 삭제를 toolbar-sub(하단 행)로 이동
- ✅ 비교 테이블 개선 — 최고값 녹색 하이라이트, 기술스택 칩, 강점/갭 스크롤, 잡플래닛 리뷰 수 병기, 경력 요구 행 추가, 균등 열 너비, 비교 뷰 max-width 해제

---

## Phase 4 — 고급 기능

- ✅ 지원 현황 타임라인 + 캘린더 뷰 (탭 전환, 상태 로그 파싱, 월별 그룹/달력 그리드)
- ⬜ 캘린더 일정 연동 — 마감일·면접 날짜 필드 추가 + "구글 캘린더에 추가" URL 버튼 (OAuth 불필요, URL 파라미터 방식)
- ⬜ 회사별 메모 / 연락처 관리 (담당 HR, 레퍼럴 등)
- ⬜ provider/모델별 평균 비용 요약 — `usage_log.jsonl`의 `operation`(공고 추출/본문 생성/적합도 평가 등)·`model`을 집계해 "공고 한 건 분석 평균 비용", "프로필 생성 평균 비용"을 provider·모델별로 계산해 설정 화면 사용 이력에 표시. 개별 공고에 로그를 연결하는 필드는 없으므로 등록 건수 기준 나눗셈으로 근사

---

## Phase 5 — Gemini 프롬프트 범용화 (진행중)

> 상세: `docs/phase2_gemini_experiment.md`

- ✅ Phase 2 실험 인프라 — `scripts/run_gemini_exp.py` (모델 조합 4종, LLM Judge 옵션)
- ✅ v5 오버피팅 진단 — NHN 단일 케이스에 과적합 확인 (모델 무관, 케이스 문제)
- ✅ 모델 등급 매핑 — Gemini 3.5 Flash ≈ Sonnet급, 3.1 Flash-Lite ≈ Haiku급 (기준 조합 확정)
- ✅ v6 실험 (n=3 × 3케이스) — 심각도 하드 규칙은 유효(다키 Δ17→13), 필수/우대 강조가 비요건 갭 누락 유발(NHN 3/5→2/5 악화)
- ✅ v7 튜닝·회귀 검증 — v7.4 채택 (체크리스트 31/39, v7.5는 30/39로 롤백), v0.16.2 커밋
- ✅ 단계별 파이프라인 검증 — 1단계(추출)·2단계(본문) 단독 테스트, 공용 스키마 사실성 앵커 2건 발견·제거 (`test_extract_gemini.py`, `test_body_gemini.py`)
- ✅ refill 엔드투엔드 + 교차 provider 검증 — Gemini 3사 합격(88/78/52), OpenAI 무결, Claude 프롬프트 회귀 없음
- ✅ main 승격 + prod 재빌드 (2026-07-10, merge 50c40ee) — Phase 5 완료

---

## Phase 6 — v1.0.0 사전 코드/프롬프트 리뷰 (완료, 2026-07-13 v1.0.0 태깅)

> Codex 코드/프롬프트 리뷰(`review_w_codex.md`) 반영, `fix/extra-section-placeholder` 브랜치

- ✅ 프로필 추출 시 추가 메모 placeholder 미치환 버그 수정 (4805088)
- ✅ 자격요건/우대사항 표기 기준(✅❌🔲) provider 간 불일치 해소 (20b85fd)
- ✅ 프로필 summary 필드 할루시네이션 방지 지시 강화 (1194510)
- ✅ 외부 입력 신뢰 경계 — `TRUST_BOUNDARY_NOTICE` 8개 시스템 프롬프트에 추가 (ec168e1)
- ✅ 사용자 지정 평가 기준 권한 경계 — `CUSTOM_CRITERIA_BOUNDARY_NOTICE` 추가 (0182769)
- ✅ 태그 탈출 이스케이프(`escape_tag_chars`) + 원티드/리멤버 리치텍스트 HTML 정제(`_clean_rich_text`) (58595d9)
- ✅ prompts.py 상단 docstring 섹션 번호 설명 오류 수정
- ✅ injection 방어 회귀 테스트 (실제 LLM 호출로 확인)
- ✅ `review_w_codex.md` ① refill 전체 재분석 시 사용자 관리 필드(상태/핀/태그/지원경로/생성일) + 지원 상태 로그 이력 초기화 버그 수정
- ✅ `review_w_codex.md` ② 다중 삭제 시 백업 ZIP 파일명 충돌(초 단위 → 마이크로초) 수정
- ✅ `review_w_codex.md` ③ URL 스크래핑 SSRF 방어 (스킴/호스트/DNS 검사 + redirect 재검사)
- ✅ `review_w_codex.md` ④ slug/URL 저장형 XSS 방어 (inline onclick → data-속성 + 이벤트 위임 전환, 캘린더/타임라인 JSON.stringify 파싱 버그도 함께 수정)
- ✅ `review_w_codex.md` ⑤ Q&A 대화 히스토리가 LLM 호출에 전달 안 되던 버그 수정 (단일/비교 Q&A 모두, 임의 암호코드로 멀티턴 기억 여부 검증)
- ✅ `review_w_codex.md` ⑥ API 입력 크기/개수 제한 부족 (텍스트/Q&A/PDF/이미지/URL 응답 크기 상한 추가, `_safe_get()` 재구성 시 헤더 이중 디코딩 회귀 버그도 함께 수정)
- ✅ refill/refit 지원 상태 로그 세분화 — `_append_status_log()` 신설(섹션 없으면 새로 생성), `refit_company()`(적합도만 재평가)가 로그를 남기지 않던 문제 수정, 로그 문구 판정을 `preserved_log_entries` 존재 여부가 아닌 `existing_slug`(refill 호출 여부) 기준으로 변경
- ✅ v1.0.0 승격 전 전수 코드 리뷰 (Claude 직접 리뷰, `fix/extra-section-placeholder` 브랜치 전체 diff 대상) — 문제 없음, main에 fast-forward 병합
- ✅ Codex CLI 리뷰(main 24개 커밋 전체 diff 대상) 발견 3건 수정 — SSRF 방어 DNS 리바인딩 우회 가능성(`scraper.py`), Gemini 스트림 재시도 시 응답 중복 출력(`llm/gemini.py`), 드롭다운 바깥 클릭 리스너가 내부 클릭 시 사라지는 버그(`app.js`, 재분석/내보내기 드롭다운 2곳)

---

## Phase 7 — 메신저 알림 기능 개선 (계획 확정, 미착수)

> 브레인스토밍 결론. 순서: 1(채널) → 2·3(내용/포맷) → 4(신규 트리거)

1. ✅ 채널 확장 — 슬랙·디스코드 Incoming Webhook 추가. `backend/telegram.py`의 "설정돼 있으면 전송, 없으면 스킵" 패턴을 그대로 따라 각 채널을 독립 토글로 구성(동시에 여러 채널 활성화 가능). 이메일·카카오톡은 제외(이메일은 공고 건수 대비 채널 무게감이 과함, 카카오톡은 개인용으론 진입장벽 높음). 1차는 채널별 문법 구분 없이 플레인 텍스트로 통일해서 빠르게 붙임(`backend/slack.py`, `backend/discord.py` 신설, `backend/notify.py`에서 `asyncio.gather`로 병렬 전송)
2. ✅ 분석 완료 알림 내용 보강 — 회사명·직무·점수·라벨(고정 노출)에 아래 항목을 설정에서 체크박스로 켜고 끌 수 있게 추가. 4개 모두 끄면 기존 심플 메시지로 폴백
   - 강점 요약(기본 ON) / 갭 요약(기본 ON) — `strengths`/`gaps`에서 상위 1~2개를 "👍 강점"/"👎 갭" 섹션으로 묶고 각 항목은 근거 설명을 뺀 제목만 "• " 불릿으로 표시(줄글로 이어붙이면 가독성이 떨어져 섹션+불릿 구조로 변경)
   - 잡플래닛 평점(기본 OFF), 임직원 수(기본 OFF, LLM 판단이 아닌 원문 추출값이라 안정성 평가보다 신뢰도 높음)
   - 제외 확정: 연봉 조건(원본 데이터 자체가 드묾), 안정성 평가(LLM 판단이라 신뢰도 이슈), 위치 조건(공고에서 바로 보이는 정보라 중복)
   - 부수 변경: `strengths`/`gaps` 등급 표기를 (강)·(중)·(약) / (상)·(중)·(하)로 서로 다르게 쓰던 것을 (상)·(중)·(하)로 통일하고, `prompts.py`에 강점 등급 판정 기준([강점 등급 기준] 섹션)을 갭과 동일한 논리(필수요건=상, 우대사항=중/하)로 신규 추가
   - 검증: 실제 로그인 흐름으로 `GET`/`PUT /api/settings` 라운드트립 확인 + 텍스트 붙여넣기로 실제 회사 분석 실행해 강화된 알림이 슬랙/디스코드/텔레그램에 정상 도착하는 것까지 확인
3. ✅ 채널별 포맷팅 커스터마이즈 — 알림 "재료"(회사명/직무/점수/라벨/강점/갭/잡플래닛/임직원수)를 `main.py`에서 dict로 조립하고, `backend/notify_format.py`의 `build_message(materials, bold)` 공통 빌더 하나를 텔레그램(`<b>` + `parse_mode=HTML`)·슬랙(`*굵게*`)·디스코드(`**굵게**`)가 각자 다른 `bold` 함수만 주입해서 재사용. `notify.py`는 여전히 순수 fan-out(`asyncio.gather`)만 담당
   - 검증: 실제 컨테이너 안에서 `notify.send_notification(materials)`를 직접 호출해 3채널 실제 전송, 텔레그램 굵게/슬랙 굵게/디스코드 굵게 정상 렌더링 확인
4. ✅ 주간 지원 현황 요약 알림 (신규 트리거, 즉시 발송이 아닌 주기 알림) — `main.py`에 `_weekly_summary_loop()` 백그라운드 태스크 추가(`lifespan`에서 시작, 설정된 요일·시각에 실행). `_build_weekly_summary_materials()`가 이번 주(월~오늘) 신규 등록 건수·현재 상태별 개수·방치된 항목(활성 상태 회사 중 상태 로그 마지막 갱신일로부터 7일 이상 경과)을 집계해 `kind: "weekly_summary"` 재료로 조립, `notify_format.py`의 `build_message()`가 `kind`로 분기해 렌더링(텔레그램/슬랙/디스코드 서식 재사용). 설정 화면에 온/오프 체크박스(`notify_weekly_summary`, 기본 OFF) + 요일·시각 선택 UI(`weekly_summary_weekday`/`weekly_summary_time`, 기본 월요일 09:00) 추가, 별도 "📅 주간 지원 현황 요약 알림" 섹션으로 분리(분석완료 알림 내용 섹션과 트리거 방식이 달라 사용자 지적으로 분리). 새 스케줄러 라이브러리 추가 없이 `asyncio.sleep` 기반 루프로 구현. 분석완료 알림 메시지도 함께 압축(헤더 한 줄 합침, 강점/갭 쉼표 나열)하고, 체크박스가 세로로 쌓여 빈 공간이 커지던 CSS 버그(`label` 전역 `flex-direction:column`이 `.export-check-item`에 상속)도 수정
   - 검증: `GET`/`PUT /api/settings` 라운드트립(요일/시각 포함, 잘못된 값 400 검증) + 컨테이너 안에서 `_build_weekly_summary_materials()` + `notify.send_notification()` 직접 호출해 실제 데이터 기준 3채널 정상 도착 확인 + Playwright로 설정 화면 렌더링 스크린샷 확인
   - 미포함: "다가오는 일정"(마감일·면접일 알림)은 위 "캘린더 일정 연동" 항목(마감일·면접 날짜 필드+UI 선행 필요)이 끝난 뒤 이어서 추가

---

## Phase 8 — 오픈소스 공개 준비 (진행중, 2026-07-15)

private 저장소를 지인 대상 셀프호스팅 공개로 전환하기 위한 작업. 커밋은 로컬 main에 있고(`v1.1.4`~`v1.1.6`) 아직 origin에 push 안 함.

- ✅ 히스토리 점검 — `.env`/API 키/개인정보가 git 히스토리에 커밋된 적 있는지 전수 검색, 깨끗함 확인(`docs/review-w-codex/`가 실수로 커밋됐던 건은 `git-filter-repo`로 히스토리에서 완전 제거, Phase 7 문서 참고)
- ✅ `LICENSE`(MIT) 추가, README에 라이선스 섹션 추가
- 🔧 `GETTING_STARTED.md` — 완전 비전공자 대상 설치 가이드. **아직 사용자가 계속 리뷰·수정 중** — 다음 세션에서 이어서 다듬을 것. 현재까지 반영된 것:
  - Docker Desktop 설치(계정 로그인/스킵 안내 포함) → 프로젝트 다운로드 → API 키 발급(Gemini 기본/무료 우선, Claude 선택/추천) → `run/` 폴더 스크립트로 초기설정·실행 → 로그인 → 첫 사용 순서
  - 터미널 직접 사용법은 별도 "선택" 섹션으로 분리
  - `assets/guide/`에 스크린샷 9장 자리 표시 + 캡처 목록(파일명 지정) — **사용자가 직접 캡처해서 넣기로 함, 아직 미완료**
- ✅ `run/setup.command`·`run/setup.bat` — 더블클릭으로 Gemini/Claude API 키·로그인 비밀번호를 입력받아 `.env` 자동 생성(터미널 명령어 불필요). `run/start.*`/`run/stop.*`도 함께 추가
- ✅ 기본 provider를 Claude→Gemini로 변경(`backend/config.py`) — 무료 티어로 바로 체험 가능, Claude는 "추천" 옵션으로 재배치(`.env.example`/`CLAUDE.md`/`README.md`/`GETTING_STARTED.md` 전부 반영)
- ✅ 저장소 루트 정리 — `Dockerfile`/`nginx.conf` → `docker/`, 설치·실행 스크립트 → `run/` (docker-compose.yml 경로 갱신 후 실제 재빌드로 검증 완료)
- ✅ `GETTING_STARTED.md` 스크린샷 9장 반영 완료 (2026-07-20) — 실제 캡처본으로 교체, Windows 보안/방화벽 안내 추가, 마크다운 렌더링 버그 수정. `v1.1.4`~`v1.2.0` 태그도 전부 origin push 완료
- ⬜ **다음에 할 일** (사용자 액션): (1) 실제로 GitHub 저장소를 private→public 전환, (2) 지인들에게 링크 공유

## 알려진 이슈 / 기술 부채

- ✅ `refill` 엔드포인트: 원문 텍스트(`{slug}.raw.txt`) 별도 보존으로 해결
- ✅ `refill` 슬러그 버그: 재분석 시 새 파일 생성되던 문제 → `existing_slug` 파라미터로 수정
- ✅ 슬러그 충돌: slug 구조를 `{회사명}__{직무명}` 형식으로 변경해 근본 해결. 기존 파일 일괄 마이그레이션 완료
- ✅ Wanted 외 사이트는 텍스트 붙여넣기 또는 이미지 업로드로 가능 — 탭 위 안내 박스 추가
- ✅ Q&A 한국어 IME 중복 입력 버그 — `e.isComposing` 체크 추가로 해결
- ✅ SSE 오류 전파 — `_make_sse()` try-except로 error 이벤트 전송
- ✅ LLM 클라이언트 매 요청마다 재생성 → 모듈 레벨 싱글톤 캐시 (`_provider_cache`)
- ✅ Claude thinking 블록 파싱 오류 → `block.text` hasattr 체크로 수정
- ⬜ 대용량 PDF (100페이지+) 처리 시 토큰 초과 가능성 → 청크 분할 처리 검토
- ✅ SSE 스트리밍 도중 화면 이탈 시 dangling reader 수정 (navigate/popstate에서 cancel 처리)
- ✅ SSE 스트리밍 도중 연결 끊김 시 자동 재시도 (최대 2회, 지수 백오프)
- ✅ Gemini 429(요청 한도) 처리 정확화 — `getattr(e, "status_code", None)` → `getattr(e, "code", None)`로 수정(google-genai `APIError`는 `status_code`가 아니라 `code` 속성 사용, 기존엔 항상 None이라 문자열 매칭으로만 우연히 동작하던 죽은 코드였음). 429도 재시도 대상에 포함하되 20초 대기 후 1회만 재시도(RPD 초과 시 재시도해도 무의미하므로 짧게 시도), 503류는 기존대로 최대 2회. 재시도 로그에 "429(요청 한도 초과)"로 명확히 표기. 최종 실패 메시지도 "Gemini 무료 티어 요청 한도(분당/일일)를 초과했습니다..."로 구체화. 프론트엔드(`app.js` `streamQA`)가 429/503 응답의 `err.detail`을 버리고 `HTTP 429`로만 표시하던 버그도 함께 수정 — 이제 백엔드가 보낸 실제 안내 메시지가 채팅 버블에 그대로 표시됨
- ✅ provider 런타임 설정: 서버 재시작 시 .env의 DEFAULT_PROVIDER로 리셋되던 문제 → `data/runtime_settings.json`에 저장해 재시작 후에도 유지되도록 수정 (2026-07-21)
- ✅ refill UI: 상세 페이지 `🎯 재분석 ▾` 드롭다운으로 구현 — 적합도만 재평가 / 전체 재분석 선택 가능
- ✅ prod 실데이터 오염: NHN·딥파인·다키 3사 리포트가 실험 중 Gemini(v5) 생성본으로 덮어써짐 → Claude(sonnet-4-6) refit 원복 완료 (2026-07-10, 다키 38점 = 실험 전 기준점 일치)
- ✅ 적합도 리포트 이중 헤더: 모델 출력이 개행으로 시작하면 `re.sub` `^` 앵커 미스매치 → strip 순서 수정 (v0.16.2, main.py 4곳)
- ⬜ Claude haiku 추출 시 매출 필드에 원문에 없는 연도 날조 1건 관찰 (n=1, 지시문 방어선 있음) → 재발 시 "원문 발췌 문자열만 허용" 제약 검토 (공용 스키마라 타 모델 간섭 주의)
- ✅ OpenAI High=`gpt-5`(reasoning_effort=medium)로 `/api/companies/from-url` 실행 시 적합도 평가 단계가 120초를 넘겨 5/5 전부 504 Gateway Timeout (2026-07-13, dev 실측) — `_process_company` 호출부 3곳(URL/텍스트/이미지)의 `asyncio.wait_for` 타임아웃을 120초→300초로 상향해 nginx 일반 API 제한(300초)과 통일(v1.1.1). Codex 전체 코드 리뷰(`review_w_codex_2026-07-14_v1.0.5.md`)에서 발견된 나머지 7건(slug URL 인코딩, Q&A 히스토리 40개 제한, 비교 5개 제한, 백업 fail-closed, 재분석 타임라인 노출, 텔레그램 오류감지, 타임존)도 함께 수정
- ✅ Gemini 429 오류 메시지가 실제 원인과 무관하게 항상 "무료 티어 요청 한도 초과"로 고정 출력되던 버그 수정 (2026-07-13). refit 비용 측정 중 유료(prepay) 계정에서 429가 발생했는데도 무료 티어 문구가 나가 사용자 혼선 발생 — docker 로그에서 확인한 구글 원본 오류는 `Your prepayment credits are depleted`(선불 크레딧 소진)였음. `gemini.py` `_raise()`에서 `e.message`(google-genai `APIError`가 원본 메시지를 그대로 담고 있음)를 읽어 "prepay" 포함 시 크레딧 소진 안내로, 그 외 429는 원본 메시지를 함께 노출하도록 수정

---

## Phase 9 — LLM provider 경쟁조건 수정 + 진행중 표시 + Docker 없는 uv 실행 지원 (완료, v1.1.8~v1.2.0, 2026-07-20)

- ✅ LLM provider 전역 상태 경쟁조건 수정 (v1.1.8) — 회사 분석 도중 설정 화면에서 provider를 바꾸면 같은 분석 1건 안에서 provider가 섞이던 문제. `LLMSnapshot`(`backend/llm/router.py`) + `capture_snapshot()`/`light_from_snapshot()`/`high_from_snapshot()`로 파이프라인 시작 시점에 provider·모델·reasoning_effort를 고정. 실제로 분석 도중 provider를 전환해 섞이지 않는지 재현 테스트로 검증
- ✅ 분석 진행 중 표시 배너 (v1.1.9) — `_track_in_progress` 데코레이터로 서버에 진행 건수 기록, `GET /api/analysis-in-progress`를 7초 주기로 폴링해 네비게이션 바에 "N건 분석 중..." 표시. 페이지를 이동해도 유지돼 실수로 같은 분석을 중복 제출하는 것을 방지
- ✅ Codex 리뷰 후속 수정 3건 (v1.1.9) — 이미지 분석 OCR↔이후 단계 간 provider 경쟁조건, 프로필 생성 1·2단계 간 reasoning_effort 경쟁조건, 진행 배너 카운터가 URL 스크래핑·이미지 OCR 같은 앞단 구간을 못 세던 범위 문제(카운터를 `_process_company()`가 아니라 `add_from_text`/`from-url`/`from-image`/`refill` 4개 API 진입점 자체로 이동)
- ✅ `.env` API 키 인식 실패 버그 수정 (v1.1.10) — Windows 실사용 중 발견. `.env`에 BOM이 붙어 저장되면 첫 번째 키(`GOOGLE_API_KEY`)를 인식 못 하던 문제(`env_file_encoding` `utf-8`→`utf-8-sig`), `run/setup.bat`에 `setlocal enabledelayedexpansion`이 없어 입력한 키 값이 반영 안 되던 지연 확장 버그, `.env` 재작성 시 주석 한글이 깨지던 문제까지 함께 수정
- ✅ Docker 없이 `uv`로 실행하는 대안 경로 추가 (v1.2.0) — `run/start-uv.command`/`.bat` 신규(uv 미설치 시 자동 설치 여부 질문, Y/n 기본 Y). `setup.command`/`.bat` 제거하고 각 `start-*` 스크립트가 `.env` 없으면 자체적으로 초기 설정까지 처리(더블클릭 1번으로 축소). `start`/`stop`을 `start-docker`/`stop-docker`로 이름 통일. `backend/main.py`에 `frontend/` 폴더가 있을 때만 정적 파일을 직접 서빙하는 조건부 마운트 추가(Docker는 그대로 nginx가 서빙) + 인증 미들웨어 범위를 `/api/*`로 제한(정적 파일까지 인증 걸려 화면이 401로 막히던 문제 수정). Windows `.bat` 파일이 CP949가 아닌 UTF-8로 저장돼 한글이 깨져 명령어로 오인식되던 실전 이슈 발견·수정. `GETTING_STARTED.md`를 uv 기본 경로로 전면 재구성(7단계→5단계), 실제 스크린샷 9장 반영, Windows 보안 경고·방화벽 허용 안내 추가, 볼드+한글 조사 결합 시 마크다운 렌더링이 깨지던 CommonMark 버그 4곳 수정(`marked`로 실제 렌더링 검증)
- 실제 Windows 환경(가상머신)에서 처음부터 끝까지(다운로드 → uv 설치 → 초기 설정 → 실행 → 로그인 → 회사 분석) 실행해 검증 완료

## Phase 10 — public 전환 전 보안 점검 + backend 구조 리팩터링 + RAG 서브프로젝트 착수 (완료/진행중, v1.2.1~v1.2.4, 2026-07-21)

- ✅ public 전환 전 민감정보 전수조사 + 코드 리뷰 (v1.2.1) — git 히스토리·워킹트리 전체에서 API 키/토큰 패턴 검색(0건), `data/` 미추적 재확인. 코드 리뷰에서 발견된 경미한 이슈 3건 수정: 이미지 업로드 압축폭탄 방지(`_MAX_IMAGE_PIXELS` 1600만 상한), 로그인 비밀번호 비교를 `hmac.compare_digest()`로 상수시간화, `/api/models` 실패 시 Gemini API 키가 URL 쿼리파라미터로 노출되던 예외 메시지를 서버 로그로만 남기도록 수정
- ✅ `docs/phase1_comparison_report.md`/`phase2_gemini_experiment.md`/`phase_d_plan.md`(실제 지원 검토 회사명·부정 평가 포함) 3개 파일을 `git filter-repo`로 전체 히스토리(140개 커밋, 태그 18개, main·dev 브랜치)에서 완전 제거 후 force-push, `.gitignore`에 `docs/phase*.md` 추가. 재작성 전 히스토리는 로컬 백업 bundle로 보관
- ✅ backend 모듈 구조 리팩터링 (v1.2.2~v1.2.3, 기능 변경 없음) — `main.py`(1436줄)를 `auth.py`(로그인/JWT/인증 미들웨어), `export.py`(백업/export 유틸), `routers/{settings,profile,companies,qa}.py`로 분리해 53줄로 축소. `notify.py`/`notify_format.py`/`discord.py`/`slack.py`/`telegram.py` → `notify/` 패키지, `usage_tracker.py`/`pdf_parser.py`/`scraper.py`/`jobplanet.py` → `services/` 패키지로 재배치. 라우트 32개 경로·순서 동일 여부를 실제 앱 로드로 대조, 원본과 새 코드를 함수 단위 AST 비교로 검증(45개 함수 중 의도한 이름 변경 7건 외 차이 없음), dev/prod 재빌드 후 전 엔드포인트 실제 요청으로 확인
- ✅ provider/모델/알림설정/주간요약 스케줄 재시작 후 리셋되던 문제 수정 (v1.2.4) — `data/runtime_settings.json`에 원자적 저장(`.tmp`→`os.replace()`), 재시작 시 자동 복원. dev/prod에서 실제 컨테이너 재시작으로 유지 여부 검증
- 🔧 **RAG 서브프로젝트** — main에는 merge하지 않는 영구 브랜치 `rag/main`에서 별도 진행 중. 상세 진행상황·다음 할 일은 그 브랜치의 `backend/rag/README.md`와 `docs/rag-project-plans/00_meta/STATUS.md`에서 관리 — 이 TODO.md에는 더 이상 세부 내용을 적지 않는다.

---

## Phase 11 — RAG 서브프로젝트 main 반영 + MCP 설계 (계획 배경만 정리, 구체화 전)

> 상세: `docs/mcp_plan_notes.md`(MCP 부분). RAG는 `rag/main` 브랜치에서 별도 진행 중(대화형 근거 기반 RAG → RAG 모듈 안정화, 상세는 `rag/main` 브랜치의 `docs/rag-project-plans/00_meta/STATUS.md`).

- ⬜ RAG(`rag/main`) 완료 후 main 반영 — 브랜치를 통째로 merge하지 않고, 완성된 코드만 새 `feat/...` 브랜치로 재작성해 main에 반영(실험·시행착오 히스토리는 `rag/main`에만 남김)
- ⬜ 그 다음 MCP 설계·구현 착수 — 세부 도구·전송 방식·인증·쓰기 승인 정책은 아직 미확정
