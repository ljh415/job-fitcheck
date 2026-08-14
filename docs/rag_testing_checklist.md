# RAG 기능 테스트 체크리스트

main 반영(merge) 직후 회귀 확인용. 카테고리별로 독립적으로 실행 가능 — 순서 안 지켜도 됨.
각 항목은 "단계 → 기대 결과" 형식. 자세한 사용법은 [RAG_GUIDE.md](../RAG_GUIDE.md), 코드
구조는 `backend/rag/README.md` 참고.

## 0. 사전 준비

- [ ] `.env`에 `RAG_POSTGRES_HOST=rag-postgres` 설정
- [ ] `docker compose --profile rag up --build` 실행 → `rag-postgres`/`api` 둘 다 기동 확인

## 1. Opt-in 토글

- [ ] `RAG_POSTGRES_HOST` 미설정 상태로 실행 → 네비게이션에 **🤖 RAG** 버튼 안 보임, 회사 등록/평가/Q&A 등 기존 기능 정상
- [ ] `RAG_POSTGRES_HOST` 설정 후 재실행 → 버튼 노출
- [ ] 새로 띄운 Postgres는 비어있음 → 재색인 전에 질문하면 오류(정상 — 아래 2번으로)

## 2. 최초 재색인

- [ ] `/rag` 진입 → **🔄 재색인** 클릭 → 완료까지 대기(`docker compose logs -f api`로 진행 확인 가능)
- [ ] 완료 후 아무 기술명(예: `Python`)으로 갭 체크 또는 질문 → 정상 응답
- [ ] 재색인 중 다시 재색인 버튼 클릭 → 409(진행 중 안내)

## 3. Provider 설정

- [ ] `/rag` → ⚙️ 설정 팝업 → 현재 provider 확인(기본 Google)
- [ ] 다른 provider로 전환(구성돼 있는 경우) → 저장 시 확인창(재색인 필요 안내) → 승인 → 전환 완료까지 대기
- [ ] 전환 실패 시나리오(예: local provider인데 SSH 터널 불가) → override는 이전 값 유지, 에러 메시지 표시

## 4. Gap-check (기술 갭 분석)

- [ ] `RAG_INCLUDE_PROFILE=false`(기본값) → `/rag`에 스킬 갭 확인 폼 자체가 안 보임
- [ ] `RAG_INCLUDE_PROFILE=true`로 설정 + 재색인 1회 실행 → 폼 노출, 기술명 입력 시 근거 수준(직접 근거/부분 근거/인접 경험/근거 없음) + 발췌문 + 행동 계획 표시

## 5. Ask (Agent 채팅)

- [ ] 단일 도구 질문(예: "Redis 요구하는 공고 몇 개야?") → `get_market_demand` 도구 사용, 정확한 숫자 응답
- [ ] 복합 도구 질문(예: "백엔드 직무 중 쿠버네티스 공고 알려줘") → `list_matching_postings` 등 적절한 도구 조합
- [ ] 멀티턴(이전 답변 참조하는 후속 질문) → 문맥 유지된 응답
- [ ] **+ 새 채팅** / 채팅 전환 드롭다운 / **🗑 삭제** 각각 정상 동작
- [ ] 질문 응답 대기 중(전송 버튼 "전송 중..." 상태) **+ 새 채팅** 클릭 → 새 채팅이 사라지지 않고 유지됨(2026-08-14 수정 확인용)
- [ ] 질문 응답 대기 중 현재 채팅을 삭제 → 응답 도착 후에도 삭제된 채팅이 되살아나지 않음

## 6. CRUD 자동 동기화

- [ ] 회사 신규 등록(URL/텍스트/이미지 아무 경로나) → 잠시 후 RAG 질문에 그 회사가 검색됨(자동 재색인)
- [ ] 회사 수동 편집(`tech_stack` 등 변경) → RAG `compare_companies`/`list_matching_postings` 결과에 반영됨
- [ ] 회사 삭제 → RAG 질문·비교 결과에서 더 이상 안 나옴
- [ ] `RAG_INCLUDE_PROFILE=true` 상태에서 프로필 재업로드/수정 → gap-check 결과가 갱신된 프로필 기준으로 나옴

## 7. 끄기/정리

- [ ] `.env`에서 `RAG_POSTGRES_HOST` 제거 후 재실행 → 버튼 사라짐, 나머지 기능 영향 없음
- [ ] `docker compose --profile rag down` → `rag-postgres` 컨테이너 종료 확인(profile 없이 `up`만 해서는 안 꺼짐)
