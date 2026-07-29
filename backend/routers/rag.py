"""RAG 서브프로젝트(rag/main 전용) 테스트 화면용 API.

Plan B 6단계(`rag/postgres/gap.py`, `rag/postgres/answer.py`)를 그대로 호출한다 — 이 라우터는
얇은 wrapper일 뿐, 로직은 전부 rag/ 안에 있다. provider(google/local)를 요청마다 선택할 수
있게 해서, "provider 하나로 고정하지 않고 비교한다"는 Plan A 설계 원칙을 UI에서도 유지한다.
저장소는 PostgreSQL+pgvector 전용(SQLite `rag/gap.py`는 Plan A 기준선 재현용으로만 남아있고
이 서비스 경로에선 더 이상 안 씀 — Stage 6에서 전환).

main 브랜치엔 없는 라우터 — rag/main 전용.
"""
import asyncio

import httpx
import psycopg
from fastapi import APIRouter, HTTPException
from google.genai import errors as genai_errors
from pydantic import BaseModel

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


class GapCheckRequest(BaseModel):
    skill: str
    provider: str = "google"  # "google" | "local"


class AskRequest(BaseModel):
    question: str
    provider: str = "google"  # "google" | "local" — 임베딩 provider(도구 내부 검색용)
    history: list[dict] = []  # [{"role": "user"|"assistant", "content": str}, ...] — 이전 대화 turn


@router.post("/gap-check")
async def gap_check(req: GapCheckRequest):
    if req.provider not in ("google", "local"):
        raise HTTPException(400, "provider는 'google' 또는 'local'만 가능합니다")

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
        embed_provider = GoogleEmbeddingProvider() if req.provider == "google" else LocalEmbeddingProvider()
        # 판정 LLM provider/model/reasoning_effort를 요청 시작 시 한 번만 캡처해 끝까지 그대로
        # 쓴다 — 안 그러면 assess_gap() 내부 시장수요 판정·근거 판정·행동계획 생성이 각각 따로
        # capture_snapshot()을 불러서, 요청 처리 도중 설정 화면에서 provider를 바꾸면 한 요청
        # 안에서 provider가 섞일 수 있다(Codex 리뷰 2026-07-29 지적, llm/router.py의
        # LLMSnapshot 설계 의도와도 일치).
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
            "provider": req.provider,
        }
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
    finally:
        # 두 자원을 독립적으로 정리한다 — embed_provider.close()(SSH 터널 종료 대기라 실패할 수
        # 있음)가 예외를 던지면 그다음 줄(conn.close())이 실행되지 않고 원래 예외까지 가려질 수
        # 있었다(Codex 재리뷰로 발견, 2026-07-23).
        close = getattr(embed_provider, "close", None)
        if close:
            try:
                close()
            except Exception:
                pass
        if conn is not None:
            conn.close()  # 명시적으로 닫아야 함 — GC(__del__)에 의존하면 idle in transaction 커넥션이 쌓일 수 있다(Codex 리뷰로 발견, 2026-07-23)


@router.post("/reindex")
async def reindex():
    """재색인 웹 트리거(2026-07-29) — `rag.postgres.reindex.run()`이 지금까지 CLI 전용이라
    실제 사용자는 트리거할 방법이 없었다. google+local 둘 다, 프로필 포함해서 대칭적으로
    실행한다 — Google API는 이미 유료 티어로 확정돼 있어(`project_gemini_key_switching` 메모리
    참고) "Google엔 프로필 기본 제외" 같은 안전장치가 불필요하다(자동 트리거는 안 함, 사용자가
    누를 때만 실행). run()은 동기 함수(psycopg/httpx 동기 호출)라 `asyncio.to_thread`로 감싸
    이벤트 루프를 막지 않는다 — 출력은 그대로 컨테이너 stdout으로 흘려보내 기존
    `docker compose logs -f api` 디버깅 흐름을 유지한다."""
    try:
        for provider_name in ("google", "local"):
            await asyncio.to_thread(rag_reindex.run, provider_name, True)
        return {"status": "ok"}
    except RuntimeError as e:
        raise HTTPException(503, f"RAG 파이프라인 연결/설정 오류: {e}")
    except httpx.HTTPError as e:
        raise HTTPException(503, f"임베딩 서버 통신 오류: {e}")
    except genai_errors.APIError as e:
        raise HTTPException(503, f"Google 임베딩 API 오류: {e}")
    except psycopg.OperationalError as e:
        raise HTTPException(503, "DB 연결 오류 — 잠시 후 다시 시도하세요")


@router.post("/ask")
async def ask(req: AskRequest):
    """대화형 근거 기반 RAG 진입점 — 자연어 질문을 Agent(tool-use)로 답한다(2026-07-28, Phase 1~4의
    "질문 분류→고정 함수 실행" 구조를 대체). 질문이 애매하거나 여러 능력을 조합해야 답할 수 있을 때
    완전히 무관한 답을 내놓는 구조적 결함이 실측으로 확인돼(`docs/rag-project-plans/00_meta/HISTORY.md`
    2026-07-28 항목), LLM이 도구를 스스로 골라 쓰며 답하는 구조로 전환. conn/embed_provider 생성·정리·
    예외 처리는 gap_check()와 동일한 패턴(Codex 리뷰로 다듬어진 부분이라 그대로 유지)."""
    if req.provider not in ("google", "local"):
        raise HTTPException(400, "provider는 'google' 또는 'local'만 가능합니다")

    conn = None
    embed_provider = None
    try:
        conn = get_connection()
        embed_provider = GoogleEmbeddingProvider() if req.provider == "google" else LocalEmbeddingProvider()
        result = await answer_query_agent(conn, req.question, embed_provider, history=req.history)
        result["provider"] = req.provider
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
    finally:
        close = getattr(embed_provider, "close", None)
        if close:
            try:
                close()
            except Exception:
                pass
        if conn is not None:
            conn.close()
