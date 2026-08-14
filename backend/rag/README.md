# RAG 서브프로젝트 — `backend/rag/`

Job FitCheck가 이미 모아둔 채용공고 데이터와 본인 프로필을 가지고 **"검색으로 관련 정보를 찾아서, 그 근거를 바탕으로 LLM이 답하게 하는"** 시스템(RAG = Retrieval-Augmented Generation)을 직접 만들어본 학습용 서브프로젝트에서 시작해, main에 정식 기능으로 반영된 결과물입니다. 이 문서는 **지금 이 브랜치의 코드가 뭘 하는지**를 처음 보는 사람도 알 수 있게 설명하는 게 목적입니다. 아래 곳곳에 나오는 `docs/rag-project-plans/`는 개발 초기 계획 문서로 **`rag/main` 브랜치 전용(git 미추적, 이 브랜치엔 없음)** — 지금 진행 중인 main 반영 작업 자체의 상태는 `docs/rag-integration/STATUS.md`·`HISTORY.md`(이것도 git 미추적, 이 브랜치 전용)를 본다.

## 한눈에 보기

- **지금 상태**: Plan A·B 전체 완료 + "대화형 근거 기반 RAG" Phase 1~4 구현·검증 완료 + **Agentic RAG 아키텍처 전환·전수 검증 완료(2026-07-29)**. Phase 1의 "질문 분류→고정 함수 실행" 방식이 애매한 질문에 무관한 답을 내는 구조적 결함이 실사용 중 발견돼, Agent(tool-use, 개발 당시엔 Claude 전용·현재는 Claude/OpenAI/Gemini 전부 지원)가 RAG 도구들을 스스로 선택·조합하는 구조로 전환. 5개 카테고리(단일 도구/복합 도구/멀티턴/답변 불가/함정형) 15문항 전수 검증 완료(14/15 명확 통과) — 상세는 `conversational-rag/00_design.md`의 "아키텍처 전환"/"Provider 일관성 수정" 섹션.
- **정체성 — Agentic RAG**: `POST /api/rag/ask`는 질문을 고정 분류하지 않는다. `assess_skill_gap`/`list_matching_postings`/`get_market_demand` 등 RAG 동작(검색 후 LLM 판정) 하나하나를 도구로 노출하고, LLM이 질문마다 무엇을 몇 개나 쓸지 스스로 판단해 답을 종합한다(`rag/postgres/agent.py`, `llm/*.py`의 `run_agent()`). 도구 선택은 코드 분기가 아니라 각 provider API의 tool-use 기본 동작에 위임돼 있다. Agent는 Claude 전용이었으나(2026-07-29) main 반영 작업으로 Claude/OpenAI/Gemini 전부 지원하도록 확장됨(2026-07-30) — 임베딩만 빼고 오케스트레이션·도구 내부 판정 LLM 둘 다 `llm.router.high_provider()`, 즉 메인 앱의 현재 provider 설정을 그대로 따른다(설정 화면에서 provider를 바꾸면 Agent도 같이 바뀜).
- **실제 서비스 경로**: `POST /api/rag/gap-check`(기술명 직접 지정) + `POST /api/rag/ask`(자연어 질문 → Agent가 도구 자유 선택) + `POST /api/rag/reindex`(공고/프로필 변경사항을 현재 활성 embedding provider 하나로 반영) → `backend/rag/postgres/` (PostgreSQL+pgvector). embedding provider는 메인 LLM provider에서 자동 매핑되며(`resolve_rag_embedding_provider()`) 설정 화면에서 override 가능 — "여러 provider를 요청마다 선택"하던 이전 구조는 main 반영 과정에서 단일 활성 provider로 재설계됨(아래 3번 설계 결정 표 참고). UI는 main SPA(`frontend/index.html`의 `/rag` 뷰)에 통합돼 있고, 독립 테스트 화면이던 `rag-test.html`은 이 브랜치(main 반영용)엔 없다. SQLite 코드(`backend/rag/*.py`)는 삭제 안 하고 Plan A 검증 결과 재현용으로만 남아있음 — 지금 웹 UI는 이 코드를 안 씀. 옛 분류 기반 `query_router.py`의 `classify_query`/`answer_query`도 `/api/rag/ask`가 더 이상 안 쓰지만 도구 실행부 일부를 재사용 중이라 아직 삭제 안 함.
- **자유 텍스트 주제 검색(Phase 2 신규)**: `posting_list`에서 `TRACKED_SKILLS` 밖 자유 텍스트 주제는 임베딩/FTS로 후보를 미리 거르지 않고 `judge_topic_postings()`가 공고 전체 원문을 LLM에 판정시킨다(`method="llm"` 기본값) — 이 corpus 규모(공고 수십~백 건)에서는 코사인 유사도·reranker 절대점수 모두 관련/무관을 못 가른다는 게 실측으로 확인돼(`docs/rag-project-plans/00_meta/HISTORY.md` 2026-07-28 항목), 점수 필터링 자체를 없애는 쪽을 택함. `method="local"`(벡터 검색만, LLM 호출 없음)은 비교·실험용으로 병존.
- **핵심 수치**: 검색 품질(Precision@5/Recall@10) — FTS5 0.75/0.41, Google 임베딩 0.68/0.33, 로컬 임베딩(Jina v5-text-small) 0.65/0.42. Gap 판정 정확도 10/10(정답지 기준).
- **한번 써보려면**: `RAG_POSTGRES_HOST` 등 opt-in 환경변수를 채운 뒤(`.env.example` 참고) `docker compose --profile rag up --build -d`로 `rag-postgres`까지 같이 띄우고, 메인 앱(`http://localhost:8000`)에 로그인해 navbar의 RAG 진입 버튼(`RAG_POSTGRES_HOST` 설정 시에만 노출)을 누른다. **새로 띄운 Postgres는 비어있으므로 `/rag` 뷰의 "🔄 재색인" 버튼을 먼저 눌러 완료를 기다려야 한다** — 이 단계 없이 질문하면 검색 대상이 없어 오류가 난다(Codex 리뷰로 발견, 2026-08-12). 재색인이 끝나면 기술/개념(또는 자연어 질문)을 입력할 수 있다. RAG가 꺼져 있으면(환경변수 미설정) 버튼 자체가 안 보이고 메인 앱의 나머지 기능은 지금과 동일하게 동작한다.

