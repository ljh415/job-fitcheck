# RAG 서브프로젝트 — `backend/rag/`

Job FitCheck가 이미 모아둔 채용공고 데이터와 본인 프로필을 가지고 **"검색으로 관련 정보를 찾아서, 그 근거를 바탕으로 LLM이 답하게 하는"** 시스템(RAG = Retrieval-Augmented Generation)을 직접 만들어보는 학습용 서브프로젝트입니다. 계획 자체(Plan A/B/C 전체 로드맵)는 `docs/rag-project-plans/`(git 미추적, 로컬 전용)에 있고, 이 문서는 **지금까지 실제로 만든 코드가 뭘 하는지**를 처음 보는 사람도 알 수 있게 설명하는 게 목적입니다.

---

## 1. 왜 이런 걸 만드나 (배경)

지금까지 Job FitCheck는 "이 공고에 Python이 있나요?" 같은 질문에 **글자가 똑같이 있는지**로만 답할 수 있었습니다. 근데 사람은 "백엔드 API 서버 만들어본 회사 찾아줘"처럼 정확한 단어를 안 쓰고 물어보는 경우가 많죠. 이럴 때 필요한 게 **의미 기반 검색**이고, 그걸 가능하게 하는 핵심 기술이 **임베딩(embedding)**입니다.

**임베딩이란**: 문장을 숫자 목록(벡터)으로 바꾸는 것. 비유하면 모든 문장을 지도 위의 GPS 좌표로 찍는 겁니다 — 의미가 비슷한 문장은 좌표가 가깝게, 의미가 다른 문장은 멀리 찍힙니다. "FastAPI로 API 서버 개발"과 "백엔드 API 개발 경험"은 겹치는 단어가 하나도 없어도, 임베딩 모델이 학습을 잘 해놨다면 좌표가 가깝게 찍힙니다.

이걸로 하고 싶은 최종 목표(Plan A):
- 지금 모은 공고들 중에서 **실제 시장이 어떤 기술을 요구하는지** 통계로 확인
- 본인 프로필과 대조해서 **어떤 기술은 이미 증명됐고, 어떤 건 근거가 부족한지(gap)** 판정
- gap을 어떻게 보완하면 좋을지 **행동 계획**까지 제시

지금 단계(Plan A)는 이 전체 시스템을 만들기 전에, **검색 자체가 잘 되는지 최소한의 형태로 검증**하는 단계입니다. 그래서 Docker, 별도 서버 이런 거 없이 Python 스크립트 + SQLite 파일 하나로 되어 있습니다.

---

## 2. 지금까지 만든 것 (Plan A 1~8단계 + Plan B 1~4·6단계 완료)

### 전체 흐름

```
data/companies/*.raw.txt (공고 원문 70건) + data/candidate_profile.md (후보자 프로필)
        │
        ▼  [2단계] 정확한 기술명이 몇 건씩 있는지 집계
   posting, posting_skill, candidate_evidence 테이블 (data/rag.db)
        │
        ▼  [3~4단계] 문단 단위로 자르고(청킹) → Google/로컬 두 provider로 벡터화(임베딩)
   document_chunk, chunk_embedding 테이블 (같은 data/rag.db)
        │
        ▼  [5단계] 질문을 벡터로 바꿔서 가장 비슷한 청크 찾기 = 검색 (FTS5·Google·Local 비교)
   evaluate.py — Precision@5/Recall@10 정식 평가
        │
        ▼  [6단계] 검색 근거 + 프로필을 놓고 "직접근거/부분근거/인접경험/근거없음" 판정
   gap.py — 시장 수요(SQL)·근거 검색(임베딩)·판정(LLM) 3단 분리
        │
        ▼  [7단계] 판정 결과를 근거 설명 + 구체적 행동 계획으로
   answer.py — GP-01/GP-06/AC-06 스타일 집계 질문까지 지원
        │
        ▼  [UI] 실제로 눌러보면서 확인
   routers/rag.py + frontend/rag-test.html — POST /api/rag/gap-check
```

`data/rag.db`는 SQLite 파일 하나입니다. 텍스트 파일(`.raw.txt`)이 "원본 진실"이고, 이 db 파일은 언제든 원본에서 다시 만들어낼 수 있는 "캐시/색인"이라고 생각하면 됩니다.

