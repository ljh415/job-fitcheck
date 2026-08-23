"""RAG(대화형 근거 기반 검색) — main SPA `/rag` 뷰가 쓰는 API.

원래 `rag/main` 브랜치의 독립 테스트 화면(`rag-test.html`)용이었으나, main 반영
(`feat/rag-integration-plan`) 작업으로 main SPA(`frontend/index.html`의 `tpl-rag`)에
정식 통합됐다 — `rag-test.html`은 이제 안 씀. `rag/postgres/gap.py`·`answer.py`·`agent.py`를
그대로 호출하는 얇은 wrapper이고, 로직은 전부 `rag/` 안에 있다. 저장소는 PostgreSQL+pgvector
전용(SQLite `rag/gap.py`는 Plan A 기준선 재현용으로만 남아있고 이 서비스 경로에선 더 이상
안 씀 — Stage 6에서 전환).

RAG는 opt-in 기능이다 — `settings.rag_postgres_host`가 비어 있으면 `/status` 외 나머지
엔드포인트는 503을 반환한다(main 이식 시 opt-in 설계, `docs/rag-integration/STATUS.md` 2번
참고). 라우터 자체는 항상 등록한다 — import 시점에 DB 연결을 시도하지 않으므로 등록 자체는
안전하고, `/status`가 항상 응답 가능해야 프론트가 신뢰성 있게 활성화 여부를 조회할 수 있다.

임베딩 provider는 요청마다 고르지 않는다 — `resolve_rag_embedding_provider()`(`config.py`)가
메인 앱의 현재 LLM provider(Claude/OpenAI/Gemini)에서 자동 매핑하고(OpenAI는 임베딩 provider
미구현이라 google로 폴백), 필요하면 `/api/rag/settings`로 override해 `runtime_settings.json`에
영속화한다. `settings.rag_configured_providers`는 override 값이 유효한지 검사하는 용도로만
쓰인다 — Local은 GPU 인프라를 직접 구성한 배포에서만 켜지므로, 대부분의 배포에서는 이 목록이
`["google"]`뿐이다.
"""
import asyncio
import json
import logging
from datetime import datetime

import httpx
import psycopg
from fastapi import APIRouter, Depends, HTTPException
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

from config import (
    default_embedding_provider,
    get_rag_embedding_provider_override,
    resolve_rag_embedding_provider,
    set_rag_embedding_provider_override,
    settings,
)
from llm.base import LLMAPIError
from llm.router import capture_snapshot, high_from_snapshot
from rag.answer import generate_action_plan
from rag.embed.google import GoogleEmbeddingProvider
from rag.embed.local import LocalEmbeddingProvider
from rag.postgres.agent import answer_query_agent
from rag.postgres.db import get_connection
from rag.postgres.gap import assess_gap
from rag import reindex_service
# app_db는 채팅 이력용 SQLite(data/app.db) — 이 파일이 이미 쓰는 rag.postgres.db.get_connection()
# (RAG 벡터용 Postgres)과 완전히 다른 DB라 이름 충돌을 피하고 구분이 되도록 모듈째로 임포트해서
# app_db.xxx()로 호출한다.
from services import app_db

router = APIRouter(prefix="/api/rag")
logger = logging.getLogger(__name__)

# 진행 중인 RAG 생성 태스크 참조 보관 — qa.py의 _active_qa_tasks와 같은 이유
# (asyncio 문서 권고: 참조를 안 들고 있으면 이벤트 루프가 GC 시점에 실행 중인 태스크를
# 조용히 없애버릴 수 있다).
_active_rag_tasks: set[asyncio.Task] = set()


def _require_rag_enabled() -> None:
    if not settings.rag_postgres_host:
        raise HTTPException(503, "RAG가 설정되지 않았습니다 — RAG_POSTGRES_HOST 등 .env 설정이 필요합니다.")


def _require_profile_enabled() -> None:
    if not settings.rag_include_profile:
        raise HTTPException(400, "RAG_INCLUDE_PROFILE이 꺼져 있어 프로필 근거 기반 기능을 사용할 수 없습니다.")


