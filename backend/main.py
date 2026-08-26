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
from mcp.server.transport_security import TransportSecuritySettings

import auth
from config import ensure_dirs
from mcp_server import mcp as mcp_server
from routers import companies, profile, qa, rag
from routers import settings as settings_router
from services.app_db import init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# MCP streamable HTTP 앱은 자체 lifespan(세션 매니저 시작)을 갖고 있는데, FastAPI가 서브앱을
# mount()할 때 그 lifespan을 자동으로 물려받지 않는다 — 아래 앱 lifespan에서 명시적으로
# 합쳐야 세션 매니저가 실제로 시작된다("Task group is not initialized" 오류로 실측 확인).
#
# DNS 리바인딩 방지(Host/Origin 헤더 검사)는 기본으로 꺼둔다 — 브라우저가 임의 웹페이지에서
# localhost로 요청을 보내는 공격을 막는 기능인데, MCP 클라이언트(Codex/Claude)는 브라우저가
# 아니라 서버 간 호출이고 이미 JWT Bearer 인증이 진짜 보안 경계다. nginx가 `$host`(포트 없는
# 호스트명)를 그대로 넘겨서 기본 허용목록("localhost:*" 등 포트 필수 패턴)과 안 맞아 421로
# 막히는 문제도 실측 확인 — 원격 클라이언트 지원 결정과도 안 맞는 로컬 전용 허용목록이라
# 끄는 게 맞다.
_mcp_app = mcp_server.streamable_http_app(
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    # 프로필 히스토리는 핵심 기능이 아니다 - init_db()는 내부에서 실패를 흡수하고
    # is_healthy()에 남긴다(app_db.py). DB 손상/권한 문제로 회사 CRUD 같은 핵심
    # 기능까지 막히면 안 되기 때문 - 실패해도 앱은 정상 시작한다.
    init_db()
    task = asyncio.create_task(companies.weekly_summary_loop())
    async with _mcp_app.router.lifespan_context(_mcp_app):
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
# /api/ 접두사라 auth_middleware가 그대로 커버 — MCP 전용 인증 코드를 따로 안 만듦
app.mount("/api/mcp", _mcp_app)

# Docker 배포 시에는 nginx가 frontend/를 서빙하므로 이미지 안에 frontend/가 없다(Dockerfile 참고).
# uv 등으로 로컬에서 직접 실행할 때만 frontend/가 실제로 존재하므로, 있을 때만 마운트해
# 두 실행 방식 모두에서 안전하게 동작하도록 한다.
_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