### 파일별 설명

```
backend/rag/
├── schema.py            — db에 어떤 표(테이블)들이 있는지 정의
├── skills.py             — "지금 이 데이터셋"에서 추적할 기술 목록 + 정답지 (아래 4번 설명 참고)
├── ingest.py             — 공고 70개를 읽어서 posting/posting_skill 테이블에 채워넣는 스크립트
├── verify_step2.py       — 2단계가 제대로 됐는지 자동으로 채점하는 스크립트
├── chunking.py           — 긴 텍스트를 작은 조각(청크)으로 자르는 규칙
├── chunks.py             — 공고·프로필을 청킹해서 document_chunk 테이블에 채워넣는 스크립트(청크가
│                           바뀔 때만 FTS5 검색 인덱스도 같이 최신화)
├── retrieval.py          — 청크 단위 벡터 검색 + FTS5 검색 공용 유틸(여러 파일이 같이 씀)
├── run_embedding.py      — ★ 임베딩 실행 진입점. `python3 -m rag.run_embedding --provider google`
├── evaluate.py           — 5단계: FTS5/Google/Local 검색 품질 비교 평가
├── gap.py                — 6단계: Gap 판정 + 시장수요 계산(고정 기술은 정확 매칭, 자유 키워드는 하이브리드 추정)
├── answer.py             — 7단계: 근거 설명 + 행동 계획 생성, 여러 기술 종합 질문(GP-01/06/AC-06) 지원
├── CHANGELOG.md          — rag/main 전용 버전 이력(rag-v0.x.0, main의 CHANGELOG.md와 별개)
└── embed/
    ├── base.py             — "임베딩 provider는 이런 기능이 있어야 한다"는 공통 규격
    ├── google.py            — Google(gemini-embedding-2)로 구현한 것
    ├── local.py             — 3050Ti에 SSH 터널로 접속하는 로컬 provider
    ├── inference_server.py  — 3050Ti에서 실제로 도는 FastAPI 추론 서버
    └── pipeline.py          — provider가 뭐든 상관없이 "아직 임베딩 안 된 것만 찾아서 처리"하는 공통 로직

backend/routers/rag.py     — RAG 테스트 UI용 API(POST /api/rag/gap-check, provider 선택 가능)
frontend/rag-test.html     — 브라우저에서 직접 눌러볼 수 있는 테스트 화면
```

**Plan B(PostgreSQL+pgvector) — 위와 별도 저장소로 `backend/rag/postgres/`에 미러링**

실제 서비스 흐름(`routers/rag.py`)은 이제 이 아래 코드를 쓴다. 위 SQLite 코드(`rag/gap.py` 등)는
지우지 않고 Plan A 기준선 재현용 "동결 스크립트"로 남아있다(`rag/postgres/evaluate.py`가 그 안의
평가 질문·지표 함수를 그대로 재사용하기도 함).

```
backend/rag/postgres/
├── schema.py         — Postgres 방언 스키마(pgvector `vector_1536`/`vector_1024` 컬럼 + HNSW
│                       partial index, `document_chunk.text_tsv`(tsvector generated column) + GIN 인덱스)
├── db.py             — 연결 헬퍼(psycopg + pgvector 타입 등록)
├── ingest.py          — SQLite `ingest.py` 포팅
├── chunks.py          — SQLite `chunks.py` 포팅(FTS5 rebuild 호출 없음 — generated column이 자동 갱신)
├── pipeline.py        — SQLite `embed/pipeline.py` 포팅(provider 차원별 컬럼에 저장)
├── retrieval.py       — pgvector `<=>` 연산자로 SQL 안에서 정렬까지 끝냄(BLOB pack/unpack 불필요)
├── fts.py             — Postgres 전문검색(`websearch_to_tsquery`) — SQLite `fts5_literal()`류 이스케이프 불필요
├── hybrid.py          — RRF(관계형 정확매칭+벡터 검색 결합)
├── reindex.py         — ★ 전체 재색인 진입점. `python3 -m rag.postgres.reindex --provider google`
├── evaluate.py         — 5단계 대응: pgvector exact search 품질 평가(Google/Local만, FTS 비교는 hnsw_eval 쪽)
├── evaluate_hybrid.py — 벡터 단독 vs RRF 하이브리드 비교(순환논리 주의 — 파일 docstring 참고)
├── hnsw_eval.py        — exact vs HNSW 근사검색 recall·지연시간 비교
├── verify.py           — 2단계 집계 검증(SQLite `verify_step2.py`와 기대값 공유)
├── gap.py              — 6단계 Gap 판정(SQLite `gap.py` 포팅, 순수 함수는 원본에서 import)
└── answer.py           — 6단계 답변 생성(SQLite `answer.py` 포팅, 순수 함수는 원본에서 import)
```