def _require_app_db_healthy() -> None:
    """채팅방/메시지는 RAG의 Postgres가 아니라 data/app.db(SQLite)에 저장한다 —
    init_db() 실패로 is_healthy()=False면 조회는 빈 목록, 쓰기는 500으로 새서 DB 장애를
    "이력 없음"과 구분 못 하게 된다. companies.py/profile.py의 기존 fit_history/
    profile_versions 503 계약과 동일하게 맞춘다."""
    if not app_db.is_healthy():
        raise HTTPException(503, "채팅 기록 기능을 일시적으로 사용할 수 없습니다.")


@router.get("/status")
async def status():
    """RAG 활성화 여부 조회 — 프론트가 이걸로 RAG 관련 UI를 조건부로 노출한다.
    다른 엔드포인트와 달리 RAG 비활성 상태에서도 항상 응답한다. `configured_providers`는
    RAG가 꺼져 있으면 빈 배열 — 켜지지도 않은 상태에서 provider 선택지를 보여줄 이유가 없다."""
    enabled = bool(settings.rag_postgres_host)
    return {
        "enabled": enabled,
        "configured_providers": settings.rag_configured_providers if enabled else [],
        "include_profile": settings.rag_include_profile,
    }


class RagSettingsUpdateRequest(BaseModel):
    embedding_provider: str | None = None  # null=자동(메인 provider 따름), 아니면 명시적 고정


@router.get("/settings", dependencies=[Depends(_require_rag_enabled)])
async def get_rag_settings():
    """RAG 전용 설정 — 메인 설정 화면과 분리된, `/rag` 페이지 안의 별도 팝업에서 쓴다(쿼리마다
    provider를 고르던 드롭다운을 없애고 하나로 통일한 결정 — 이유는 `docs/rag-integration/
    STATUS.md` 4번 참고)."""
    # resolve_rag_embedding_provider()를 먼저 불러야 한다 — 이 함수가 유효하지 않은 override를
    # 자동으로 정리(None)하는 부수효과가 있어서, override를 먼저 읽으면 이번 응답에서만
    # override="local"인데 resolved="google"인 모순된 스냅샷이 나간다.
    resolved = resolve_rag_embedding_provider()
    return {
        "override": get_rag_embedding_provider_override(),
        "resolved": resolved,
        "available": settings.rag_configured_providers,
    }


@router.put("/settings", dependencies=[Depends(_require_rag_enabled)])
async def update_rag_settings(req: RagSettingsUpdateRequest):
    """embedding provider가 실제로 바뀌는 요청이면, override를 먼저 커밋하지 않고 **그
    provider로 재색인이 성공한 뒤에만** 커밋한다 — 안 그러면 저장 즉시 조회에 반영돼서,
    색인이 없거나 오래된 provider로 바로 전환되는 문제가 생긴다. 재색인이 실패하면 override는
    그대로 유지되고 에러만 반환 — "전환은 했는데 검색은 깨진" 중간 상태가 안 생긴다."""
    target = req.embedding_provider
    if target is not None and target not in settings.rag_configured_providers:
        raise HTTPException(400, f"embedding_provider는 {settings.rag_configured_providers} 중 하나이거나 null이어야 합니다")

    target_resolved = target or default_embedding_provider()
    if target_resolved != resolve_rag_embedding_provider():
        if not reindex_service.try_begin_manual():
            raise HTTPException(409, "재색인이 이미 진행 중입니다. 잠시 후 다시 시도하세요.")
        await _run_reindex_or_503(target_resolved)

    try:
        set_rag_embedding_provider_override(target)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "override": get_rag_embedding_provider_override(),
        "resolved": resolve_rag_embedding_provider(),
    }


class GapCheckRequest(BaseModel):
    skill: str


class AskRequest(BaseModel):
    """chat_id 채팅방에 질문을 추가한다. history는 클라이언트가 안 보낸다 — 서버가
    rag_messages에서 직접 조회(최근 20턴, status='done'만)해서 조립한다(QnA와 동일한 이유,
    docs/chat-history-server-storage/PLAN.md 참고)."""
    question: str = Field(max_length=2_000)
    chat_id: str


class RagMigrationMessage(BaseModel):
    question: str = Field(max_length=2_000)
    data: dict | None = None
    pending: bool = False


class RagMigrationChat(BaseModel):
    title: str | None = None
    created_at_ms: int
    messages: list[RagMigrationMessage] = Field(default_factory=list)