```
공고 원문·프로필 → 청킹 → 임베딩(Google/Local) ─┐
                                                ├→ pgvector 검색 → LLM 판정(근거 수준) → 행동 계획
                     정확 기술 매칭(SQL) ────────┘
```

---

## 1. 왜 이런 걸 만드나 (배경)

지금까지 Job FitCheck는 "이 공고에 Python이 있나요?" 같은 질문에 **글자가 똑같이 있는지**로만 답할 수 있었습니다. 근데 사람은 "백엔드 API 서버 만들어본 회사 찾아줘"처럼 정확한 단어를 안 쓰고 물어보는 경우가 많죠. 이럴 때 필요한 게 **의미 기반 검색**이고, 그걸 가능하게 하는 핵심 기술이 **임베딩(embedding)**입니다.

이걸로 하고 싶은 최종 목표:
- 지금 모은 공고들 중에서 **실제 시장이 어떤 기술을 요구하는지** 통계로 확인
- 본인 프로필과 대조해서 **어떤 기술은 이미 증명됐고, 어떤 건 근거가 부족한지(gap)** 판정
- gap을 어떻게 보완하면 좋을지 **행동 계획**까지 제시

RAG 개념 자체(청크·임베딩·코사인 유사도 등)가 낯설면 `docs/rag-project-plans/00_meta/concepts.md`(로컬 전용)에 이 프로젝트 맥락에서 풀어쓴 설명이 있습니다.

---

## 2. 지금까지 만든 것

### 전체 흐름

```
data/companies/*.raw.txt (공고 원문 70건) + data/candidate_profile.md (후보자 프로필)
        │
        ▼  정확한 기술명 집계 + 문단 단위 청킹 → Google/로컬 임베딩
   posting, posting_skill, document_chunk, chunk_embedding (PostgreSQL)
        │
        ▼  검색(pgvector exact/HNSW + 관계형 정확매칭 RRF 결합)
   evaluate.py 계열 — Precision@5/Recall@10 정식 평가
        │
        ▼  검색 근거 + 프로필을 놓고 "직접근거/부분근거/인접경험/근거없음" 판정
   gap.py — 시장 수요(SQL)·근거 검색(임베딩)·판정(LLM) 3단 분리
        │
        ▼  판정 결과를 근거 설명 + 구체적 행동 계획으로
   answer.py — 단일 기술 질문 + 여러 기술 종합(우선순위·강점·순서계획) 질문 둘 다 지원
        │
        ▼  실제로 눌러보면서 확인
   routers/rag.py + main SPA `/rag` 뷰 — POST /api/rag/gap-check, /ask, /reindex
```

