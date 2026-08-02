"""RAG 서브프로젝트(rag/main 전용) 테스트 화면용 API.

Plan B 6단계(`rag/postgres/gap.py`, `rag/postgres/answer.py`)를 그대로 호출한다 — 이 라우터는
얇은 wrapper일 뿐, 로직은 전부 rag/ 안에 있다. provider(google/local)를 요청마다 선택할 수
있게 해서, "provider 하나로 고정하지 않고 비교한다"는 Plan A 설계 원칙을 UI에서도 유지한다.
저장소는 PostgreSQL+pgvector 전용(SQLite `rag/gap.py`는 Plan A 기준선 재현용으로만 남아있고
이 서비스 경로에선 더 이상 안 씀 — Stage 6에서 전환).

RAG는 opt-in 기능이다 — `settings.rag_postgres_host`가 비어 있으면 `/status` 외 나머지
엔드포인트는 503을 반환한다(main 이식 시 opt-in 설계, `docs/rag-integration/STATUS.md` 2번
참고). 라우터 자체는 항상 등록한다 — import 시점에 DB 연결을 시도하지 않으므로 등록 자체는
안전하고, `/status`가 항상 응답 가능해야 프론트가 신뢰성 있게 활성화 여부를 조회할 수 있다.

임베딩 provider(google/local) 선택은 어디서나 `settings.rag_configured_providers`(3번
항목) 기준으로 검증한다 — Local은 GPU 인프라를 직접 구성한 배포에서만 켜지므로, 대부분의
배포에서는 이 목록이 `["google"]`뿐이다.
"""
import asyncio
import threading
from typing import Literal

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
from rag.postgres import reindex as rag_reindex

router = APIRouter(prefix="/api/rag")


def _require_rag_enabled() -> None:
    if not settings.rag_postgres_host:
        raise HTTPException(503, "RAG가 설정되지 않았습니다 — RAG_POSTGRES_HOST 등 .env 설정이 필요합니다.")


def _require_profile_enabled() -> None:
    if not settings.rag_include_profile:
        raise HTTPException(400, "RAG_INCLUDE_PROFILE이 꺼져 있어 프로필 근거 기반 기능을 사용할 수 없습니다.")


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
    """RAG 전용 설정 — 메인 설정 화면과 분리된, `/rag` 페이지 안의 별도 팝업에서 쓴다(2026-07-31,
    쿼리마다 provider를 고르던 드롭다운을 없애고 하나로 통일한 결정 참고 — 이유는
    `docs/rag-integration/STATUS.md` 4번 참고)."""
    # resolve_rag_embedding_provider()를 먼저 불러야 한다 — 이 함수가 유효하지 않은 override를
    # 자동으로 정리(None)하는 부수효과가 있어서, override를 먼저 읽으면 이번 응답에서만
    # override="local"인데 resolved="google"인 모순된 스냅샷이 나간다(라이브 테스트로 발견).
    resolved = resolve_rag_embedding_provider()
    return {
        "override": get_rag_embedding_provider_override(),
        "resolved": resolved,
        "available": settings.rag_configured_providers,
    }


@router.put("/settings", dependencies=[Depends(_require_rag_enabled)])
async def update_rag_settings(req: RagSettingsUpdateRequest):
    """embedding provider가 실제로 바뀌는 요청이면, override를 먼저 커밋하지 않고 **그
    provider로 재색인이 성공한 뒤에만** 커밋한다(Codex 재리뷰로 발견, 2026-07-31 —
    예전엔 저장 즉시 조회에 반영돼서, 색인이 없거나 오래된 provider로 바로 전환되는
    문제가 있었음). 재색인이 실패하면 override는 그대로 유지되고 에러만 반환 — "전환은
    했는데 검색은 깨진" 중간 상태가 안 생긴다."""
    target = req.embedding_provider
    if target is not None and target not in settings.rag_configured_providers:
        raise HTTPException(400, f"embedding_provider는 {settings.rag_configured_providers} 중 하나이거나 null이어야 합니다")

    global _reindex_in_progress
    target_resolved = target or default_embedding_provider()
    if target_resolved != resolve_rag_embedding_provider():
        with _reindex_lock:
            if _reindex_in_progress:
                raise HTTPException(409, "재색인이 이미 진행 중입니다. 잠시 후 다시 시도하세요.")
            _reindex_in_progress = True
        await _run_reindex_or_503(target_resolved)

    try:
        set_rag_embedding_provider_override(target)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "override": get_rag_embedding_provider_override(),
        "resolved": resolve_rag_embedding_provider(),
    }