### 지금 실행하면 어떻게 되는지

```bash
cd backend
python3 -m rag.ingest              # 2단계: 공고 데이터 정리 (한 번만 하면 됨)
python3 -m rag.verify_step2        # 2단계가 맞게 됐는지 채점 (17개 항목 전부 통과해야 정상)
python3 -m rag.run_embedding --provider google   # Google로 임베딩
python3 -m rag.run_embedding --provider local    # 로컬(3050Ti)로 임베딩 — SSH 연결 필요
python3 -m rag.evaluate             # 5단계: 검색 품질 정식 평가
python3 -m rag.gap                  # 6단계: Gap 판정 검증(정답지 10개 기준)
python3 -m rag.answer               # 7단계: 실제 gap(GCP/CI-CD/IaC)에 대한 리포트 생성
python3 -m rag.answer --aggregate   # 7단계: 여러 기술 종합 질문(우선순위·강점·순서계획) 생성
```

`run_embedding`은 **내용이 안 바뀐 공고는 건드리지 않습니다.** 처음 실행하면 청크를 전부 API에 보내 벡터로 바꾸고, 그다음부터는 새로 추가되거나 수정된 공고만 다시 처리합니다(안 그러면 실행할 때마다 비용/시간이 계속 낭비되기 때문). 브라우저로 직접 확인하고 싶으면 `docker compose -f docker-compose.dev.yml up --build -d` 후 `http://localhost:8100/rag-test.html`(dev 기준)에서 기술/개념을 입력하고 provider를 골라보면 됩니다.

---

## 3. 왜 이렇게 설계했나 (설계 결정 요약)

