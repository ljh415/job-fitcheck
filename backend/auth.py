"""인증: JWT 발급/검증, 인증 미들웨어, 로그인 라우트."""
import hmac
import logging
from datetime import datetime, timezone, timedelta

import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from config import settings
from models import LoginRequest

logger = logging.getLogger(__name__)
router = APIRouter()

_JWT_ALGORITHM = "HS256"
_JWT_EXPIRE_DAYS = 30


def make_token() -> str:
    payload = {
        "sub": "user",
        "exp": datetime.now(timezone.utc) + timedelta(days=_JWT_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.app_secret, algorithm=_JWT_ALGORITHM)


def verify_token(token: str) -> bool:
    try:
        jwt.decode(token, settings.app_secret, algorithms=[_JWT_ALGORITHM])
        return True
    except jwt.PyJWTError:
        return False


# 인증 미들웨어 — APP_SECRET이 설정된 경우에만 적용
# 프론트엔드 정적 파일(/api/ 아닌 경로)은 원래 Docker에서 nginx가 FastAPI를 거치지 않고 직접
# 서빙해 인증 검사 대상이 아니었다. main.py가 정적 파일을 직접 서빙하는 경로(uv 등 로컬 실행)에서도
# 동일하게 동작하도록, API 경로(/api/*)만 인증 대상으로 제한한다. 로그인 게이트는 프론트엔드 JS가
# 담당하고, 실제 데이터 접근 권한은 /api/*에서 걸리므로 보안 경계는 그대로 유지된다.
_AUTH_SKIP = {"/api/login", "/api/health"}


async def auth_middleware(request: Request, call_next):
    if not settings.app_secret or not request.url.path.startswith("/api/") or request.url.path in _AUTH_SKIP:
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not verify_token(auth.split(" ", 1)[1]):
        return JSONResponse(status_code=401, content={"detail": "인증이 필요합니다."})
    return await call_next(request)


@router.post("/api/login")
async def login(req: LoginRequest):
    if not settings.app_secret:
        logger.info("로그인 성공 (APP_SECRET 미설정 — 개발 모드)")
        return {"token": "dev"}
    if not hmac.compare_digest(req.password.encode(), settings.app_secret.encode()):
        logger.warning("로그인 실패 — 비밀번호 불일치")
        raise HTTPException(status_code=401, detail="비밀번호가 틀렸습니다.")
    logger.info("로그인 성공")
    return {"token": make_token()}
