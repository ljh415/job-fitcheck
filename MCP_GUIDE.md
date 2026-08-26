# MCP 서버 가이드

Codex·Claude 같은 외부 AI 클라이언트가 채팅 안에서 직접 Job FitCheck의 회사·프로필·RAG
기능을 도구로 쓸 수 있게 하는 기능입니다. 예: "지원한 회사 목록 보여줘", "이 공고 URL 분석해서
등록해줘", "내 프로필이랑 비교했을 때 이 공고 갭이 뭐야?"

> **현재 상태**: `feat/mcp-server` 브랜치에만 있고 아직 `main`(prod)에 merge되지 않았습니다.
> 지금은 dev 환경에서만 쓸 수 있습니다. main에 merge되면 별도 설정 없이 항상 켜져 있습니다
> (RAG처럼 opt-in이 아니라, 서버가 뜨면 `/api/mcp` 경로가 자동으로 함께 뜹니다).

## 준비물

1. Job FitCheck 로그인 비밀번호(`APP_SECRET`)
2. MCP 클라이언트(Claude Code, Codex 등) — HTTP transport와 커스텀 헤더를 지원해야 함

## 연결 방법 (Claude Code 기준)

**1) JWT 토큰 발급**

```bash
curl -s -X POST http://<서버주소>/api/login \
  -H "Content-Type: application/json" \
  -d '{"password": "<APP_SECRET>"}'
```

응답의 `token` 값을 복사해둡니다(30일 유효).

**2) MCP 서버로 등록**

```bash
claude mcp add --transport http job-fitcheck http://<서버주소>/api/mcp/mcp \
  --header "Authorization: Bearer <위에서 받은 토큰>"
```

**3) 등록 확인**

```bash
claude mcp list
```

그 다음부턴 새 Claude Code 세션에서 자연어로 요청하면 됩니다 — 별도 명령어 없이 "내 회사
목록 보여줘" 같은 요청만으로 도구가 호출됩니다.

> 인증은 웹 로그인과 동일한 `APP_SECRET` 기반 JWT 하나뿐입니다. 별도의 MCP 전용 권한 체계는
> 없습니다 — 개인용 도구라 로그인 = 전체 접근 허용이라는 전제입니다.

## 제공하는 도구

### 회사·지원 관리

| 도구 | 설명 |
|---|---|
| `list_companies` | 검색어·상태·핀·최소 점수로 회사 목록 필터링 |
| `get_company` | 특정 회사의 원문·분석 결과·상태 로그 전체 조회 |
| `compare_companies` | 최대 5개 회사를 나란히 비교 |
| `get_application_timeline` | 지원한 회사들의 상태 변화 이력을 시간순으로 조회 |
| `update_company` | 지원 상태·즐겨찾기(핀) 변경 — **확인 없이 즉시 실행** |
| `prepare_company_import` | 공고 URL/텍스트 원문 수집 + 분석용 프롬프트 3종 준비(LLM 미호출, 비용 없음) |
| `create_company` | 분석 결과를 검증 후 저장 — **확인 없이 즉시 실행** |

### 프로필

| 도구 | 설명 |
|---|---|
| `get_profile` | 후보자 프로필(구조화 정보 + 본문) 조회 |

### RAG (선택 기능, `RAG_POSTGRES_HOST` 설정 시에만)

| 도구 | 설명 |
|---|---|
| `get_rag_status` | RAG 활성화 여부 확인 |
| `search_rag_evidence` | 질문과 관련된 공고·프로필 근거 청크를 벡터 검색(임베딩 API 비용 발생) |
| `list_matching_postings` | 기술 스택·직무명으로 공고 검색(순수 DB 조회, 비용 없음) |

## 쓰기 승인 정책

원칙은 "기존 데이터를 덮어쓰거나 지우는 건 확인이 필요하고, 순수 추가·사용자가 평소 클릭
한 번으로 바꾸던 저위험 필드는 확인 없이 바로 실행"입니다.

- `create_company`, `update_company`: 확인 불필요, 즉시 실행
- 삭제·재분석(refill): **1차 범위에 없음** — MCP로 지우거나 재분석할 방법이 아예 없습니다

## 회사 등록이 특별한 이유 — 2단계 흐름

