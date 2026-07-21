"""
Anthropic Claude provider 구현.

구조화 추출: tool_choice={"type": "tool"} 로 특정 툴 강제 호출
스트리밍: messages.stream() 컨텍스트 매니저 사용
"""
from collections.abc import AsyncIterator

import anthropic

from services import usage_tracker
from config import settings
from .base import LLMAPIError, LLMProvider


class AnthropicProvider(LLMProvider):

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

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
        reasoning_effort: str | None = None,  # ponytail: OpenAI 전용, Anthropic은 미사용. ABC 시그니처 일치용
    ) -> dict:
        tool_def = anthropic.types.ToolParam(
            name=tool_name,
            description=tool_description,
            input_schema=tool_schema,  # type: ignore[arg-type]
        )
        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=0.5,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool_def],
                # 특정 툴을 반드시 호출하도록 강제 — 텍스트 응답을 방지
                tool_choice={"type": "tool", "name": tool_name},
            )
        except anthropic.AuthenticationError:
            raise LLMAPIError("LLM API 인증 실패 — 설정에서 Anthropic API 키를 확인해주세요.", 401)
        except anthropic.RateLimitError:
            raise LLMAPIError("LLM API 요청 한도 초과 — 잠시 후 다시 시도해주세요.", 429)
        except anthropic.APIStatusError as e:
            raise LLMAPIError(f"LLM 서비스 오류 ({e.status_code}) — 잠시 후 다시 시도해주세요.", 503)
        except anthropic.APIConnectionError:
            raise LLMAPIError("LLM 서비스 연결 실패 — 네트워크 상태를 확인해주세요.", 503)
        usage_tracker.append_usage(
            operation=operation,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        if response.stop_reason == "max_tokens":
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "[%s] tool call 응답이 max_tokens(%d)에 의해 잘렸습니다. 일부 필드가 누락될 수 있습니다.",
                operation, max_tokens,
            )
        for block in response.content:
            if block.type == "tool_use" and block.name == tool_name:
                return dict(block.input)  # type: ignore[arg-type]
        raise RuntimeError("Tool use block not found in response")

    async def complete(
        self,
        system: str,
        user: str,
        model: str,
        operation: str = "",
        content: list[dict] | None = None,
        max_tokens: int = 4096,
        reasoning_effort: str | None = None,  # ponytail: OpenAI 전용, Anthropic은 미사용. ABC 시그니처 일치용
    ) -> str:
        msg_content: str | list = content if content is not None else user
        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": msg_content}],  # type: ignore[arg-type]
            )
        except anthropic.AuthenticationError:
            raise LLMAPIError("LLM API 인증 실패 — 설정에서 Anthropic API 키를 확인해주세요.", 401)
        except anthropic.RateLimitError:
            raise LLMAPIError("LLM API 요청 한도 초과 — 잠시 후 다시 시도해주세요.", 429)
        except anthropic.APIStatusError as e:
            raise LLMAPIError(f"LLM 서비스 오류 ({e.status_code}) — 잠시 후 다시 시도해주세요.", 503)
        except anthropic.APIConnectionError:
            raise LLMAPIError("LLM 서비스 연결 실패 — 네트워크 상태를 확인해주세요.", 503)
        usage_tracker.append_usage(
            operation=operation,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        stop_reason = response.stop_reason
        if stop_reason == "max_tokens":
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "[%s] 응답이 max_tokens(%d)에 의해 잘렸습니다 (출력 %d토큰). 내용이 불완전할 수 있습니다.",
                operation, max_tokens, response.usage.output_tokens,
            )
        for block in response.content:
            if hasattr(block, "text"):
                return block.text  # type: ignore[union-attr]
        return ""

    async def stream(
        self,
        system: str,
        messages: list[dict],
        model: str,
        operation: str = "",
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        try:
            async with self._client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,  # type: ignore[arg-type]
            ) as s:
                async for text in s.text_stream:
                    yield text
                msg = await s.get_final_message()
                usage_tracker.append_usage(
                    operation=operation,
                    model=model,
                    input_tokens=msg.usage.input_tokens,
                    output_tokens=msg.usage.output_tokens,
                )
        except anthropic.AuthenticationError:
            raise LLMAPIError("LLM API 인증 실패 — 설정에서 Anthropic API 키를 확인해주세요.", 401)
        except anthropic.RateLimitError:
            raise LLMAPIError("LLM API 요청 한도 초과 — 잠시 후 다시 시도해주세요.", 429)
        except anthropic.APIStatusError as e:
            raise LLMAPIError(f"LLM 서비스 오류 ({e.status_code}) — 잠시 후 다시 시도해주세요.", 503)
        except anthropic.APIConnectionError:
            raise LLMAPIError("LLM 서비스 연결 실패 — 네트워크 상태를 확인해주세요.", 503)
