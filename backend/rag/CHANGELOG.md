# RAG 서브프로젝트 Changelog

`rag/main` 브랜치 전용 — main 브랜치엔 없다. 버전 번호는 루트 `CHANGELOG.md`(앱 버전, `v1.x.x`)와
겹치지 않게 별도 네임스페이스(`rag-v0.x.0`)를 쓴다. 나중에 정식으로 main에 merge한다면(순수 추가
기능이라 semver 기준 마이너 릴리스에 해당 — 기존 기능을 하나도 안 건드림), 그 시점 버전(예: v1.3.0)
담당자가 이 파일 내용을 참고해 옮기면 된다.

상세 이력·설계 논의는 `docs/rag-project-plans/00_claude_handoff.md`(git 미추적)에 exhaustively
기록돼 있음 — 이 파일은 그걸 압축한 요약.

## rag-v0.18.0 — Codex 5차 재리뷰 2건 추가 수정 (2026-07-24)

rag-v0.17.0의 2건 수정을 재검토 요청 — 둘 다 요청 범위에서 해결 확인, conn 중복 close도 없음. 신규 Medium 1건·Low 1건 발견.

- **[Medium] RAG 라우터가 `LLMAPIError`를 안 잡아서 LLM 장애가 500으로 반환됨**: `assess_gap()`/`generate_action_plan()`이 쓰는 LLM provider(Claude/OpenAI/Gemini 텍스트 생성)의 인증 실패·rate limit·서버 오류는 `LLMAPIError`로 래핑되는데(`routers/companies.py` 등 기존 라우터가 이미 쓰는 패턴), `routers/rag.py`엔 이 except가 없었음 — `except LLMAPIError as e: raise HTTPException(e.status_code, ...)` 추가(자체 status_code 그대로 반영)
- **[Low] `ingest_run()` 내부(`ingest_postings`/`ingest_skill_alias`/`ingest_candidate_evidence`) 실패 시 conn이 호출부에 반환 안 돼 정리 안 됨**: `reindex.py`의 `_run_with_conn()` 분리와는 별개의 더 안쪽 경계 — `ingest.run()`이 만든 연결이니 실패 시 자체적으로 닫고 재발생하도록 수정
- 이제까지 총 5차 재검토, 누적 수정: High 1건(경고로 관리하는 정책으로 수용)·Medium 8건·Low 5건. async 이벤트루프 이슈만 의도적으로 미해결 상태 유지(문서화, 위 rag-v0.17.0 참고)

## rag-v0.17.0 — Codex 4차 재리뷰 2건 추가 수정 + async 이벤트루프 이슈 문서화 (2026-07-24)

- **[Medium] Google API 5xx 오류가 여전히 500으로 샘**: `except genai_errors.ClientError`가 4xx만 잡았음 — `ClientError`/`ServerError`는 둘 다 `APIError`의 하위 클래스인데(코드로 직접 확인) 상위 클래스로 안 잡아서 5xx는 누락. `APIError`로 확장
- **[Low] `reindex.py`의 `prune_deleted_postings()`/`populate_posting_chunks()`/`schema_conn`이 여전히 try 밖에 있어서 그 구간 실패 시 conn 미종료** — 전체를 `_run_with_conn()`으로 분리해 바깥 try/finally로 감싸고, `schema_conn`도 개별 try/finally로 정리
- **[신규 발견, Medium, 의도적으로 보류] `async def gap_check()`가 동기 DB/Google SDK/httpx 호출을 그대로 실행해 이벤트 루프를 막을 수 있음** — FastAPI는 `async def` 안에서 부른 일반 함수를 스레드풀로 자동 이동시켜주지 않음(공식 문서 확인). 특히 Local provider는 최대 600초 타임아웃이라 그 동안 같은 워커가 막힐 수 있음. **사용자 결정: 지금은 개인용 단일 사용자 툴이라 영향이 작아 문서화만 하고 넘어감** — 실제 다중 사용자 서비스로 확장할 때 재검토(`docs/rag-project-plans/00_claude_handoff.md` "향후 탐색 아이디어" 참고)

## rag-v0.16.0 — Codex 3차 재리뷰 4건 추가 수정 (2026-07-24)

rag-v0.15.0의 4건 수정을 다시 재검토 요청 — 4건 모두 의도한 경로에 반영됨을 확인, 신규 Medium 2건·Low 2건 발견.

