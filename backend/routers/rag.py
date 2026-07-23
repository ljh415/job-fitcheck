"""RAG 서브프로젝트(rag/main 전용) 테스트 화면용 API.

Plan B 6단계(`rag/postgres/gap.py`, `rag/postgres/answer.py`)를 그대로 호출한다 — 이 라우터는
얇은 wrapper일 뿐, 로직은 전부 rag/ 안에 있다. provider(google/local)를 요청마다 선택할 수
있게 해서, "provider 하나로 고정하지 않고 비교한다"는 Plan A 설계 원칙을 UI에서도 유지한다.
저장소는 PostgreSQL+pgvector 전용(SQLite `rag/gap.py`는 Plan A 기준선 재현용으로만 남아있고
이 서비스 경로에선 더 이상 안 씀 — Stage 6에서 전환).

main 브랜치엔 없는 라우터 — rag/main 전용.
"""
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from rag.answer import generate_action_plan
from rag.embed.google import GoogleEmbeddingProvider
from rag.embed.local import LocalEmbeddingProvider
from rag.postgres.db import get_connection
from rag.postgres.gap import assess_gap

router = APIRouter(prefix="/api/rag")


class GapCheckRequest(BaseModel):
    skill: str
    provider: str = "google"  # "google" | "local"


@router.post("/gap-check")
async def gap_check(req: GapCheckRequest):
    if req.provider not in ("google", "local"):
        raise HTTPException(400, "provider는 'google' 또는 'local'만 가능합니다")

    conn = get_connection()
    embed_provider = None
    try:
        # provider 생성도 try 안에서 해야 SSH 터널/모델 검증 실패(RuntimeError)가 503으로
        # 잡힌다 — 예전엔 try 밖에 있어서 이 실패가 그대로 500으로 샜다(Codex 리뷰로 발견,
        # 2026-07-23). embed_provider를 미리 None으로 둬서 생성 자체가 실패해도 finally의
        # getattr(None, "close", None)이 안전하게 아무 일도 안 하도록 한다.
        embed_provider = GoogleEmbeddingProvider() if req.provider == "google" else LocalEmbeddingProvider()
        gap_result = await assess_gap(conn, req.skill, embed_provider)
        action_plan = None
        if gap_result["evidence_level"] != "직접 근거":
            action_plan = await generate_action_plan(gap_result)
        return {
            "skill": gap_result["skill"],
            "evidence_level": gap_result["evidence_level"],
            "reasoning": gap_result["reasoning"],
            "market_demand": gap_result["market_demand"],
            "excerpts": gap_result["excerpts"],
            "action_plan": action_plan,
            "provider": req.provider,
        }
    except RuntimeError as e:
        # LocalEmbeddingProvider의 SSH 터널/모델 검증 실패, assess_gap()의 프로필 미임베딩 감지 등
        raise HTTPException(503, f"RAG 파이프라인 연결/설정 오류: {e}")
    except httpx.HTTPError as e:
        # provider 생성 후 실제 embed_documents()/embed_query() 호출 중 네트워크 오류 —
        # 이것도 503(일시적 연결 문제)이지 500(서버 버그)이 아니다(Codex 재리뷰로 발견, 2026-07-23)
        raise HTTPException(503, f"임베딩 서버 통신 오류: {e}")
    finally:
        close = getattr(embed_provider, "close", None)
        if close:
            close()
