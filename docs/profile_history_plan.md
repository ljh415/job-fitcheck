# 프로필 스냅샷 & 적합도 평가 히스토리 — 계획 문서

> **작성**: 2026-08-15, RAG merge 완료 후 대화로 확정
> **상태**: 핵심 설계 확정 중. 구현은 아직 착수 안 함 — RAG 반영 이후 새 feature 브랜치(dev worktree)에서 시작 예정(`docs/TODO.md` Phase 11 참고).

## 배경 / 목적

기존 "후보자 프로필 버전 관리" 아이디어(2026-08-11)를 구체화하는 과정에서, 진짜 목적이 단순히
"옛 프로필을 보관"하는 게 아니라 **"프로필이 바뀌면서(예: 사이드 프로젝트 추가) 같은 회사 기준
적합도 점수가 실제로 얼마나 올랐는지 추적"**하는 것으로 확인됨. 그래서 두 가지가 세트로
필요하다:

1. **프로필 스냅샷** — 그 평가가 어떤 프로필 내용을 기준으로 나온 건지 실제로 열람 가능해야 함
2. **회사별 적합도 평가 히스토리** — refit(재평가)할 때마다 이전 평가 결과를 덮어쓰지 않고
   이력으로 쌓고, 각 이력이 어느 프로필 스냅샷 기준이었는지 연결

이 둘은 분리된 기능이 아니라 하나의 기능이다 — 프로필 스냅샷이 재료, 평가 히스토리가 그
재료를 참조하는 구조.

## 확정된 사항

### 트리거
- PDF 재업로드(`POST /api/profile/upload`)와 수동 편집(`PUT /api/profile`) **둘 다** 프로필
  스냅샷을 생성한다 — 편집도 편집이므로 구분 없이 전부 스냅샷 대상.

### 프로필 스냅샷 저장
- 별도 구조화 없이 **그 시점 프로필 원문(frontmatter+body) 전체를 통으로 저장**.
- 개수 제한 없음.
- 개별 삭제 가능(RAG 채팅 세션 삭제와 같은 패턴).

### 저장소 — SQLite
- Postgres(RAG)는 opt-in 선택 기능이라 핵심 기능(프로필 버전 관리)이 거기 의존하면 RAG 안
  쓰는 사용자는 이 기능도 못 쓰게 됨 — 그래서 RAG의 Postgres는 안 쓴다.
- 대신 SQLite를 쓴다 — 별도 서버 설치가 필요 없고 Python 표준 라이브러리(`sqlite3`)에 이미
  있어서 "선택 기능에 종속" 문제 자체가 없음. 단일 파일(`data/app.db`)로 관리,
  `data/` 전체가 이미 git 미추적이라 자동으로 추적 제외됨.
- 프로필 스냅샷 파일을 별도 `.md` 파일로 안 두고, **SQLite 안에 원문 텍스트를 그대로 저장**
  (매번 파일 열고 파싱하는 오버헤드 없이 쿼리 한 번으로 목록·상세 조회).
- 세부 테이블 스키마는 아직 미정(다음 논의 대상).

### 회사별 적합도 평가 히스토리(`fit_history`)
- refit/재분석으로 새 평가 결과가 나올 때마다(**최초 평가 포함**) 그 결과를 히스토리에
  추가한다 — "덮어쓰기 전에 이전 걸 이력으로 미는" 방식이 아니라, **평가가 나올 때마다
  그 결과 자체를 이력에 추가**하고 "현재 값"은 이력의 최신 항목으로 취급.
- 프로필이 안 바뀐 상태로 refit해도 새 이력으로 추가한다 — "진짜 중복인지" 판단 로직은
  안 만듦(LLM 특성상 완전 동일 판정이 애매하고, refit 자체가 하나의 이벤트라 기록할 가치가
  있음).
- 각 이력 항목은 그 평가가 어느 프로필 스냅샷을 기준으로 했는지 연결(참조)한다.

### 기존 90개 회사 소급 적용
- 일괄 refit은 안 함(LLM 90회 호출 비용만 들고 진짜 과거 기록도 아님).
- 지금 이미 저장돼 있는 기존 `fit_score`/`fit_label`을 히스토리의 **첫 항목**으로 그대로
  사용, 연결된 프로필 스냅샷은 "이전 버전 불명"으로 표시.