### 파일별 설명

**실제 서비스 경로 — `backend/rag/postgres/`(PostgreSQL+pgvector)**

```
backend/rag/postgres/
├── schema.py          — 스키마(pgvector `vector_1536`/`vector_1024` + HNSW partial index,
│                        `document_chunk.text_tsv` tsvector generated column + GIN 인덱스)
├── db.py              — 연결 헬퍼(psycopg + pgvector 타입 등록)
├── ingest.py           — 공고 원문 → posting/posting_skill 적재
├── chunks.py           — 청킹 + `prune_deleted_postings()`(원문 삭제된 posting 정리)
├── pipeline.py         — 임베딩 파이프라인(provider 차원별 컬럼에 저장)
├── retrieval.py        — pgvector `<=>` 검색
├── fts.py              — Postgres 전문검색(`websearch_to_tsquery`)
├── hybrid.py           — RRF(관계형 정확매칭+벡터 검색 결합)
├── query_router.py     — `judge_topic_postings()`(자유 텍스트 주제 검색, 전체원문 LLM 판정) +
│                        옛 분류 기반 `classify_query`/`answer_query`(미사용, 삭제는 보류)
├── agent.py            — ★ Agentic RAG 핵심. `AGENT_SYSTEM`+`TOOL_DEFS`(도구 7개)+
│                        `answer_query_agent()` — LLM(메인 앱 provider 설정을 따름)이 질문마다
│                        도구를 스스로 선택·조합
├── reindex.py          — ★ 전체 재색인 진입점. `python3 -m rag.postgres.reindex --provider google`
├── evaluate.py / evaluate_hybrid.py / hnsw_eval.py / verify.py — 검색 품질·HNSW·집계 평가
├── gap.py              — Gap 판정
└── answer.py           — 답변·행동계획 생성

backend/routers/rag.py     — API(POST /api/rag/gap-check, /api/rag/ask, /api/rag/reindex)
frontend/index.html·app.js — main SPA `/rag` 뷰(기술 확인 + 자연어 채팅 + 재색인·provider 설정)
```

**Plan A 기준선 재현용(동결 스크립트) — `backend/rag/*.py`(SQLite)**

Postgres 포팅 이전 SQLite 버전. 삭제하지 않고 남겨둔 이유: Plan A 검증 결과(10/10, P@5/R@10 기준선)를 만들어낸 코드 자체이자 재현 스크립트라서 — `rag/postgres/evaluate.py`가 이 안의 평가 질문·지표 함수를 그대로 재사용하기도 함. `schema.py`/`ingest.py`/`chunking.py`/`chunks.py`/`retrieval.py`/`evaluate.py`/`gap.py`/`answer.py`/`skills.py`(정답지)/`embed/`(provider 구현체, Postgres판과 공유).

### 실행 방법

```bash
cd backend
python3 -m rag.postgres.reindex --provider google --include-profile   # 전체 재색인(스키마 생성+적재+임베딩)
python3 -m rag.postgres.reindex --provider local --include-profile    # 로컬(3050Ti)도 — SSH 연결 필요
python3 -m rag.postgres.verify        # 집계 검증(17개 항목)
python3 -m rag.postgres.gap           # Gap 판정 검증(정답지 10개 기준)
python3 -m rag.postgres.answer --aggregate   # 우선순위·강점·순서계획 집계 리포트
```

`reindex.py`는 **내용이 안 바뀐 공고는 건드리지 않습니다**(문서 해시 비교). 스키마 자체를 바꿨다면(`schema.py` 수정 후) `--rebuild-schema` 플래그로 테이블을 지우고 다시 만들어야 합니다(`CREATE TABLE IF NOT EXISTS`는 컬럼 추가를 안 해주기 때문). 브라우저로 확인하려면 위 "한눈에 보기"의 안내대로 메인 앱을 띄우고 `/rag`로 들어간다.