class RagChatMigrationRequest(BaseModel):
    """localStorage의 job-fitcheck-rag-chats 전체({chatId: {title, createdAt, messages}})를
    한 번에 보낼 때 쓰는 형태 그대로 받는다."""
    chats: dict[str, RagMigrationChat]


@router.post("/gap-check", dependencies=[Depends(_require_rag_enabled), Depends(_require_profile_enabled)])
async def gap_check(req: GapCheckRequest):
    provider = resolve_rag_embedding_provider()
    conn = None
    embed_provider = None
    try:
        # conn·embed_provider 생성 모두 try 안에서 해야 DB 연결 실패·SSH 터널/모델 검증
        # 실패(RuntimeError)가 503으로 잡힌다 — try 밖에 있으면 그대로 500으로 샌다. 미리
        # None으로 둬서 생성 자체가 실패해도 finally가 안전하게 아무 일도 안 하도록 한다.
        # 둘 다 동기(블로킹) 호출이라 asyncio.to_thread로 감싼다 — assess_gap/generate_action_plan
        # 내부의 진짜 비동기 LLM 호출은 이 uvicorn 이벤트 루프에 그대로 둔다. 함수 전체를
        # 워커 스레드에서 asyncio.run()으로 돌리면, llm/__init__.py의 provider 캐시
        # (anthropic.AsyncAnthropic 등 내부 httpx 연결 풀이 첫 사용 루프에 묶임)가 요청마다
        # 다른 이벤트 루프에서 재사용돼 "다른 루프에 묶인 객체" 오류를 낼 수 있다 — 그래서
        # 동기 DB·임베딩 호출만 개별적으로 to_thread로 옮기는 이 방식을 쓴다.
        conn = await asyncio.to_thread(get_connection)
        embed_provider = await asyncio.to_thread(
            GoogleEmbeddingProvider if provider == "google" else LocalEmbeddingProvider
        )
        # 판정 LLM provider/model/reasoning_effort를 요청 시작 시 한 번만 캡처해 끝까지 그대로
        # 쓴다 — 안 그러면 assess_gap() 내부 시장수요 판정·근거 판정·행동계획 생성이 각각 따로
        # capture_snapshot()을 불러서, 요청 처리 도중 설정 화면에서 provider를 바꾸면 한 요청
        # 안에서 provider가 섞일 수 있다(llm/router.py의 LLMSnapshot 설계 의도와도 일치).
        snap = capture_snapshot()
        llm = (*high_from_snapshot(snap), snap.reasoning_effort)
        gap_result = await assess_gap(conn, req.skill, embed_provider, llm=llm)
        action_plan = None
        if gap_result["evidence_level"] != "직접 근거":
            action_plan = await generate_action_plan(gap_result, llm=llm)
        return {
            "skill": gap_result["skill"],
            "evidence_level": gap_result["evidence_level"],
            "reasoning": gap_result["reasoning"],
            "market_demand": gap_result["market_demand"],
            "excerpts": gap_result["excerpts"],
            "action_plan": action_plan,
            "provider": provider,
        }
    except LLMAPIError as e:
        # assess_gap()/generate_action_plan()이 쓰는 LLM provider(Claude/OpenAI/Gemini 텍스트
        # 생성)의 인증 실패·rate limit·서버 오류는 LLMAPIError로 래핑된다(routers/companies.py
        # 등 기존 라우터가 이미 쓰는 패턴). LLMAPIError가 자체 status_code(401/429/503 등)를
        # 갖고 있으니 그대로 반영한다 — 임베딩 API 오류와는 다른 조각(LLM 텍스트 생성 쪽)이라
        # genai_errors.APIError로는 안 잡힌다.
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except RuntimeError as e:
        # LocalEmbeddingProvider의 SSH 터널/모델 검증 실패, assess_gap()의 프로필 미임베딩 감지 등
        raise HTTPException(503, f"RAG 파이프라인 연결/설정 오류: {e}")
    except httpx.HTTPError as e:
        # provider 생성 후 실제 embed_documents()/embed_query() 호출 중 네트워크 오류 —
        # 이것도 503(일시적 연결 문제)이지 500(서버 버그)이 아니다.
        raise HTTPException(503, f"임베딩 서버 통신 오류: {e}")
    except genai_errors.APIError as e:
        # LocalEmbeddingProvider는 통신 오류를 httpx.HTTPError로 던지지만 Google provider는
        # google.genai.errors.ClientError/ServerError를 그대로 재발생시켜서(429 재시도 소진,
        # API 키 오류, Google 서버 5xx 등) 위 except들로 안 잡히면 그대로 500으로 샌다.
        # ClientError/ServerError 둘 다 APIError의 하위 클래스라, 공통 상위 클래스 APIError로
        # 잡아야 4xx·5xx 둘 다 커버된다. 이것도 서버 버그가 아니라 외부 API 오류이므로 503.
        raise HTTPException(503, f"Google 임베딩 API 오류: {e}")
    except psycopg.OperationalError as e:
        # get_connection() 실패(Postgres 다운·인증 오류 등)만 좁게 잡는다 — psycopg.Error
        # 전체를 잡으면, assess_gap() 안의 SQL 문법·제약조건 위반 같은 실제 버그
        # (ProgrammingError/IntegrityError 등도 psycopg.Error 하위)까지 "일시적 DB 연결 오류"로
        # 둔갑해 500으로 표시돼야 할 게 503으로 가려진다. 메시지도 호스트/스키마 같은 내부
        # 정보를 그대로 노출하지 않도록 일반화한다.
        raise HTTPException(503, "DB 연결 오류 — 잠시 후 다시 시도하세요")
    finally:
        # 두 자원을 독립적으로 정리한다 — embed_provider.close()(SSH 터널 종료 대기라 실패할 수
        # 있음)가 예외를 던지면 그다음 줄(conn.close())이 실행되지 않고 원래 예외까지 가려질 수
        # 있다. 둘 다 블로킹이라 to_thread로 감싼다.
        close = getattr(embed_provider, "close", None)
        if close:
            try:
                await asyncio.to_thread(close)
            except Exception:
                pass
        if conn is not None:
            await asyncio.to_thread(conn.close)  # GC(__del__) 의존 시 idle in transaction 커넥션이 쌓일 수 있다


