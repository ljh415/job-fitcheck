"""
FastAPI 앱 진입점.

각 API 도메인은 별도 모듈로 분리되어 있다:
  - auth.py              — 로그인, JWT, 인증 미들웨어
  - routers/settings.py  — 헬스체크, provider/모델 설정, 평가 기준, 사용량, 전체 export
  - routers/profile.py   — 후보자 프로필 (PDF 업로드 → LLM 추출)
  - routers/companies.py — 회사 CRUD, 회사 추가 파이프라인, 주간 요약
  - routers/qa.py        — Q&A (SSE 스트리밍)
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import auth
from config import ensure_dirs
from routers import companies, profile, qa, rag
from routers import settings as settings_router
from services.app_db import init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    try:
        init_db()
    except Exception as e:
        # 프로필 히스토리는 핵심 기능이 아니다 - DB 손상/권한 문제로 여기서 죽으면
        # 회사 CRUD 같은 핵심 기능까지 통째로 막힌다. 실패해도 앱은 정상 시작하고,
        # 이후 관련 API 호출은 각자 에러로 실패한다(프론트가 사용자에게 표시함).
        logger.error("프로필 히스토리 DB 초기화 실패 - 이 기능만 비활성화됩니다: %s", e)
    task = asyncio.create_task(companies.weekly_summary_loop())
    yield
    task.cancel()


app = FastAPI(title="Job FitCheck", version="0.1.0", lifespan=lifespan)

app.middleware("http")(auth.auth_middleware)

app.include_router(auth.router)
app.include_router(settings_router.router)
app.include_router(profile.router)
app.include_router(companies.router)
app.include_router(qa.router)
app.include_router(rag.router)  # opt-in 기능 — RAG_POSTGRES_HOST 미설정 시 각 엔드포인트가 503

# Docker 배포 시에는 nginx가 frontend/를 서빙하므로 이미지 안에 frontend/가 없다(Dockerfile 참고).
# uv 등으로 로컬에서 직접 실행할 때만 frontend/가 실제로 존재하므로, 있을 때만 마운트해
# 두 실행 방식 모두에서 안전하게 동작하도록 한다.
_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