CLI 대신 웹에서 재색인하려면 `/rag` 페이지의 `🔄 재색인` 버튼 또는 `POST /api/rag/reindex`를
직접 호출한다 — **현재 활성 embedding provider 하나만** 재색인한다(google+local을 항상 같이
돌리던 옛 구조는 main 반영 과정에서 단일 provider로 재설계됨, 아래 3번 설계 결정 표 참고).
provider를 바꾸려면 설정 화면(`/api/rag/settings`)에서 전환 — 전환은 그 provider로 먼저
재색인이 성공해야만 실제로 반영된다.

---

## 3. 왜 이렇게 설계했나 (설계 결정 요약)

| 결정 | 이유 |
|---|---|
| `chunk_embedding`에 `provider`/`model`/`dimensions` 같이 저장, provider별 고정 차원 컬럼(`vector_1536`/`vector_1024`) 분리 | Google·로컬을 나란히 비교하는 게 목적이라 한 테이블에 다 넣되 안 섞이게 구분. pgvector HNSW 인덱스는 컬럼 차원이 고정돼야 걸 수 있어서 컬럼을 분리함(provider 2개뿐이라 테이블 분리보다 컬럼 분리가 diff 작음) |
| 개인 프로필은 Google **무료 티어**엔 안 보냄 | 무료 티어는 입력 데이터가 구글 제품 개선에 쓰일 수 있어서. 유료 티어(사용자가 명시적으로 키 전환)나 로컬 provider로는 프로필도 임베딩함 |
| `EmbeddingProvider` 공통 규격(클래스) | Google/로컬 API 호출 방식은 다르지만 그 위(청킹·저장·검색)는 provider 무관하게 동작하도록. **RAG 개발 당시(rag/main)엔 "여러 provider를 나란히 비교"가 목적이라 활성 provider 전환 로직을 일부러 안 만들었으나, main 반영 과정에서 뒤집힘** — 실사용에선 "비교"가 아니라 "메인 앱이 쓰는 provider 하나를 그대로 따라가는 것"이 맞다고 재판단해 `resolve_rag_embedding_provider()` + `/api/rag/settings`로 단일 활성 provider 전환 구조를 새로 만듦(`docs/rag-integration/STATUS.md` 4번 항목) |
| 로컬 임베딩 모델을 5개나 실제로 다 돌려봄 | 모델 카드 평판이나 "한국어엔 한국어 전용 모델"같은 직관을 안 믿고 매번 실측(P@5/R@10)으로 결정 — 최종 채택된 Jina는 원래 3번째 후보였고, 한국어 전용 모델(ko-sroberta)이 오히려 최저점 |
| 시장 수요를 13개 고정 기술은 정확 매칭, 자유 키워드는 임베딩+FTS+LLM 판정으로 다르게 계산 | 순수 임베딩 유사도로는 이 corpus(비슷한 직군 공고들)에서 "관련 있음/없음"을 못 가른다는 게 실측으로 확인됨(Redis 3건/Python 40건이 유사도 분포는 거의 같았음) — 후보를 넉넉히 모으고 LLM이 실제로 판정하는 방식으로 대체 |
| `skills.py`의 스킬 목록·근거 판정은 "정답지"이지 실제 서비스 로직이 아님 | 검색 정확도를 채점하려고 사람이 만든 기준값. 이 "동의어 범위" 데이터는 Gap 판정 LLM에게 참고 정보로 전달하는 용도로도 재활용됨 |
| Postgres로 옮기면서 SQLite 코드는 삭제하지 않고 그대로 둠 | "폐기"는 서비스가 그 경로를 안 부르게 하는 것(라우터 전환)이지 파일 삭제가 아님 |
| SQLite FTS5 대신 Postgres `tsvector` generated column을 씀 | FTS5는 청크가 바뀔 때마다 수동으로 재구축해야 했는데, generated column은 INSERT/UPDATE마다 DB가 알아서 갱신해줘서 그 로직 자체가 필요 없어짐 |

---

## 4. 현재 상태

