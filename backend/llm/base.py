"""
LLM provider 추상 인터페이스.

모든 provider(Anthropic, OpenAI)는 이 클래스를 상속해 구현한다.
router.py가 이 인터페이스만 알고 있으므로 provider를 바꿔도 호출 코드가 변경되지 않는다.
"""
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


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
        max_tokens: int = 8192,
        reasoning_effort: str | None = None,
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
        reasoning_effort: str | None = None,
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

    async def run_agent(
        self,
        system: str,
        question: str,
        tools: list[dict],
        tool_executor: Callable[[str, dict], Awaitable[Any]],
        model: str,
        operation: str = "",
        history: list[dict] | None = None,
        max_iterations: int = 6,
    ) -> dict:
        """여러 도구를 노출하고 LLM이 스스로 어떤 도구를(몇 개든, 안 쓰든) 호출할지 판단하며
        답하게 하는 ReAct 스타일 에이전트 루프. `extract_structured()`(특정 도구 강제 호출 1회)와
        달리, 도구 선택 자체를 LLM에 맡기고 도구 결과를 관찰한 뒤 다음 행동을 반복 판단한다.

        Claude/Gemini/OpenAI 셋 다 구현돼 있다. 기본 구현은 미지원 — 새 provider가 추가되면
        재정의해야 한다."""
        raise NotImplementedError(f"{type(self).__name__}은 아직 tool-use 에이전트 루프를 지원하지 않습니다.")
