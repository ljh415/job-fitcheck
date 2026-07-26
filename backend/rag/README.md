# RAG 서브프로젝트 — `backend/rag/`

Job FitCheck가 이미 모아둔 채용공고 데이터와 본인 프로필을 가지고 **"검색으로 관련 정보를 찾아서, 그 근거를 바탕으로 LLM이 답하게 하는"** 시스템(RAG = Retrieval-Augmented Generation)을 직접 만들어보는 학습용 서브프로젝트입니다. 계획 자체(Plan A/B/C 전체 로드맵)는 `docs/rag-project-plans/`(git 미추적, 로컬 전용)에 있고, 이 문서는 **지금까지 실제로 만든 코드가 뭘 하는지**를 처음 보는 사람도 알 수 있게 설명하는 게 목적입니다.

## 한눈에 보기

- **지금 상태**: Plan A(검색 기준선, 8단계)·Plan B(PostgreSQL+pgvector 전환, 6단계) 전체 완료 + Codex 코드 리뷰 5차 재검토까지 반영됨. Plan C(Qdrant·reranker·GraphRAG 비교)는 설계만 있고 미착수.
- **실제 서비스 경로**: `POST /api/rag/gap-check` → `backend/rag/postgres/` (PostgreSQL+pgvector). SQLite 코드(`backend/rag/*.py`)는 삭제 안 하고 Plan A 검증 결과 재현용으로만 남아있음 — 지금 웹 UI는 이 코드를 안 씀.
- **핵심 수치**: 검색 품질(Precision@5/Recall@10) — FTS5 0.75/0.41, Google 임베딩 0.68/0.33, 로컬 임베딩(Jina v5-text-small) 0.65/0.42. Gap 판정 정확도 10/10(정답지 기준).
- **한번 써보려면**: (저자의 로컬 dev 환경 기준) `docker compose -f docker-compose.dev.yml up --build -d` 후 `http://localhost:8100/rag-test.html`에서 기술/개념(또는 자연어 질문)을 입력. `docker-compose.dev.yml`은 git 미추적 파일이라 새로 clone한 환경에는 없음 — `rag-postgres` 서비스 정의를 직접 추가해야 재현 가능(`schema.py`의 `SCHEMA_SQL` 참고).

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
   routers/rag.py + frontend/rag-test.html — POST /api/rag/gap-check
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
├── reindex.py          — ★ 전체 재색인 진입점. `python3 -m rag.postgres.reindex --provider google`
├── evaluate.py / evaluate_hybrid.py / hnsw_eval.py / verify.py — 검색 품질·HNSW·집계 평가
├── gap.py              — Gap 판정
└── answer.py           — 답변·행동계획 생성

backend/routers/rag.py     — API(POST /api/rag/gap-check, provider=google|local 선택)
frontend/rag-test.html     — 브라우저 테스트 화면
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

`reindex.py`는 **내용이 안 바뀐 공고는 건드리지 않습니다**(문서 해시 비교). 스키마 자체를 바꿨다면(`schema.py` 수정 후) `--rebuild-schema` 플래그로 테이블을 지우고 다시 만들어야 합니다(`CREATE TABLE IF NOT EXISTS`는 컬럼 추가를 안 해주기 때문). 브라우저로 확인하려면 (git 미추적 로컬 dev 설정 기준) `docker compose -f docker-compose.dev.yml up --build -d` 후 `http://localhost:8100/rag-test.html`.

---

## 3. 왜 이렇게 설계했나 (설계 결정 요약)