async def _run_reindex_or_503(provider: str) -> None:
    """reindex_service.run()을 감싸 실패를 일관된 HTTPException으로 변환한다.
    `/reindex`와 `update_rag_settings()`(provider 전환 시 선행 재색인) 둘 다 같은 처리가
    필요해서 공유한다."""
    try:
        await reindex_service.run(provider)
    except RuntimeError as e:
        raise HTTPException(503, f"RAG 파이프라인 연결/설정 오류: {e}")
    except httpx.HTTPError as e:
        raise HTTPException(503, f"임베딩 서버 통신 오류: {e}")
    except genai_errors.APIError as e:
        raise HTTPException(503, f"Google 임베딩 API 오류: {e}")
    except psycopg.OperationalError as e:
        raise HTTPException(503, "DB 연결 오류 — 잠시 후 다시 시도하세요")


@router.post("/reindex", dependencies=[Depends(_require_rag_enabled)])
async def reindex():
    """재색인 웹 트리거 — `rag.postgres.reindex.run()`이 지금까지 CLI 전용이라
    실제 사용자는 트리거할 방법이 없었다. 항상 `resolve_rag_embedding_provider()`가 결정한
    활성 provider 하나만 재색인한다(provider별로 따로 고르던 구조를 없애고 설정값 하나로
    통일 — `/api/rag/settings` 참고). 프로필 포함 여부는
    `settings.rag_include_profile`(기본 false — 이력서 내용이 임베딩 API로 전송되는 걸
    사용자가 명시적으로 켜야 함)을 따른다. run()은 동기 함수(psycopg/httpx 동기 호출)라
    `asyncio.to_thread`로 감싸 이벤트 루프를 막지 않는다 — 출력은 그대로 컨테이너 stdout으로
    흘려보내 기존 `docker compose logs -f api` 디버깅 흐름을 유지한다. 수동 트리거라 이미
    진행 중이면(자동 트리거와 겹쳤을 수도 있음) 조용히 미루지 않고 409로 명확히 알린다 —
    사용자가 버튼을 눌렀는데 응답이 없으면 안 되므로."""
    if not reindex_service.try_begin_manual():
        raise HTTPException(409, "재색인이 이미 진행 중입니다. 잠시 후 다시 시도하세요.")
    provider = resolve_rag_embedding_provider()  # 시작 시점에 한 번만 확정 — 응답도 이 값을 그대로 씀
    await _run_reindex_or_503(provider)
    return {"status": "ok", "provider": provider}


