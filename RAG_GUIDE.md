# RAG 채팅 가이드 (선택 기능)

등록된 채용공고 전체를 근거로 자연어로 질문하고 답을 받는 대화형 채팅 기능입니다. 예:
"Redis 요구하는 공고 어디어디야?", "AWS를 안 해봤으면 단점이 될까?", "내 스킬 갭 요약해줘".

기본은 꺼져 있습니다. 안 켜도 지금처럼 마크다운 파일 기반 기능(회사 등록·적합도 평가·Q&A)은 그대로 동작합니다 — RAG는 그 위에 추가되는 별도 계층입니다.

## 켜는 방법

`.env`에 아래 한 줄을 추가하고,

```bash
RAG_POSTGRES_HOST=rag-postgres
```

Postgres(+pgvector) 컨테이너까지 함께 띄웁니다.

```bash
docker compose --profile rag up --build
```

임베딩은 `GOOGLE_API_KEY`(메인 앱에서 이미 설정한 키)를 그대로 재사용하므로 별도 키가 필요 없습니다. 켜져 있으면 상단 네비게이션에 **🤖 RAG** 버튼이 나타납니다.

> **Docker 없이 `uv`로 직접 실행하는 경로는 지원하지 않습니다** — Postgres를 별도로 준비해야 해서, `run/start-uv.command`/`.bat` 경로에서는 RAG 버튼이 계속 안 보입니다. RAG까지 쓰려면 Docker 방식으로 실행해주세요([GETTING_STARTED.md](GETTING_STARTED.md)의 "Docker로 실행하고 싶다면" 참고).

## 끄는 방법

`.env`에서 `RAG_POSTGRES_HOST`를 지우거나 빈 값으로 두면 됩니다. RAG 버튼이 사라지고, 나머지 기능은 영향 없습니다. `docker compose up`(profile 없이)으로 다시 실행하면 `rag-postgres` 컨테이너 자체도 안 뜹니다.

## 이력서(프로필) 내용도 근거로 쓰려면

기본적으로는 채용공고만 검색 대상입니다. "내가 이 회사에 지원하면 불리할까?" 같은, 본인 이력서와 대조해야 답할 수 있는 질문까지 쓰려면 `.env`에 아래를 추가하세요.

```bash
RAG_INCLUDE_PROFILE=true
```

이 값이 켜져 있으면 이력서 내용이 임베딩 API(Google)로 전송됩니다 — 기본값이 꺼져 있는 이유입니다. 켠 뒤에는 RAG 화면의 "🔄 재색인" 버튼을 한 번 눌러야 프로필이 실제로 반영됩니다.

## 사용하는 LLM provider

RAG 채팅은 메인 앱의 현재 LLM provider 설정(Claude/OpenAI/Gemini, 설정 화면에서 전환)을 그대로 따라갑니다. 임베딩 provider는 기본적으로 Google이고, RAG 화면 안의 "⚙️ 설정" 팝업에서 다른 provider로 바꿀 수 있습니다(GPU 인프라를 직접 구성한 경우에 한함 — 대부분의 배포에서는 Google 하나만 선택 가능합니다).

## 문제가 생기면

- RAG 버튼이 안 보임 → `.env`의 `RAG_POSTGRES_HOST`가 비어있거나, Docker 대신 uv로 실행 중인 경우입니다.
- 재색인 관련 오류 → `docker compose logs -f api`로 로그 확인.
- 그 외 개발 관련 세부 사항은 `backend/rag/README.md` 참고(코드 구조 설명, 개발자용).
