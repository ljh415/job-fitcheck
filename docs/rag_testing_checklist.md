# RAG 기능 테스트 체크리스트

main 반영(merge) 직후 회귀 확인용. 카테고리별로 독립적으로 실행 가능 — 순서 안 지켜도 됨.
각 항목은 "단계 → 기대 결과" 형식. 자세한 사용법은 [RAG_GUIDE.md](../RAG_GUIDE.md), 코드
구조는 `backend/rag/README.md` 참고.

## 0. 사전 준비

- [x] `.env`에 `RAG_POSTGRES_HOST=rag-postgres` 설정(+ 나머지 RAG_POSTGRES_* 4개,
      `RAG_INCLUDE_PROFILE=false`) — merge 전 `.env` 백업 후 진행
- [x] `docker compose --profile rag up --build` 실행 → `rag-postgres`/`api` 둘 다 기동 확인.
      **부수 발견**: nginx가 4주째 재시작 안 된 상태라 API 컨테이너 재생성 후 502 발생 —
      `docker compose restart nginx`로 해결(merge와 무관한 운영 이슈, 코드 버그 아님).
      `/api/rag/status` → `{"enabled":true,"configured_providers":["google"],
      "include_profile":false}` 확인(2026-08-15)

## 1. Opt-in 토글

- [x] `RAG_POSTGRES_HOST` 미설정 상태로 실행 → 네비게이션에 **🤖 RAG** 버튼 안 보임, 회사 등록/평가/Q&A 등 기존 기능 정상 — P0 단계에서 Playwright로 이미 확인(중복 실행 안 함)
- [x] `RAG_POSTGRES_HOST` 설정 후 재실행 → 버튼 노출 — Playwright로 확인(2026-08-15)
- [x] 새로 띄운 Postgres는 비어있음 → 재색인 전에 질문하면 오류(정상 — 아래 2번으로) — `/api/rag/ask` 호출 시 크래시 없이 "내부 오류가 발생해서..." 안내 메시지로 정상 처리(HTTP 200)

## 2. 최초 재색인

- [x] `/rag` 진입 → **🔄 재색인** 클릭 → 완료까지 대기(`docker compose logs -f api`로 진행 확인 가능) — `POST /api/rag/reindex` 200, 90개 회사 → 청크 240개, gemini-embedding-2로 임베딩 완료(2026-08-15)
- [x] 완료 후 아무 기술명(예: `Python`)으로 갭 체크 또는 질문 → 정상 응답 — "Python 요구 공고 몇 개야?" → `get_market_demand` 도구로 49/90건(54.4%) 정확히 응답
- [x] 재색인 중 다시 재색인 버튼 클릭 → 409(진행 중 안내) — 진행 중 중복 요청 시 "재색인이 이미 진행 중입니다" 409 정상 확인

## 3. Provider 설정

- [x] `/rag` → ⚙️ 설정 팝업 → 현재 provider 확인(기본 Google) — `GET /api/rag/settings` →
      `{"override":null,"resolved":"google",...}` 확인
- [ ] 다른 provider로 전환(구성돼 있는 경우) → 저장 시 확인창(재색인 필요 안내) → 승인 →
      전환 완료까지 대기 — 미실행(성공 케이스는 개인 GPU 서버가 실제로 켜져 있어야 하는데
      지금 꺼져있음/잠자기 상태라 재현 불가, 아래 TODO로 남김)
- [x] 전환 실패 시나리오(예: local provider인데 SSH 터널 불가) → override는 이전 값 유지,
      에러 메시지 표시 — dev의 SSH 설정을 prod에 임시로 가져와 실제 시도(개인 GPU 서버가
      꺼져있는 실제 상황), `PUT /api/rag/settings` → 503 + "SSH 터널이 시작 직후 종료됨"
      명확한 에러, 이후 조회해도 `override: null, resolved: "google"`로 이전 값 그대로
      유지 확인(2026-08-15)

## 4. Gap-check (기술 갭 분석)

- [x] `RAG_INCLUDE_PROFILE=false`(기본값) → `/rag`에 스킬 갭 확인 폼 자체가 안 보임 —
      Playwright로 폼 엘리먼트 전부 숨김, 비활성 안내만 노출 확인
- [x] `RAG_INCLUDE_PROFILE=true`로 설정 + 재색인 1회 실행 → 폼 노출, 기술명 입력 시 근거
      수준(직접 근거/부분 근거/인접 경험/근거 없음) + 발췌문 + 행동 계획 표시 —
      "PostgreSQL" 조회 시 evidence_level="인접 경험"(MySQL 경험 기반 정확 판정), 실제
      이력서 발췌문 5개, 구체적 행동계획(마이그레이션 실습+완료 기준)까지 전부 정상
      생성 확인(2026-08-15)