| 결정 | 이유 |
|---|---|
| `chunk_embedding` 테이블에 `provider`/`model`/`dimensions`를 같이 저장 | Google·로컬·OpenAI를 다 비교해볼 거라서, 한 테이블에 다 넣되 서로 섞이지 않게 구분해둠 |
| 개인 프로필은 Google **무료 티어**엔 안 보냄 | 무료 티어는 입력 데이터가 구글 제품 개선에 쓰일 수 있어서. 유료 티어(사용자가 명시적으로 키 전환)나 로컬 provider로는 프로필도 임베딩함 |
| `EmbeddingProvider`라는 공통 규격(클래스)을 만듦 | Google/로컬/OpenAI 각각 API 호출 방식이 다르지만, 그 위(청킹, 저장, 검색)는 어떤 provider든 똑같이 동작하게 하려고. `llm/` 폴더에 이미 있는 패턴(Claude/OpenAI/Gemini 채팅 provider들)과 같은 방식 — 단, "하나만 골라 쓰는" 라우터는 없음(RAG는 여러 provider를 나란히 비교하는 게 목적이라 일부러 안 만듦) |
| 로컬 임베딩 모델을 5개나 실제로 다 돌려봄(`Alibaba-NLP/gte-multilingual-base` → `intfloat/multilingual-e5-base` → `BAAI/bge-m3` → `jinaai/jina-embeddings-v5-text-small` → `jhgan/ko-sroberta-multitask`) | 모델 카드 평판이나 "한국어니까 한국어 전용 모델이 유리하겠지"같은 직관을 그대로 믿지 않고, 매번 실제 검색 성능(Precision@5/Recall@10)을 측정해서 골랐음. 결과적으로 최종 채택된 Jina는 원래 3번째 후보였고, 한국어 전용 모델(ko-sroberta)은 오히려 가장 낮은 점수가 나옴 — "직관이 항상 맞진 않는다"를 직접 확인한 사례 |
| 시장 수요(이 기술을 몇 %의 공고가 요구하는지)를 13개 고정 기술은 정확 매칭, 그 외 자유 키워드는 임베딩+FTS5+LLM 판정으로 다르게 계산 | 순수 임베딩 유사도만으로는 "이 corpus에서 관련 있음/없음"을 구분 못 한다는 게 실측으로 확인됨(전혀 다른 빈도의 기술인 Redis(3건)와 Python(40건)이 유사도 분포는 거의 같았음 — 비슷한 직군 공고들끼리 도메인 어휘가 겹쳐서 생기는 현상). 그래서 후보를 넉넉히 모으고 LLM이 실제 내용을 읽고 판정하는 방식으로 대체 |
| `skills.py`의 스킬 목록·근거 판정은 "정답지"이지 실제 서비스 로직이 아님 | 지금은 검색이 잘 되는지 채점하려고 사람이 직접 확인해서 만든 기준값. 다만 이 "동의어 범위" 데이터는 6단계 Gap 판정 LLM에게 참고 정보로 전달하는 용도로도 재활용됨(아래 5번 참고) |
| Postgres로 옮기면서 SQLite 코드는 삭제하지 않고 그대로 둠(`rag/gap.py` 등) | Plan A 검증 결과(10/10, P@5/R@10 기준선)를 만들어낸 코드 자체이자 재현 스크립트라서 — "폐기"는 서비스가 그 경로를 안 부르게 하는 것(`routers/rag.py` 전환)이지 파일 삭제가 아님 |
| `chunk_embedding`에 provider별 고정 차원 컬럼(`vector_1536`/`vector_1024`)을 따로 둠 | pgvector의 HNSW 인덱스는 컬럼 차원이 고정돼야 걸 수 있는데, Google(1536차원)과 로컬(1024차원)이 원래는 한 컬럼에 섞여 있었음. provider가 2개뿐이라 테이블을 나누기보다 컬럼을 나누는 쪽이 diff가 작아서 이렇게 함 |
| SQLite FTS5 대신 Postgres `tsvector` generated column을 씀 | FTS5는 가상 테이블을 만들고 청크가 바뀔 때마다 수동으로 `rebuild_fts5()`를 불러야 했는데, `GENERATED ALWAYS AS ... STORED` 컬럼은 INSERT/UPDATE마다 DB가 알아서 갱신해줘서 그 수동 재구축 로직 자체가 필요 없어짐 |

---

## 4. 알아두면 좋은 개념 설명

- **청크(chunk)**: 공고 원문 하나를 통째로 벡터화하면 여러 주제(회사소개+자격요건+복지 등)가 섞여서 검색이 부정확해집니다. 그래서 문단 단위로 잘라서(최대 1,200자) 여러 개의 작은 조각으로 만들고, 각 조각마다 따로 벡터를 만듭니다.
- **벡터/차원**: 임베딩 결과는 숫자 목록입니다("차원 N개") — Google은 1536, 로컬(Jina)은 1024. 숫자가 많을수록 더 정밀하지만 저장 공간과 계산량이 늘어납니다.
- **코사인 유사도**: 벡터 두 개가 "같은 방향"을 가리키는 정도(0~1, 1이 완전히 같은 방향). 이걸로 "질문과 가장 비슷한 공고"를 찾습니다.
- **문서용/질의용 구분(비대칭 인코딩)**: 임베딩 모델은 "이건 저장할 문서야"와 "이건 검색 질문이야"를 다르게 인코딩하는 경우가 많습니다. Google은 텍스트 앞에 `title:`/`task:` 같은 prefix를 붙이는 방식, 로컬(Jina)은 `encode(..., task="retrieval", prompt_name="document"/"query")`처럼 API 파라미터로 구분하는 방식 — **모델마다 방식이 완전히 다릅니다.** `embed/inference_server.py`에 모델 계열별 분기(`_family_for()`)가 있는 이유가 이겁니다.
- **provider**: 임베딩을 만들어주는 주체(Google API, OpenAI API, 내 컴퓨터에서 돌리는 오픈소스 모델). 서로 다른 provider가 만든 벡터는 "다른 지도" 위의 좌표라서 섞어서 비교하면 안 됩니다.
- **키워드 검색(FTS5) vs 임베딩 검색, 그리고 하이브리드**: FTS5는 정확한 단어가 원문에 있어야만 찾고, 동의어(K8s vs Kubernetes)는 놓칩니다. 임베딩은 의미가 비슷하면 찾지만, 이 프로젝트의 corpus(비슷한 직군 공고들)에서는 "얼마나 자주 언급되는지"를 구분하는 용도로는 약하다는 게 실측으로 드러났습니다(5번 참고). 그래서 "후보를 넓게 모으는 건 두 방식을 같이 쓰고, 최종 판단은 LLM이 실제 내용을 읽고 한다"는 하이브리드 구조를 쓰게 됐습니다.
- **Gap 판정에서 "기능적 동일성" 원칙**: 어떤 기술의 근거가 있는지 판정할 때, "정확히 그 단어가 등장하는가"가 아니라 "발췌문의 경험이 대상 개념과 실제로 같은 기능을 수행하는가"를 봅니다(예: Ansible은 "인프라 자동화"라는 큰 틀에서는 같지만 "IaC 프로비저닝"이라는 좁은 의미에서는 다른 기능이라 인접 경험으로 판정). 이 경계를 프롬프트에 사례로 못박는 대신, `skills.py`에 이미 정의된 동의어 범위 데이터를 판정 시점에 참고 정보로 전달하는 방식으로 풀었습니다 — 하드코딩 없이 일반화된 규칙을 유지하면서도 실제로는 정확한 경계를 전달할 수 있는 방법.