- 필요하면 사용자가 원할 때 직접 refit해서 새 이력을 쌓으면 됨.

### 스냅샷 삭제 정책
- 참조 중(어떤 평가 히스토리가 그 스냅샷을 가리키고 있음)이어도 **삭제 허용**.
- 삭제된 스냅샷을 참조하던 이력 항목은 점수·라벨은 그대로 남고, 원문 열람만 불가능해짐.

### UI (목업으로 확정, 2026-08-15)
- **설정 화면** "📤 프로필 업데이트" 아코디언 안, 현재 프로필 표시 아래에 "이전 버전(N개)"
  섹션 신설 — 타임스탬프 + 한 줄 요약 + 항목별 "보기"/🗑 삭제 버튼.
- **회사 상세 페이지** 적합도 배지 옆에 "📋 평가 이력 보기 (N건)" 토글 → 펼치면 표
  (시점 | 프로필 버전 | 점수 | 라벨), 최신순. 점수 옆에 이전 이력 대비 증감(예: `72 +10`)
  표시. 프로필 버전 칸은 클릭 가능한 링크(스냅샷 삭제됐으면 "삭제됨", 소급 적용된 최초
  이력은 "이전 버전 불명"으로 표시, 클릭 불가).
- 목업: 대화 중 확인 완료(claude.ai 아티팩트로 렌더링, 실제 앱 색상 토큰 그대로 사용).

### SQLite 스키마 (2026-08-15 초안)

```sql
CREATE TABLE profile_versions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,   -- ISO8601
    content    TEXT NOT NULL    -- candidate_profile.md 전체(frontmatter+body), 통으로 저장
);

CREATE TABLE fit_history (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    company_slug       TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    profile_version_id INTEGER,   -- NULL = 소급 적용분("이전 버전 불명"). FK 강제 안 함
                                   -- (참조된 버전이 삭제돼도 이 값은 그대로 남겨두고,
                                   --  조회 시 LEFT JOIN이 안 맞으면 "삭제됨"으로 표시)
    fit_score          INTEGER,
    fit_label          TEXT,
    content            TEXT       -- 그 시점 회사 .md 전체(적합도 리포트 포함), 상세보기용
);
CREATE INDEX idx_fit_history_slug ON fit_history(company_slug, created_at);
```

- `profile_version_id`에 FK 제약을 걸지 않는 이유: 스냅샷 삭제를 막지 않기로 했으므로(위
  "스냅샷 삭제 정책" 참고), 강제 제약이 있으면 삭제 자체가 막히거나 CASCADE로 이력까지
  같이 지워짐. 그냥 값만 남겨두고 조회 시점에 `LEFT JOIN`으로 매칭 여부를 판단해
  "삭제됨"/"이전 버전 불명"을 구분한다("이전 버전 불명" = `profile_version_id IS NULL`,
  "삭제됨" = 값은 있는데 `profile_versions`에 매칭되는 행이 없음).
- `fit_history.content`에 회사 파일 전체를 저장하는 이유: 표에는 점수/라벨만 보여주지만,
  나중에 특정 이력을 클릭해 그 시점 강점/갭 전체를 보고 싶을 수 있어 통으로 남겨둠(구조화
  안 해도 되니 구현도 단순).
- `write_profile()`이 파일을 쓸 때마다 `profile_versions`에도 새 행을 insert. 이후 refit이
  실행되면 "가장 최근 `profile_versions` 행"을 `profile_version_id`로 사용.
- DB 파일: `data/app.db`(`data/` 전체가 이미 git 미추적).
- 모듈 위치: `backend/services/profile_history.py`(가칭) — `sqlite3` 표준 라이브러리만
  사용, 앱 시작 시(`ensure_dirs()` 근처) 테이블 없으면 생성.

## 관련 결정 — SQLite 파일을 기능별로 안 나누고 공유(`data/app.db`)