_reindex_in_progress = False  # main.py가 uvicorn 단일 프로세스(workers 미지정)라 in-process
# 플래그로 충분하다 — chunk_embedding에 UNIQUE(chunk_id, provider, model, dimensions) 제약이
# 있어서 재색인 두 개가 겹치면 두 번째가 UniqueViolation으로 500이 났다(Codex 리뷰로 발견,
# 2026-07-29). 여러 worker/인스턴스로 확장하면 advisory lock으로 바꿔야 한다.
_reindex_lock = threading.Lock()  # _reindex_in_progress/_reindex_pending은 이벤트 루프
# 스레드(API 요청)와 워커 스레드(asyncio.to_thread로 도는 _run_reindex_sync) 양쪽에서
# 건드린다 — "확인 후 결정"이 두 단계짜리라 잠금 없인 원자적이지 않다. 워커가 pending을
# False로 확인하고 루프를 빠져나가는 그 순간과, CRUD 훅이 in_progress를 확인해 pending을
# True로 세우는 순간이 겹치면 방금 세운 pending을 워커의 finally가 바로 덮어써서 CRUD의
# "재색인 필요" 신호가 조용히 사라질 수 있었다(Codex 재리뷰로 발견, 2026-07-31/2026-08-02
# 재검토 후 threading.Lock으로 해결 — 두 플래그를 건드리는 모든 지점을 이 잠금으로 감싼다).


class GapCheckRequest(BaseModel):
    skill: str


class AskMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)


class AskRequest(BaseModel):
    # 메인 앱 QARequest(models.py)와 같은 방어값 — 예전엔 history가 무제한 list[dict]라 role/길이
    # 검증이 전혀 없었다(Codex 리뷰로 발견, 2026-07-29). 긴 대화는 결국 Anthropic API의 context
    # 한도를 넘어 영구 실패하고, 잘못된 role/content는 외부 API 400이 503으로 오인 처리됐다.
    question: str = Field(max_length=2_000)
    history: list[AskMessage] = Field(default_factory=list, max_length=40)