---

## 5. 지금까지 검증한 것 / 아직 안 된 것

**Plan A(1~8단계) 전부 완료**

- 2단계: 공고 70건에서 실제로 특정 기술이 몇 건씩 있는지 grep으로 직접 세어본 값과 코드 결과가 정확히 일치(17개 항목)
- 3~4단계: Google(`gemini-embedding-2`)과 로컬(`jinaai/jina-embeddings-v5-text-small`, 3050Ti GPU) 두 provider로 공고+프로필 임베딩 완료. 로컬 모델은 5개를 실제로 비교(2번 후보였던 `multilingual-e5-base`, `BAAI/bge-m3`, 한국어 전용 `ko-sroberta-multitask`까지 다 시도)해서 최종 선정
- 5단계: 36개 평가 질문 중 12개(정확 기술명+동의어)로 FTS5/Google/Local 정식 비교 — FTS5가 정확 기술명은 최강(P@5 0.75), 동의어는 원문과 표기가 다르면 0까지 하락. Local(Jina)이 Recall@10(0.42)은 FTS5·Google도 능가
- 6단계: Gap 판정을 정답지(`CANDIDATE_EVIDENCE` 10개 기술)로 검증 — **10/10 전부 일치**. 여러 기술을 종합하는 질문(우선순위 gap 랭킹, 전체 강점 요약, 순서 계획)도 지원
- 7단계: 실제 gap(GCP, CI/CD, IaC)에 대해 막연하지 않은 구체적 행동 계획(활동+증거+완료조건) 생성 확인
- 8단계: 위 전체를 브라우저에서 눌러볼 수 있는 테스트 화면(`/rag-test.html`) 완성, 시장 수요를 자유 키워드에도 답할 수 있도록 하이브리드(임베딩+FTS5+LLM) 확장
- 코드 리뷰 2회차(Codex)로 실제 버그 9건 발견·수정 — FTS5 색인이 오래돼 크래시 나던 문제, 동시 요청 시 DB 락, 자유 입력에 특수문자가 있으면 크래시, 프로필 데이터가 검색 결과를 오염시키던 문제 등(자세한 원인·수정 내역은 `docs/rag-project-plans/00_claude_handoff.md` 참고)

**Plan B(PostgreSQL+pgvector) 1~4·6단계 완료**