## 5. Ask (Agent 채팅)

- [x] 단일 도구 질문(예: "Redis 요구하는 공고 몇 개야?") → `get_market_demand` 도구 사용,
      정확한 숫자 응답 — 90개 중 5개(5.6%) 정확 응답
- [x] 복합 도구 질문(예: "백엔드 직무 중 쿠버네티스 공고 알려줘") → `list_matching_postings`
      등 적절한 도구 조합 — 위밋모빌리티/삼양식품 2건 표로 정확히 응답
- [x] 멀티턴(이전 답변 참조하는 후속 질문) → 문맥 유지된 응답 — "MongoDB 몇 개?"(0건) →
      "거기에 Redis까지 요구하는 곳도?" → 이전 답변(0건)을 정확히 참조해 "0건" 응답
      (2026-08-15)
- [x] **+ 새 채팅** / 채팅 전환 드롭다운 / **🗑 삭제** 각각 정상 동작 — Playwright로 생성 2회
      (목록 증가 확인)·전환(선택값 반영)·삭제(목록 감소) 전부 확인
- [x] 질문 응답 대기 중(전송 버튼 "전송 중..." 상태) **+ 새 채팅** 클릭 → 새 채팅이 사라지지
      않고 유지됨(2026-08-14 수정 확인용) — 실제 도구 호출 있는 무거운 질문으로 pending
      상태 확보 후 새 채팅 생성, 응답 완료 후에도 새 채팅 유지 확인(2026-08-15)
- [x] 질문 응답 대기 중 현재 채팅을 삭제 → 응답 도착 후에도 삭제된 채팅이 되살아나지 않음 —
      pending 중 현재 채팅 삭제 → 응답 완료 후에도 목록에서 계속 사라진 상태 유지 확인

## 6. CRUD 자동 동기화

- [x] 회사 신규 등록(URL/텍스트/이미지 아무 경로나) → 잠시 후 RAG 질문에 그 회사가 검색됨
      (자동 재색인) — "Zig" 키워드로 베이스라인 0건 확인 → 테스트 회사 등록 → 자동
      재색인(증분 1건) → 재질문 시 정확히 그 회사 1건 검색됨 확인(2026-08-15)
- [x] 회사 수동 편집(`tech_stack` 등 변경) → RAG `compare_companies`/`list_matching_postings`
      결과에 반영됨 — tech_stack을 "CRDT분산시스템"으로 편집 → 재색인 로그(청크는 원문
      불변이라 재생성 0건, 정상) → Postgres `posting.tech_stack` 직접 조회로 정확히
      동기화됨 확인
- [x] 회사 삭제 → RAG 질문·비교 결과에서 더 이상 안 나옴 — 삭제 후 Postgres에서 즉시 사라짐
      (0 rows), RAG 질문도 0건으로 복귀, 전체 회사 수 90개로 원상복구 확인
- [x] `RAG_INCLUDE_PROFILE=true` 상태에서 프로필 재업로드/수정 → gap-check 결과가 갱신된
      프로필 기준으로 나옴 — 백업 → 더미 이력서 재업로드(자동 재색인 트리거 확인) →
      "FastAPI" gap-check 시 evidence_level="직접 근거" + 더미 프로필(QA Tester/Dummy
      Corp) 발췌문 반영 확인 → 원본 프로필 복원(체크섬 일치) + `RAG_INCLUDE_PROFILE`도
      `false`로 원복(2026-08-15)

## 7. 끄기/정리

- [x] `.env`에서 `RAG_POSTGRES_HOST` 제거 후 재실행 → 버튼 사라짐, 나머지 기능 영향 없음 —
      Playwright로 RAG 버튼 숨김 + 회사 목록 78건 정상 노출 확인, `/api/rag/status`도
      `enabled:false` 확인
- [x] `docker compose --profile rag down` → `rag-postgres` 컨테이너 종료 확인(profile 없이
      `up`만 해서는 안 꺼짐) — `docker compose up -d`(profile 없이) 후 `rag-postgres`가
      계속 Up 상태임을 먼저 확인, `--profile rag down`으로 실제 제거됨 확인. 이후 `.env`를
      RAG 테스트 시작 전 상태로 완전히 복원(체크섬 일치)하고 최종 재기동까지 완료
      (2026-08-15)
