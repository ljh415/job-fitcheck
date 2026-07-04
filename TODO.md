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
- ⬜ Claude / OpenAI 전환 테스트 (동일 공고 결과 비교)
- ⬜ 프롬프트 품질 검토 및 튜닝
  - ⬜ 추출 누락 필드 보완
  - ✅ 적합도 평가 기준 세분화 — 사용자 커스텀 기준 자유 입력 지원 (`/api/eval-criteria`)
  - ⬜ Q&A 응답 퀄리티 개선
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
- ⬜ 프로필 수동 편집 UI 개선 (폼 형태)
- ⬜ PDF 여러 버전 관리 (이력서 히스토리)

### 적합도 평가
- ✅ 커스텀 평가 기준 입력 — 설정 페이지에서 자유 텍스트 입력, LLM 프롬프트에 자동 주입 (`data/eval_criteria.md`)
- ✅ 연봉 평가 기준 비대칭화 — 공고 연봉이 희망 이상이면 가산, 미명시·미달은 감점 없음

---

## Phase 3 — UX / UI 개선

- ✅ 회사 추가 시 진행 단계 실시간 표시 — 프로그레스 바 + 단계별 메시지 + 경과 시간 표시 (120초 타임아웃 연동)
- ✅ 드래그&드롭 PDF 업로드 — 프로필 업로드 드롭존 UI
- ⬜ 모바일 반응형 UI
- ⬜ 마크다운 편집기 개선 (코드 하이라이트, 미리보기 split)
- ⬜ 다크모드
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
- ⬜ 주간 지원 현황 요약 리포트 생성

---

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
- ⬜ provider 런타임 설정: 서버 재시작 시 .env의 DEFAULT_PROVIDER로 리셋 (PoC 단계라 허용)