Plan A(1~8단계)·Plan B(1~6단계) 전체 완료 + Codex 코드 리뷰 5차 재검토 반영까지 끝났습니다. "대화형 근거 기반 RAG" Phase 1~4(질문 이해와 라우팅~멀티턴 대화)를 거쳐, 애매한 질문에 무관한 답을 내던 구조적 결함이 실사용 중 발견돼 **Agentic RAG(tool-use)로 아키텍처 전환**했고(개발 당시엔 Claude 전용, main 반영 과정에서 Claude/OpenAI/Gemini 전부 지원으로 확장됨), Phase 5(평가, 21문항)까지 포함해 대화형 근거 기반 RAG 전체가 완료됐습니다(2026-07-29). 이어서 진행한 "RAG 모듈 안정화"도 Phase 1(모듈 경계)·Phase 2(운영 검증) 완료, Phase 3(Qdrant·reranker·GraphRAG·3090Ti+Triton)는 전부 조건부라 현재 트리거 없음(착수 안 함 = 정상)입니다. 상세 이력은 전부 `CHANGELOG.md`(이 폴더)에 버전별로 정리돼 있습니다 — 특히 `rag-v0.19.x`(재색인 웹 트리거), `rag-v0.18.x`(Agentic RAG 전환+provider 일관성 수정+병렬화+전수 검증), `rag-v0.17.0`(Phase 4, 멀티턴+채팅 UI), `rag-v0.16.x`(Phase 3, 근거·표본 범위 노출), `rag-v0.15.0`(Phase 2, 서술형 주제 검색 전체원문 LLM 판정), `rag-v0.14.0`(대화형 RAG Phase 1), `rag-v0.13.1`~`0.13.5`(Plan B Codex 재검토 5회차)를 보면 무슨 문제가 발견되고 어떻게 고쳤는지 다 나옵니다.

Phase 2(하이브리드 근거 검색)에서는 화이트닝(naive mean-centering·완전 SVD covariance whitening)과 cross-encoder reranker(bge-reranker-v2-m3)를 자연어 평가셋(5문항)으로 실측했으나 둘 다 이 corpus 규모·균질성에서는 순위 품질을 개선하지 못해 기각했고, corpus가 작다는 점에 착안해 후보를 점수로 미리 거르지 않고 전체 원문을 LLM 판정에 넘기는 쪽을 채택했습니다(위 "자유 텍스트 주제 검색" 참고). Phase 3(근거 기반 답변)는 답변에 판정 근거(어떤 발췌문을 보고 포함시켰는지)와 표본 범위(정확매칭인지 LLM 추정인지)를 노출하는 작업이었는데, 조사해보니 `single_skill_gap`은 이미 근거를 반환하고 있었고 `all_gaps`/`action_plan`은 **백엔드엔 근거가 있는데 프론트 렌더링에서만 빠뜨리고 있던** 순수 버그였습니다 — 실제 신규 구현은 `posting_list`/`market_aggregate`뿐이었습니다.

**재색인 웹 트리거(2026-07-29)**: `reindex.py`가 완전히 CLI 전용이라 실제 사용자는 트리거할 방법이 없던 문제를 발견·해결 — `POST /api/rag/reindex` + 웹 UI의 `🔄 재색인` 버튼(위 "실행 방법" 참고). 개발 당시엔 자동 트리거(공고 변경 API 훅)는 하지 않기로 확정했었으나, **main 반영 4번 항목(데이터 동기화)에서 이 판단이 뒤집혀** 회사 공고 생성·재분석·삭제 시 `trigger_reindex_background()`가 자동으로 재색인을 트리거하도록 구현됨(`routers/companies.py` CRUD 훅) — 수동 버튼은 여전히 남아있고, 진행 중이면 조용히 pending으로 미뤄서 자동/수동이 겹쳐도 안전하게 처리한다.

**아직 안 된 것**: RAG 안정화 Phase 3(조건부 실험, 트리거 없어 정상 미착수). corpus 균질성이 검색 변별력을 떨어뜨리는 근본 문제(화이트닝·reranker로도 해결 안 됨, `posting_list`는 점수 필터링을 없애는 우회책으로 대응했지만 `market_aggregate` 등 다른 경로는 여전히 남은 문제)와 `gap-check`/`ask`(전체 Gap·행동 계획·`posting_list` LLM 경로) 지연시간 최적화는 의도적으로 뒤로 미룸(아래 6번 참고).