`create_company`는 회사 정보를 그대로 안 받고, `prepare_company_import`와 짝을 이룹니다:

```
1. prepare_company_import(url 또는 raw_text)
   → 원문 수집 + 분석용 프롬프트 3종(구조화 추출/본문 생성/적합도 평가) 반환
   → 이 단계는 LLM을 호출하지 않음(Job FitCheck API 비용 0원)

2. Codex/Claude 자신의 세션 모델이 그 프롬프트로 직접 3단계 분석 수행

3. create_company(분석 결과)
   → 검증 후 저장, 적합도 이력 기록, 알림 발송, RAG 재색인까지 자동
```

즉 **회사 하나를 등록해도 Job FitCheck 자체 LLM API 비용이 전혀 들지 않습니다** — 분석은
전부 호출한 클라이언트(Codex/Claude)가 자기 세션으로 처리합니다. 대신 그만큼 실제 분석
품질은 클라이언트가 얼마나 꼼꼼히 프롬프트를 따르느냐에 달려 있습니다.

> **주의: 적합도 점수의 일관성이 웹과 다를 수 있습니다.** 웹에서 등록한 회사는 항상 설정에서
> 고른 동일한 provider(Claude/GPT/Gemini 중 하나)가 같은 프롬프트로 평가하지만, MCP로 등록한
> 회사는 **그 순간 접속한 클라이언트의 세션 모델**이 평가합니다 — Claude Code로 하면 Claude가,
> Codex로 하면 GPT 계열이 평가하고, 클라이언트·세션에 따라 모델 자체가 달라질 수 있습니다.
> 그래서 MCP로 등록한 회사와 웹으로 등록한 회사의 점수는 "같은 평가자가 매긴 점수"가 아니라서
> 서로 직접 비교하기엔 적합하지 않을 수 있습니다. 점수의 일관된 비교가 중요하다면 웹 등록을
> 우선 고려하세요.
>
> 더 근본적으로, Job FitCheck의 자체 API 호출은 그 목적 하나만을 위한 시스템 프롬프트 +
> 해당 공고 데이터만 들어간, **매번 깨끗하게 격리된 단발성 호출**입니다. 반면 MCP는 이미
> 진행 중인 대화(그 클라이언트 자체의 시스템 프롬프트, 이전 대화 맥락 등이 이미 깔린 상태)
> 안에서 호출됩니다 — 대화 중간에 요청하면 그 세션의 흐름이나 톤, 이전에 논의하던 내용의
> 영향을 받을 여지가 구조적으로 존재합니다. 중요한 분석일수록 이 점을 감안하세요.

## 예시 대화

> "https://www.wanted.co.kr/wd/12345 이 공고 분석해서 등록해줘"

1. Claude/Codex가 `prepare_company_import(url=...)`로 원문+프롬프트를 받음
2. 원문을 읽고 구조화 정보 추출 → 마크다운 본문 생성 → (프로필 있으면) 적합도 평가까지 직접 수행
3. `create_company(...)`로 저장 요청
4. 완료되면 지원 상태 로그·적합도 이력·알림(설정돼 있으면 텔레그램/슬랙/디스코드)까지 자동 반영

> "지원한 회사들 상태 어떻게 됐어?"

`get_application_timeline`을 호출해 시간순으로 정리해서 답변합니다.

## 알려진 제약

- **잡플래닛 점수는 채워지지 않습니다** — 웹 등록 경로는 잡플래닛을 별도로 스크래핑하지만,
  MCP 경로는 LLM 추출 스키마 안에 없는 필드라 항상 비어 있습니다. 채우려면 웹 화면에서 수동
  편집으로 직접 입력해야 합니다.
- 삭제·재분석 도구는 없습니다(위 참고).
- MCP 전용 세분화된 권한은 없습니다 — 로그인 = 전체 접근.

## 문제가 생기면

- 도구 호출이 전부 실패 → 토큰 만료(30일) 가능성, `/api/login`으로 재발급 후 서버 재등록
- 에러 메시지가 이유 없이 "Error executing tool X"만 뜸 → 서버 쪽 문제일 수 있음, 로그(`docker compose logs -f api`) 확인
- RAG 관련 도구가 `enabled: false`만 반환 → `RAG_POSTGRES_HOST` 미설정(정상, opt-in 기능)