- **[Medium] Google 임베딩 API 오류가 500으로 샘**: `google.genai.errors.ClientError`(429 재시도 소진, API 키 오류 등)가 `RuntimeError`/`httpx.HTTPError`/`psycopg.Error` 어디에도 안 걸려 그대로 500으로 샜음(LocalEmbeddingProvider와 비대칭) — `except genai_errors.ClientError` 추가, 503 매핑
- **[Medium] `except psycopg.Error`가 너무 넓어서 실제 버그도 503으로 가려질 수 있었음**: `psycopg.Error`는 연결 실패뿐 아니라 SQL 문법·제약조건 위반 같은 프로그램 버그도 포함 — `psycopg.OperationalError`(연결 계열만)로 좁힘. 응답 메시지도 내부 정보(호스트·스키마) 노출 없이 일반화
- **[Low] 경고 문구가 최초 색인에서도 "사라졌습니다"로 부정확하게 표시됨**: 신규/변경 양쪽에 다 맞는 "생성되지 않았습니다"로 수정
- **[Low] `hnsw_eval.py`/`reindex.py`의 CLI 자원 정리가 일부 경로에서 불완전**: provider 생성 실패 시 `conn`이 안 닫히거나, `hnsw_eval.py`는 정상 종료 경로에도 `conn.close()`가 없었음(smoke 검증도 try 밖) — 둘 다 프로세스 종료로 대부분 회수되는 CLI라 Low였지만 `routers/rag.py`와 일관되게 수정
- 요청한 4건(provider 경고, DB 연결 503, HNSW N+1 제외, 자원 독립 정리) 전부 해결 확인됨

## rag-v0.15.0 — Codex 재리뷰 3건 추가 수정 (2026-07-24)

rag-v0.14.0에서 반영한 6건 수정을 다시 Codex에 재검토 요청 — 5건은 해결 확인, High 1건은 "경고만 추가됐고 실제 문제는 남아있다"는 지적, 추가로 Medium 2건·Low 1건 신규 발견. 전부 재현·확인 후 수정.

- **[High, 부분 재발견] 다른 provider 경고가 삭제 이후 조회라 누락될 수 있었음**: `reindex.py`의 경고 로직이 `populate_posting_chunks()`가 이미 옛 임베딩을 삭제·커밋한 뒤 `SELECT DISTINCT provider FROM chunk_embedding`으로 "남아있는" provider를 조회했는데, 변경된 posting이 어떤 provider의 유일한 출처였다면 그 provider는 이미 삭제돼 조회에 안 잡히고 경고도 안 나옴. DB 상태 조회 대신 `PROVIDERS` 레지스트리 자체와 비교하도록 수정(항상 정확). 프로필 재임베딩 시에도 같은 경고 추가(기존엔 없었음)
- **[Medium] DB 연결 실패가 503으로 안 잡힘**: `get_connection()` 실패는 `psycopg.OperationalError`(→`psycopg.Error`)인데 `RuntimeError`/`httpx.HTTPError`만 잡고 있어서 500으로 샜음 — `except psycopg.Error` 추가
- **[Medium] HNSW 지연시간 측정에 N+1 posting 조회가 섞여 있었음**: 원격 임베딩은 이미 타이머 밖으로 뺐지만, `ranked_postings_by_score()`의 chunk_id별 개별 SQL 조회가 여전히 타이머 안에 있었음 — posting 매핑을 반복 루프 밖으로 이동, 벡터 검색 SQL 자체만 측정(15~20ms → 10~13ms로 더 정확해짐, 결론은 동일: 여전히 HNSW 미사용)
- **[Low] `embed_provider.close()` 실패 시 `conn.close()`가 건너뛰어질 수 있었음**: `LocalEmbeddingProvider.close()`의 SSH 터널 종료 대기가 타임아웃 예외를 던지면 그다음 줄이 실행 안 됨 — 각 자원을 독립적으로 정리하도록 수정(하나가 실패해도 나머지는 정리됨)
- 새 SQL injection·CHECK/HNSW 컬럼 오류는 발견되지 않음. `rebuild_schema()` 자체의 원자성은 안전하나 이후 적재·청킹·임베딩은 여전히 별도 커밋(원래 알던 트레이드오프, 이번에도 그대로 인지하고 넘어감)

## rag-v0.14.0 — Codex 코드 리뷰 6건 수정 + Stage 4 HNSW 결론 정정 (2026-07-23)

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