---

## 5. 문서가 여러 개라 헷갈릴 때 (문서 지도)

**이 브랜치(main 반영, `feat/rag-integration-plan`) 기준 문서**:

| 문서 | 위치 | 용도 | git 추적 |
|---|---|---|---|
| `docs/rag-integration/STATUS.md` | 로컬 전용 | main 반영 작업 자체의 지금 상태·다음 할 일(가장 먼저 읽는 문서) | ❌ |
| `docs/rag-integration/HISTORY.md` | 로컬 전용 | main 반영 작업의 날짜별 진행 히스토리 | ❌ |
| `docs/rag-integration/KNOWN-ISSUES.md` | 로컬 전용 | 우리 코드 버그가 아닌 외부 API/SDK 이슈 기록 | ❌ |
| **이 README** | `backend/rag/` | 코드가 뭐고 왜 이렇게 짰는지, 처음 보는 사람 기준 | ✅ |
| `CHANGELOG.md` | `backend/rag/` | 버전별(`rag-v0.x.y`) 변경 이력(main 반영 전까지) | ✅ |

**`rag/main` 브랜치 전용(이 브랜치엔 없음) — RAG 자체 개발 당시 계획 문서**, 코드가 왜 이런
과정을 거쳐 지금 모습이 됐는지 궁금할 때만 그 브랜치에서 참고:

| 문서 | 용도 |
|---|---|
| `docs/rag-project-plans/00_meta/STATUS.md` | (개발 당시) 지금 상태·다음 할 일·원칙 |
| `docs/rag-project-plans/00_meta/HISTORY.md` | (개발 당시) 날짜별 진행 히스토리 |
| `docs/rag-project-plans/00_meta/concepts.md` | RAG 개념을 이 프로젝트 맥락으로 풀어쓴 설명 |
| `docs/rag-project-plans/plan-a/`, `plan-b/` | 완료된 단계별 설계·실행계획 |
| `docs/rag-project-plans/conversational-rag/`, `rag-stabilization/` | 개발 단계 설계 |

계획 문서(`docs/rag-project-plans/`)는 실제 지원 회사 데이터를 다루는 실험 성격이라 로컬
전용이고 `rag/main`에만 있습니다 — 레포를 새로 clone했다면(main 기준) 이 README와
`CHANGELOG.md`만으로도 코드를 이해할 수 있게 유지합니다.

---

## 6. 향후 방향 (요약, 상세는 `rag/main` 브랜치의 로컬 전용 `docs/rag-project-plans/00_meta/STATUS.md`)

- **대화형 근거 기반 RAG**: 자연어 질문을 지금의 단일 기술 판정 파이프라인에 그대로 넣으면 질문 의도를 못 알아듣는 문제에서 시작 — 질문 분류·라우팅(Phase 1)부터 근거 기반 답변(Phase 3), 멀티턴 대화(Phase 4), 평가(Phase 5, 21문항)까지 **전부 완료**. Phase 1의 "고정 분류" 구조 자체가 실사용 중 한계가 드러나 Agentic RAG(tool-use)로 전환됨(`conversational-rag/00_design.md`).
- **RAG 모듈 안정화**: 위 결과를 독립 모듈로 정리(Phase 1) + 운영 검증(Phase 2, 재색인 웹 트리거 포함) **완료**. Phase 3(필요할 때만 Qdrant·GraphRAG·3090Ti+Triton 조건부 실험, reranker는 이미 실측 기각)는 현재 트리거 조건 미충족이라 미착수(`rag-stabilization/00_design.md`).
- **corpus 주제 균질성 문제**: 비슷한 직군 공고들로만 이뤄진 corpus라 검색 변별력에 근본적 한계 — `posting_list`는 Phase 2에서 우회책(점수 필터링 없이 전체 LLM 판정)으로 대응했지만, `market_aggregate` 등 다른 경로의 벡터 채널 신뢰도 문제는 여전히 남아있어 전체 개발 종료 후 별도 R&D 대상.
- **`gap-check` 지연시간 최적화**, **3090Ti+Triton 멀티모델 서빙 실습**, **웹 검색 하이브리드**, **임베딩 비용 트래킹**: 전부 백로그, 아직 미착수.