| 결정 | 이유 |
|---|---|
| `chunk_embedding`에 `provider`/`model`/`dimensions` 같이 저장, provider별 고정 차원 컬럼(`vector_1536`/`vector_1024`) 분리 | Google·로컬을 나란히 비교하는 게 목적이라 한 테이블에 다 넣되 안 섞이게 구분. pgvector HNSW 인덱스는 컬럼 차원이 고정돼야 걸 수 있어서 컬럼을 분리함(provider 2개뿐이라 테이블 분리보다 컬럼 분리가 diff 작음) |
| 개인 프로필은 Google **무료 티어**엔 안 보냄 | 무료 티어는 입력 데이터가 구글 제품 개선에 쓰일 수 있어서. 유료 티어(사용자가 명시적으로 키 전환)나 로컬 provider로는 프로필도 임베딩함 |
| `EmbeddingProvider` 공통 규격(클래스), 단 "하나만 골라 쓰는" 라우터는 없음 | Google/로컬 API 호출 방식은 다르지만 그 위(청킹·저장·검색)는 provider 무관하게 동작하도록. RAG는 "여러 provider를 나란히 비교"가 목적이라 활성 provider 전환 로직은 일부러 안 만듦 |
| 로컬 임베딩 모델을 5개나 실제로 다 돌려봄 | 모델 카드 평판이나 "한국어엔 한국어 전용 모델"같은 직관을 안 믿고 매번 실측(P@5/R@10)으로 결정 — 최종 채택된 Jina는 원래 3번째 후보였고, 한국어 전용 모델(ko-sroberta)이 오히려 최저점 |
| 시장 수요를 13개 고정 기술은 정확 매칭, 자유 키워드는 임베딩+FTS+LLM 판정으로 다르게 계산 | 순수 임베딩 유사도로는 이 corpus(비슷한 직군 공고들)에서 "관련 있음/없음"을 못 가른다는 게 실측으로 확인됨(Redis 3건/Python 40건이 유사도 분포는 거의 같았음) — 후보를 넉넉히 모으고 LLM이 실제로 판정하는 방식으로 대체 |
| `skills.py`의 스킬 목록·근거 판정은 "정답지"이지 실제 서비스 로직이 아님 | 검색 정확도를 채점하려고 사람이 만든 기준값. 이 "동의어 범위" 데이터는 Gap 판정 LLM에게 참고 정보로 전달하는 용도로도 재활용됨 |
| Postgres로 옮기면서 SQLite 코드는 삭제하지 않고 그대로 둠 | "폐기"는 서비스가 그 경로를 안 부르게 하는 것(라우터 전환)이지 파일 삭제가 아님 |
| SQLite FTS5 대신 Postgres `tsvector` generated column을 씀 | FTS5는 청크가 바뀔 때마다 수동으로 재구축해야 했는데, generated column은 INSERT/UPDATE마다 DB가 알아서 갱신해줘서 그 로직 자체가 필요 없어짐 |

---

## 4. 현재 상태

Plan A(1~8단계)·Plan B(1~6단계) 전체 완료 + Codex 코드 리뷰 5차 재검토 반영까지 끝났습니다. 상세 이력(단계별 결과, 발견·수정된 버그, 정정된 결론 등)은 전부 `CHANGELOG.md`(이 폴더)에 버전별로 정리돼 있습니다 — 특히 `rag-v0.13.1`~`0.13.5`(Plan B Codex 재검토 5회차, Stage 4 HNSW 결론 정정 포함)를 보면 무슨 문제가 발견되고 어떻게 고쳤는지 다 나옵니다.

**아직 안 된 것**: Plan C(Qdrant·reranker·GraphRAG 비교) 미착수. corpus 균질성이 검색 변별력을 떨어뜨리는 근본 문제와 `gap-check` 지연시간 최적화는 의도적으로 뒤로 미룸(아래 6번 참고).

---

## 5. 문서가 여러 개라 헷갈릴 때 (문서 지도)

| 문서 | 위치 | 용도 | git 추적 |
|---|---|---|---|
| `docs/rag-project-plans/00_meta/STATUS.md` | 로컬 전용 | 지금 상태·다음 할 일·원칙(가장 먼저 읽는 문서) | ❌ |
| `docs/rag-project-plans/00_meta/HISTORY.md` | 로컬 전용 | 날짜별 진행 히스토리(궁금할 때만) | ❌ |
| `docs/rag-project-plans/00_meta/concepts.md` | 로컬 전용 | RAG 개념을 이 프로젝트 맥락으로 풀어쓴 설명 | ❌ |
| `docs/rag-project-plans/plan-a/`~`plan-d/` | 로컬 전용 | Plan별 설계·단계별 실행계획 | ❌ |
| **이 README** | `backend/rag/` | 코드가 뭐고 왜 이렇게 짰는지, 처음 보는 사람 기준 | ✅ |
| `CHANGELOG.md` | `backend/rag/` | 버전별(`rag-v0.x.y`) 변경 이력 | ✅ |

계획 문서(`docs/rag-project-plans/`)는 실제 지원 회사 데이터를 다루는 실험 성격이라 로컬 전용입니다 — 레포를 새로 clone했다면 이 README와 `CHANGELOG.md`만으로도 코드를 이해할 수 있게 유지합니다.

---

## 6. 향후 방향 (요약, 상세는 로컬 전용 `STATUS.md`)

- **Plan D — 대화형 라우팅 RAG**: 자연어 질문을 지금의 단일 기술 판정 파이프라인에 그대로 넣으면 질문 의도를 못 알아듣는 문제 발견 — 질문 분류/라우팅 레이어 필요. 논의 중, 설계 문서 아직 없음.
- **Plan C — Qdrant·reranker·GraphRAG 비교**: 설계만 있고 미착수, Plan D 이후 재논의.
- **corpus 주제 균질성 문제**: 비슷한 직군 공고들로만 이뤄진 corpus라 검색 변별력에 근본적 한계 — 전체 개발 종료 후 별도 R&D 대상.
- **`gap-check` 지연시간 최적화**, **3090Ti+Triton 멀티모델 서빙 실습**, **웹 검색 하이브리드**, **임베딩 비용 트래킹**: 전부 백로그, 아직 미착수.
