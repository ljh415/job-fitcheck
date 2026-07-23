"""RAG 서브프로젝트(rag/main 전용) 테스트 화면용 API.

Plan A(6~7단계) 파이프라인(`rag/gap.py`, `rag/answer.py`)을 그대로 호출한다 — 이 라우터는
얇은 wrapper일 뿐, 로직은 전부 rag/ 안에 있다. provider(google/local)를 요청마다 선택할 수
있게 해서, "provider 하나로 고정하지 않고 비교한다"는 Plan A 설계 원칙을 UI에서도 유지한다.

main 브랜치엔 없는 라우터 — rag/main 전용.
"""
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from rag.answer import generate_action_plan
from rag.embed.google import GoogleEmbeddingProvider
from rag.embed.local import LocalEmbeddingProvider
from rag.gap import assess_gap
from rag.ingest import DB_PATH

router = APIRouter(prefix="/api/rag")


class GapCheckRequest(BaseModel):
    skill: str
    provider: str = "google"  # "google" | "local"


@router.post("/gap-check")
async def gap_check(req: GapCheckRequest):
    if req.provider not in ("google", "local"):
        raise HTTPException(400, "provider는 'google' 또는 'local'만 가능합니다")

    conn = sqlite3.connect(DB_PATH)
    embed_provider = GoogleEmbeddingProvider() if req.provider == "google" else LocalEmbeddingProvider()
    try:
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
        # LocalEmbeddingProvider의 SSH 터널/모델 검증 실패 등 — 3050Ti가 안 켜져있으면 여기로 옴
        raise HTTPException(503, f"로컬 provider 연결 실패: {e}")
    finally:
        close = getattr(embed_provider, "close", None)
        if close:
            close()