def _history_from_rag_rows(rows: list[dict]) -> list[dict]:
    """rag_messages 조회 결과(한 행=질문+답변 data JSON)를 answer_query_agent()가 받는
    {role, content} 번갈아 나오는 형태로 펼친다."""
    result: list[dict] = []
    for r in rows:
        result.append({"role": "user", "content": r["question"]})
        data = json.loads(r["data"]) if r["data"] else {}
        result.append({"role": "assistant", "content": data.get("answer", "")})
    return result


async def _run_rag_generation(message_id: int, chat_id: str, question: str) -> dict:
    """대화형 근거 기반 RAG 실행 — 요청/응답 코루틴과 분리된 독립 태스크로 돌린다. QnA의
    _run_qa_generation()과 같은 이유(docs/chat-history-server-storage/PLAN.md "LLM 호출을
    HTTP 요청 생명주기와 분리" 참고) — 클라이언트가 연결을 끊어도 이 태스크는 계속 실행되어
    DB에 결과를 남긴다. conn/embed_provider 생성·정리·예외 처리는 기존 ask()/gap_check()와
    동일한 패턴을 그대로 유지한다."""
    provider = resolve_rag_embedding_provider()
    conn = None
    embed_provider = None
    try:
        conn = await asyncio.to_thread(get_connection)
        embed_provider = await asyncio.to_thread(
            GoogleEmbeddingProvider if provider == "google" else LocalEmbeddingProvider
        )
        history = _history_from_rag_rows(app_db.list_rag_context(chat_id))
        result = await answer_query_agent(conn, question, embed_provider, history=history)
        result["provider"] = provider
        app_db.mark_rag_message_done(message_id, json.dumps(result, ensure_ascii=False))
        return result
    except Exception as e:
        app_db.mark_rag_message_failed(message_id, str(e))
        raise
    finally:
        close = getattr(embed_provider, "close", None)
        if close:
            try:
                await asyncio.to_thread(close)
            except Exception:
                pass
        if conn is not None:
            await asyncio.to_thread(conn.close)


@router.get("/chats", dependencies=[Depends(_require_rag_enabled), Depends(_require_app_db_healthy)])
async def list_chats():
    """채팅방 목록(드롭다운용) — 최신순."""
    return {"chats": app_db.list_rag_chats()}


@router.get("/chats/{chat_id}", dependencies=[Depends(_require_rag_enabled), Depends(_require_app_db_healthy)])
async def get_chat_messages(chat_id: str):
    """특정 방의 메시지 전체(pending·failed 포함) — 페이지 로드 시 이걸로 채팅 화면을
    복원한다(localStorage 대체). data는 DB엔 JSON 문자열로 저장돼있는데 그대로 돌려주면
    응답 전체가 JSON 직렬화될 때 이중 인코딩된 문자열이 되므로, 여기서 미리 파싱해
    중첩 객체로 내려준다(ask()가 반환하는 result와 같은 모양으로 맞춤)."""
    if not app_db.get_rag_chat(chat_id):
        raise HTTPException(404, "채팅방을 찾을 수 없습니다.")
    messages = app_db.list_rag_messages(chat_id)
    for m in messages:
        m["data"] = json.loads(m["data"]) if m["data"] else None
    return {"messages": messages}


@router.post("/chats", dependencies=[Depends(_require_rag_enabled), Depends(_require_app_db_healthy)])
async def create_chat():
    """새 채팅방 생성 — id는 서버가 발급(기존 프론트 'chat-<timestamp>' 형식과 동일하게
    맞춰서 마이그레이션으로 넘어온 옛 방 id와 형태를 통일)."""
    chat_id = f"chat-{int(datetime.now().timestamp() * 1000)}"
    app_db.create_rag_chat(chat_id)
    return app_db.get_rag_chat(chat_id)


@router.delete("/chats/{chat_id}", dependencies=[Depends(_require_rag_enabled), Depends(_require_app_db_healthy)])
async def delete_chat(chat_id: str):
    """채팅방 삭제 — rag_messages는 FK(ON DELETE CASCADE)로 자동 정리된다."""
    if not app_db.delete_rag_chat(chat_id):
        raise HTTPException(404, "채팅방을 찾을 수 없습니다.")
    return {"status": "ok"}