@router.post("/gap-check", dependencies=[Depends(_require_rag_enabled), Depends(_require_profile_enabled)])
async def gap_check(req: GapCheckRequest):
    provider = resolve_rag_embedding_provider()

    def _run_sync() -> dict:
        # DB 연결·임베딩 API 호출이 전부 동기라, 이 함수 전체를 asyncio.to_thread로 워커
        # 스레드에 넘긴다 — 안 그러면 uvicorn 단일 이벤트 루프가 그동안 통째로 막혀 로그인·
        # 회사 조회 등 무관한 요청까지 같이 멈춘다(Codex 3차 리뷰로 발견, 2026-08-02, 최악
        # 429 재시도 시 최대 40초). 내부의 진짜 비동기 LLM 호출(assess_gap/generate_action_plan)은
        # 이 스레드 안에서만 도는 별도 이벤트 루프(asyncio.run)로 처리한다.
        conn = None
        embed_provider = None
        try:
            # conn 생성도 try 안에서 해야 DB 연결 실패가 503으로 잡힌다(Postgres용으로 포팅하며
            # 이 자리에 다시 놓쳤던 실수 — 원래 embed_provider에 대해 이미 한 번 고친 패턴과
            # 똑같은 이유, Codex 리뷰로 발견, 2026-07-23). provider 생성도 try 안에서 해야
            # SSH 터널/모델 검증 실패(RuntimeError)가 503으로 잡힌다 — 예전엔 try 밖에 있어서
            # 이 실패가 그대로 500으로 샜다(Codex 리뷰로 발견, 2026-07-23). conn/embed_provider를
            # 미리 None으로 둬서 생성 자체가 실패해도 finally가 안전하게 아무 일도 안 하도록 한다.
            conn = get_connection()
            embed_provider = GoogleEmbeddingProvider() if provider == "google" else LocalEmbeddingProvider()
            # 판정 LLM provider/model/reasoning_effort를 요청 시작 시 한 번만 캡처해 끝까지 그대로
            # 쓴다 — 안 그러면 assess_gap() 내부 시장수요 판정·근거 판정·행동계획 생성이 각각 따로
            # capture_snapshot()을 불러서, 요청 처리 도중 설정 화면에서 provider를 바꾸면 한 요청
            # 안에서 provider가 섞일 수 있다(Codex 리뷰 2026-07-29 지적, llm/router.py의
            # LLMSnapshot 설계 의도와도 일치).
            snap = capture_snapshot()
            llm = (*high_from_snapshot(snap), snap.reasoning_effort)

            async def _business():
                gap_result = await assess_gap(conn, req.skill, embed_provider, llm=llm)
                action_plan = None
                if gap_result["evidence_level"] != "직접 근거":
                    action_plan = await generate_action_plan(gap_result, llm=llm)
                return gap_result, action_plan

            gap_result, action_plan = asyncio.run(_business())
            return {
                "skill": gap_result["skill"],
                "evidence_level": gap_result["evidence_level"],
                "reasoning": gap_result["reasoning"],
                "market_demand": gap_result["market_demand"],
                "excerpts": gap_result["excerpts"],
                "action_plan": action_plan,
                "provider": provider,
            }
        finally:
            close = getattr(embed_provider, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass
            if conn is not None:
                conn.close()

    try:
        return await asyncio.to_thread(_run_sync)
    except LLMAPIError as e:
        # assess_gap()/generate_action_plan()이 쓰는 LLM provider(Claude/OpenAI/Gemini 텍스트
        # 생성)의 인증 실패·rate limit·서버 오류는 LLMAPIError로 래핑되는데(routers/companies.py
        # 등 기존 라우터가 이미 쓰는 패턴), RAG 라우터엔 이 except가 없어서 그대로 500으로 샜다
        # (Codex 5차 재리뷰로 발견, 2026-07-24). LLMAPIError가 자체 status_code(401/429/503 등)를
        # 갖고 있으니 그대로 반영한다 — 임베딩 API 오류와는 다른 조각(LLM 텍스트 생성 쪽)이라
        # genai_errors.APIError로는 안 잡힘.
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except RuntimeError as e:
        # LocalEmbeddingProvider의 SSH 터널/모델 검증 실패, assess_gap()의 프로필 미임베딩 감지 등
        raise HTTPException(503, f"RAG 파이프라인 연결/설정 오류: {e}")
    except httpx.HTTPError as e:
        # provider 생성 후 실제 embed_documents()/embed_query() 호출 중 네트워크 오류 —
        # 이것도 503(일시적 연결 문제)이지 500(서버 버그)이 아니다(Codex 재리뷰로 발견, 2026-07-23)
        raise HTTPException(503, f"임베딩 서버 통신 오류: {e}")
    except genai_errors.APIError as e:
        # LocalEmbeddingProvider는 통신 오류를 httpx.HTTPError로 던지지만 Google provider는
        # google.genai.errors.ClientError/ServerError를 그대로 재발생시켜서(429 재시도 소진,
        # API 키 오류, Google 서버 5xx 등) 위 except들로 안 잡히고 그대로 500으로 샜다(Codex
        # 3차 재리뷰로 발견, 2026-07-24). 처음엔 ClientError만 잡았는데 ClientError/ServerError
        # 둘 다 APIError의 하위 클래스라 4xx만 잡히고 5xx는 여전히 샜다(Codex 4차 재리뷰로
        # 발견, 2026-07-24) — 공통 상위 클래스 APIError로 잡아서 둘 다 커버한다. 이것도 서버
        # 버그가 아니라 외부 API 오류이므로 503.
        raise HTTPException(503, f"Google 임베딩 API 오류: {e}")
    except psycopg.OperationalError as e:
        # get_connection() 실패(Postgres 다운·인증 오류 등)만 좁게 잡는다 — 처음엔 psycopg.Error
        # 전체를 잡았는데, 그러면 assess_gap() 안의 SQL 문법·제약조건 위반 같은 실제 버그
        # (ProgrammingError/IntegrityError 등도 psycopg.Error 하위)까지 "일시적 DB 연결 오류"로
        # 둔갑해 500으로 표시돼야 할 게 503으로 가려질 수 있었다(Codex 3차 재리뷰로 발견,
        # 2026-07-24). 메시지도 호스트/스키마 같은 내부 정보를 그대로 노출하지 않도록 일반화한다.
        raise HTTPException(503, "DB 연결 오류 — 잠시 후 다시 시도하세요")


_reindex_pending = False  # 재색인 도중 새 CRUD 이벤트가 들어오면 이 플래그만 세우고, 현재
# 실행이 끝난 뒤 한 번 더 돈다 — 그냥 무시하면 그 이벤트가 스캔에 안 잡힌 채 다음 트리거가
# 올 때까지 색인이 안 될 수 있다(4번 "동시 재색인 경쟁조건", Codex 제안 반영). 지금은 항상
# 호출부가 한 번 결정한 provider 하나로만 재실행된다(아래 참고) — provider가 여러 개일 때
# 있었던 "범위 유실" 버그(2026-07-31 발견·해결)는 provider를 하나로 통일하면서 해소됨.


def _run_reindex_sync(provider: str) -> None:
    # 실제 작업+플래그 해제를 전부 워커 스레드 안에서 끝낸다 — 예전엔 reindex() coroutine의
    # finally에서 플래그를 풀었는데, 클라이언트 연결 끊김 등으로 그 coroutine이 cancel되면
    # asyncio.to_thread()로 넘어간 스레드는 안 멈추는데 플래그만 먼저 풀려서 새 요청이 겹쳐
    # 실행될 수 있었다(Codex 리뷰로 발견, 2026-07-29). 플래그 수명을 실제 동기 작업 전체와
    # 묶어야 cancel에도 안전하다.
    #
    # provider는 호출부가 시작 시점에 한 번 결정해서 넘긴다(재실행 때마다 다시 resolve하지
    # 않음) — 재색인 도중 설정이 바뀌는 경우는 이제 이 함수가 신경 쓸 일이 아니다. 설정
    # 변경(`update_rag_settings()`) 자체가 "새 provider로 먼저 재색인 → 성공해야 override
    # 커밋"이라는 자기 완결적 흐름이라, 이 함수가 도는 도중에 활성 provider가 바뀌는 일 자체가
    # 없다(Codex 재리뷰로 발견, 2026-07-31 — 응답 시점에 다시 resolve하면 실제로 처리 안 한
    # provider를 성공값으로 반환할 수 있는 race가 있었음).
    global _reindex_in_progress, _reindex_pending
    try:
        while True:
            rag_reindex.run(provider, settings.rag_include_profile)
            with _reindex_lock:
                if _reindex_pending:
                    # pending 상태에서 재실행 — diff 기반이라 이미 반영된 변경은 다시
                    # 스캔해도 비용이 거의 없다(변경 0건 감지, 2026-07-30 실측 확인).
                    _reindex_pending = False
                    continue
                # "pending 없음" 확인과 in_progress 해제를 같은 임계 구역 안에서 끝낸다 —
                # 둘을 분리하면(예전엔 여기서 break 후 finally가 따로 락을 다시 잡았음)
                # 그 사이 틈에 CRUD 훅이 pending=True를 세워도 곧장 덮어써 사라졌다
                # (Codex 3차 리뷰로 발견, 2026-08-02).
                _reindex_in_progress = False
                return
    except Exception:
        with _reindex_lock:
            _reindex_in_progress = False
            _reindex_pending = False
        raise


async def _run_reindex_or_503(provider: str) -> None:
    """`_run_reindex_sync`를 스레드로 돌리고, 실패를 일관된 HTTPException으로 변환한다.
    `/reindex`와 `update_rag_settings()`(provider 전환 시 선행 재색인) 둘 다 같은 처리가
    필요해서 공유한다."""
    try:
        await asyncio.to_thread(_run_reindex_sync, provider)
    except RuntimeError as e:
        raise HTTPException(503, f"RAG 파이프라인 연결/설정 오류: {e}")
    except httpx.HTTPError as e:
        raise HTTPException(503, f"임베딩 서버 통신 오류: {e}")
    except genai_errors.APIError as e:
        raise HTTPException(503, f"Google 임베딩 API 오류: {e}")
    except psycopg.OperationalError as e:
        raise HTTPException(503, "DB 연결 오류 — 잠시 후 다시 시도하세요")


def trigger_reindex_background() -> bool:
    """회사 CRUD 이벤트(공고 생성·재분석·삭제)에서 호출하는 자동 재색인 트리거(4번 항목).
    수동 트리거(`/reindex` 엔드포인트)와 달리 사용자에게 보여줄 응답이 없으므로, 이미
    진행 중이면 에러 대신 `_reindex_pending`만 세워서 조용히 뒤로 미룬다. RAG가 꺼져 있으면
    아무 일도 안 하고 즉시 False. 실패해도(임베딩 API 오류 등) CRUD 요청 자체는 이미 끝난
    뒤라 사용자에게 영향 없음 — 다음 트리거(수동/자동 무관) 때 diff 기반으로 자연히 재시도됨."""
    global _reindex_in_progress, _reindex_pending
    if not settings.rag_postgres_host:
        return False
    with _reindex_lock:
        if _reindex_in_progress:
            _reindex_pending = True
            return False
        _reindex_in_progress = True
    asyncio.create_task(asyncio.to_thread(_run_reindex_sync, resolve_rag_embedding_provider()))
    return True


@router.post("/reindex", dependencies=[Depends(_require_rag_enabled)])
async def reindex():
    """재색인 웹 트리거(2026-07-29). `rag.postgres.reindex.run()`이 지금까지 CLI 전용이라
    실제 사용자는 트리거할 방법이 없었다. 항상 `resolve_rag_embedding_provider()`가 결정한
    활성 provider 하나만 재색인한다(2026-07-31, provider별로 따로 고르던 구조를 없애고
    설정값 하나로 통일 — `/api/rag/settings` 참고). 프로필 포함 여부는
    `settings.rag_include_profile`(기본 false — 이력서 내용이 임베딩 API로 전송되는 걸
    사용자가 명시적으로 켜야 함)을 따른다. run()은 동기 함수(psycopg/httpx 동기 호출)라
    `asyncio.to_thread`로 감싸 이벤트 루프를 막지 않는다 — 출력은 그대로 컨테이너 stdout으로
    흘려보내 기존 `docker compose logs -f api` 디버깅 흐름을 유지한다. 수동 트리거라 이미
    진행 중이면(자동 트리거와 겹쳤을 수도 있음) 조용히 미루지 않고 409로 명확히 알린다 —
    사용자가 버튼을 눌렀는데 응답이 없으면 안 되므로."""
    global _reindex_in_progress
    with _reindex_lock:
        if _reindex_in_progress:
            raise HTTPException(409, "재색인이 이미 진행 중입니다. 잠시 후 다시 시도하세요.")
        _reindex_in_progress = True
    provider = resolve_rag_embedding_provider()  # 시작 시점에 한 번만 확정 — 응답도 이 값을 그대로 씀
    await _run_reindex_or_503(provider)
    return {"status": "ok", "provider": provider}


@router.post("/ask", dependencies=[Depends(_require_rag_enabled)])
async def ask(req: AskRequest):
    """대화형 근거 기반 RAG 진입점 — 자연어 질문을 Agent(tool-use)로 답한다(2026-07-28, Phase 1~4의
    "질문 분류→고정 함수 실행" 구조를 대체). 질문이 애매하거나 여러 능력을 조합해야 답할 수 있을 때
    완전히 무관한 답을 내놓는 구조적 결함이 실측으로 확인돼(`docs/rag-project-plans/00_meta/HISTORY.md`
    2026-07-28 항목), LLM이 도구를 스스로 골라 쓰며 답하는 구조로 전환. conn/embed_provider 생성·정리·
    예외 처리는 gap_check()와 동일한 패턴(Codex 리뷰로 다듬어진 부분이라 그대로 유지). DB
    연결·임베딩 호출이 동기라 이벤트 루프를 막는 문제도 gap_check()와 같은 이유로 함수 전체를
    asyncio.to_thread로 감싼다(Codex 3차 리뷰로 발견, 2026-08-02)."""
    provider = resolve_rag_embedding_provider()

    def _run_sync() -> dict:
        conn = None
        embed_provider = None
        try:
            conn = get_connection()
            embed_provider = GoogleEmbeddingProvider() if provider == "google" else LocalEmbeddingProvider()
            history = [m.model_dump() for m in req.history]
            result = asyncio.run(answer_query_agent(conn, req.question, embed_provider, history=history))
            result["provider"] = provider
            return result
        finally:
            close = getattr(embed_provider, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass
            if conn is not None:
                conn.close()

    try:
        return await asyncio.to_thread(_run_sync)
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