- 1단계(승계 확인): Plan A 평가 수치·모델 선택을 그대로 기준선으로 사용
- 2단계(저장소 구축): `rag/postgres/`에 별도 저장소 구축 — 재색인 검증 결과가 SQLite와 전부 일치
- 3단계(exact search+집계): pgvector exact search 결과가 SQLite 기준선과 소수점 둘째 자리까지 일치(포팅 정확성 확인)
- 4단계(HNSW+RRF): `vector_1536`/`vector_1024` 컬럼 분리 후 HNSW partial index. 이 corpus(청크 194개)는 너무 작아 exact/HNSW recall이 동일(지연시간만 소폭 개선). RRF 하이브리드는 로직 동작 확인(단, 평가 자체는 순환논리라 배관 점검 수준 — `evaluate_hybrid.py` docstring 참고)
- 6단계(Career Gap 답변+UI 전환): 실제 서비스 흐름(`routers/rag.py`)이 Postgres로 완전 전환. `CANDIDATE_EVIDENCE` 10/10, GP-01/GP-06/AC-06 집계 일치, 실제 API 호출로 end-to-end 확인

**아직 안 된 것**

- Plan B 5단계 — 증분 색인·운영(백업/복구)
- 36개 평가 질문 중 나머지(집계·개인gap·행동계획·답변불가)의 확장 검증
- `gap-check` 요청 자체의 지연시간 프로파일링, corpus 주제 균질성이 검색 변별력을 떨어뜨리는 근본 문제 — 둘 다 전체 개발 종료 후 별도 R&D로 지정(`docs/rag-project-plans/00_claude_handoff.md` "향후 탐색 아이디어" 참고)

---

## 6. 문서가 여러 개라 헷갈릴 때 (문서 지도)

RAG 관련 문서가 두 군데로 나뉘어 있습니다 — 역할이 다르니 이렇게 구분하면 됩니다:

| 문서 | 위치 | 용도 | git 추적 |
|---|---|---|---|
| `00_claude_handoff.md` ~ `03_...md` | `docs/rag-project-plans/` | 전체 로드맵(Plan A/B/C), 세부 결정 근거, Codex와 주고받는 논의 로그 | ❌ (로컬 전용, 실험 데이터 다뤄서) |
| **이 README** | `backend/rag/` | 코드 옆에 붙어있는 설명서 — "이게 뭐고 왜 이렇게 짰나"를 처음 보는 사람 기준으로 정리, 브레인스토밍 메모장 | ✅ (코드랑 같이 커밋됨) |

헷갈리면 일단 이 README부터 보고, 더 자세한 배경이 필요하면 `docs/rag-project-plans/00_claude_handoff.md`로 넘어가면 됩니다. 이 README는 코드가 바뀔 때마다 계속 업데이트할 예정입니다.

---

## 7. 향후 개선 아이디어 (브레인스토밍, 계속 추가 예정)

아직 어느 단계에도 확정 편입 안 된 아이디어들입니다. 여기 계속 추가하면서 생각을 정리하면 됩니다.

- **3090Ti에서 Triton으로 멀티모델 서빙 실습**: 지금은 3050Ti/3060에서 FastAPI로 모델 하나씩만 서빙해서 "이 모델이 검색을 잘하는지"만 확인하는 단계. 나중에 괜찮은 모델이 정해지면, VRAM이 24GB라 여러 모델을 동시에 올릴 수 있는 3090Ti에서 Triton Inference Server 같은 정식 서빙 프레임워크를 실습 삼아 써보고 싶다는 아이디어. "모델 성능 확인"과 "서빙 인프라 다루는 법 배우기"를 일부러 분리해서, 뭐가 문제인지 헷갈리지 않게 순서를 잡음.
- **웹 검색을 섞은 하이브리드 RAG**: 지금은 수집한 공고 70건 안에서만 검색하다 보니 "한국 전체 시장에서 이 기술 점유율은?" 같은 질문엔 "판단 불가"로만 답합니다. 실시간 웹 검색을 더하면 이런 질문에도 답할 여지가 생기는데, 문제는 웹 검색 결과가 오늘·내일 다를 수 있고(재현성 없음) 출처 신뢰도도 검증이 안 된다는 점입니다. 지금 시스템이 애써 지켜온 "출처 등급 구분"과 "근거 없는 답 안 하기" 원칙과 부딪히는 지점이라, 하려면 "웹 검색 결과 — 신뢰도 불확실" 같은 새 출처 등급을 먼저 설계해야 함.
- *(여기에 계속 추가)*