@router.post("/migrate-chats", dependencies=[Depends(_require_rag_enabled), Depends(_require_app_db_healthy)])
async def migrate_chats(req: RagChatMigrationRequest):
    """localStorage job-fitcheck-rag-chats 전체를 1회성으로 서버 저장(rag_chats/rag_messages)
    으로 옮긴다. pending:true로 남아있던 미완성 메시지는 건너뛴다(QnA migrate-qa와 동일한
    원칙 — 짝 안 맞는/미완성 항목은 스킵). 방 하나의 생성+메시지 전체 삽입은
    app_db.migrate_rag_chat()이 한 트랜잭션으로 처리 — 방 생성과 메시지 삽입이 각각 별도
    커밋이면 중간 실패 시 일부만 들어간 채 영구 스킵될 수 있는데, 한 트랜잭션으로 묶으면
    중간에 실패해도 그 방 전체가 롤백되어 재시도 시 처음부터 다시 시도할 수 있다. 유효한
    메시지가 하나도 없는 방은 빈 방으로 만들지 않고 건너뛴다."""
    inserted = 0
    for chat_id, chat in req.chats.items():
        entries = [
            (m.question, json.dumps(m.data, ensure_ascii=False))
            for m in chat.messages if not m.pending and m.data
        ]
        if not entries:
            continue
        created_at = datetime.fromtimestamp(chat.created_at_ms / 1000).isoformat(timespec="seconds")
        inserted += app_db.migrate_rag_chat(chat_id, chat.title, created_at, entries)
    return {"inserted": inserted}


@router.post("/ask", dependencies=[Depends(_require_rag_enabled), Depends(_require_app_db_healthy)])
async def ask(req: AskRequest):
    """대화형 근거 기반 RAG 진입점 — 자연어 질문을 Agent(tool-use)로 답한다(Phase 1~4의
    "질문 분류→고정 함수 실행" 구조를 대체 — 질문이 애매하거나 여러 능력을 조합해야 답할 수
    있을 때 완전히 무관한 답을 내놓는 구조적 결함이 있었다. 상세는
    `docs/rag-project-plans/00_meta/HISTORY.md` 참고). 실제 생성은 _run_rag_generation()에
    위임 — 독립 태스크로 돌려서 클라이언트 연결 끊김과 무관하게 끝까지 실행되게 한다(위 "LLM
    호출을 HTTP 요청 생명주기와 분리" 참고). 이 함수는 pending 삽입 → 태스크 생성 → 결과 대기
    → 예외를 기존과 동일한 HTTPException으로 변환하는 역할만 한다."""
    if not app_db.get_rag_chat(req.chat_id):
        raise HTTPException(404, "채팅방을 찾을 수 없습니다.")
    app_db.set_rag_chat_title_if_empty(
        req.chat_id, req.question[:24] + ("…" if len(req.question) > 24 else "")
    )
    message_id = app_db.insert_pending_rag_message(req.chat_id, req.question)
    task = asyncio.create_task(_run_rag_generation(message_id, req.chat_id, req.question))
    _active_rag_tasks.add(task)
    task.add_done_callback(_active_rag_tasks.discard)
    try:
        # asyncio.Task.cancel()은 문서화된 동작상 "지금 await 중인 Future/Task"에도 취소를
        # 전파한다 — 그냥 `await task`면 이 요청 코루틴이 취소될 때(클라이언트 연결 끊김 등)
        # task도 같이 취소돼 pending으로 남을 수 있다. shield로 감싸면 바깥 취소가 이
        # await 표현식만 끊고 task 자체는 보호돼 끝까지 실행된다 — 실측 curl/Playwright
        # 테스트에선 재현 안 됐지만, uvicorn이 비-스트리밍 응답에 취소를 안 거는 현재 동작에
        # 우연히 기댄 것일 수 있어 방어적으로 반영한다.
        result = await asyncio.shield(task)
        result["message_id"] = message_id
        return result
    except LLMAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(503, f"RAG 파이프라인 연결/설정 오류: {e}")
    except httpx.HTTPError as e:
        raise HTTPException(503, f"임베딩 서버 통신 오류: {e}")
    except genai_errors.APIError as e:
        raise HTTPException(503, f"Google 임베딩 API 오류: {e}")
    except psycopg.OperationalError as e:
        raise HTTPException(503, "DB 연결 오류 — 잠시 후 다시 시도하세요")
