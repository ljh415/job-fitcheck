"""
LLM provider 추상 인터페이스.

모든 provider(Anthropic, OpenAI)는 이 클래스를 상속해 구현한다.
router.py가 이 인터페이스만 알고 있으므로 provider를 바꿔도 호출 코드가 변경되지 않는다.
"""
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class LLMAPIError(Exception):
    """LLM API 호출 실패 — 인증 오류·rate limit·서버 오류 등을 사용자 친화적 메시지로 래핑."""

    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


class LLMProvider(ABC):

    @abstractmethod
    async def extract_structured(
        self,
        system: str,
        user: str,
        tool_name: str,
        tool_description: str,
        tool_schema: dict,
        model: str,
        operation: str = "",
    ) -> dict:
        """Tool use / Function calling으로 구조화 JSON을 반환한다.
        Anthropic은 tool_use, OpenAI는 function calling으로 각각 구현."""
        ...

    @abstractmethod
    async def complete(
        self,
        system: str,
        user: str,
        model: str,
        operation: str = "",
        content: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> str:
        """단순 텍스트 완성 — 마크다운 본문 생성 등에 사용.
        content가 주어지면 user 대신 멀티모달 블록(텍스트+이미지)으로 전송."""
        ...

    @abstractmethod
    async def stream(
        self,
        system: str,
        messages: list[dict],
        model: str,
        operation: str = "",
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Q&A 스트리밍 응답 — SSE로 프론트에 청크 단위로 전달."""
        ...
