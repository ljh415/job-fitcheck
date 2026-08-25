"""Job FitCheck MCP 서버 — Codex/Claude 같은 외부 AI가 회사·프로필·RAG 기능을 도구로 쓸 수
있게 한다. 전송은 HTTP/SSE(streamable HTTP), 인증은 `backend/main.py`의 기존 JWT Bearer
미들웨어를 그대로 재사용한다(`/api/mcp` 아래 마운트해서 별도 인증 코드를 만들지 않음).

설계 배경: docs/planning/mcp_plan_notes.md(로컬 전용), 진행 기록: docs/mcp-server/(로컬 전용).
"""
from mcp.server.mcpserver.server import MCPServer

from routers import rag

mcp = MCPServer(name="job-fitcheck")


@mcp.tool()
async def get_rag_status() -> dict:
    """RAG(대화형 근거 기반 검색) 활성화 여부와 설정을 확인한다."""
    return await rag.status()