프로필 히스토리 작업 중 "다른 기존 기능도 SQLite로 옮길 만한 게 있는지" 점검하다가
`usage_log.jsonl`(LLM 비용 추적 로그)이 좋은 후보로 나왔다. 이 개인용 앱 규모(데이터
수백 개 단위, 쓰기 빈도도 낮음)에서는 기능마다 `.db` 파일을 따로 두는 것보다 파일 하나에
테이블만 나눠 두는 게 관리가 단순하다고 판단 — 그래서 `data/profile_history.db`라는
이름 대신 **`data/app.db`** 하나로 통합하고, 여기에 `profile_versions`/`fit_history`
(이 문서의 기능) + `usage_log`(아래) 테이블을 같이 둔다. 나중에 다른 후보(예:
`rag_agent_log.jsonl`)를 옮길 때도 이 파일에 테이블만 추가하면 된다.

### `usage_log` 테이블 (기존 `data/usage_log.jsonl` 대체, 별도 작업)

`backend/services/usage_tracker.py`의 `read_usage()`가 지금 파일 전체(현재 768줄,
122KB)를 매번 읽어서 파싱·합산한다 — append-only라 계속 커지기만 하고 로테이션도 없어서
SQLite 전환 이유가 명확한 케이스. 이건 이 문서(프로필 히스토리)와는 별개 작업이지만
같은 `data/app.db`를 쓰기로 했으므로 스키마만 같이 기록해둔다.

```sql
CREATE TABLE usage_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    operation     TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd      REAL NOT NULL,
    request_id    TEXT
);
CREATE INDEX idx_usage_log_ts ON usage_log(ts);
```

`read_usage()`는 `SUM(cost_usd)`/`SUM(input_tokens)`/`SUM(output_tokens)`/`COUNT(*)`로
합계를 내고, `ORDER BY id DESC LIMIT ?`로 최근 항목만 가져오는 방식으로 바뀐다.
`rag_agent_log.jsonl`(RAG 도구 호출 트레이스)도 같은 패턴이지만 RAG(opt-in) 전용이라
후순위로 미룬다.

### "보기" 클릭 동작 (확정, 2026-08-15)

팝업으로 다루기엔 프로필 원문이 길어질 수 있어서, 기존 목록/상세/비교/타임라인과 같은
패턴으로 **별도 라우팅 화면**(`/profile-version/{id}`)을 새로 만든다 — 현재 프로필
화면과 비슷한 형태(기존 마크다운 렌더링 재사용)로 그 시점 스냅샷 원문을 보여준다.

### API 엔드포인트 (초안, 2026-08-16 — 구현하면서 계속 맞춰나갈 예정, 확정 아님)

**프로필 버전**
- `GET /api/profile/versions` — 목록(최신순): `id`, `created_at`, `summary`(스냅샷
  frontmatter의 summary 필드)
- `GET /api/profile/versions/{id}` — 스냅샷 전체(현재 `GET /api/profile`과 같은 형태)
- `DELETE /api/profile/versions/{id}` — 삭제

**회사 평가 이력**
- `GET /api/companies/{slug}/fit-history` — 목록(최신순): `id`, `created_at`,
  `profile_version_id`, `profile_version_created_at`, `fit_score`, `fit_label`(무거운
  `content` 필드는 응답에서 제외). `profile_version_id`가 null이면 "이전 버전 불명",
  값은 있는데 `profile_version_created_at`이 null이면 "삭제됨"으로 프론트가 판단.

**새 엔드포인트 없이 기존 API에 훅으로 처리**
- 프로필 스냅샷 생성: `POST /api/profile/upload`, `PUT /api/profile` 내부에서 저장 직후
  자동 insert
- 평가 이력 생성: 최초 등록(`_process_company`), `POST /{slug}/refit`,
  `POST /{slug}/refill` 내부에서 저장 직후 자동 insert

회사 상세 페이지 진입 시 `GET /api/companies/{slug}`와 `GET /api/companies/{slug}/fit-history`를
같이 호출해서 토글 누르기 전에도 "(N건)" 개수가 바로 보이게 한다(fit-history 응답 자체가
가벼워서 항상 같이 불러도 무방).

## 아직 미정

- 구현 시작 시점 — 아직 착수 안 함

## 관련 문서

- `docs/TODO.md` Phase 11 — 이 기능이 속한 항목(이름 변경 예정: "후보자 프로필 버전 관리"
  → 이 문서 제목에 맞춰 갱신)
