"""
Anthropic Claude provider 구현.

구조화 추출: tool_choice={"type": "tool"} 로 특정 툴 강제 호출
스트리밍: messages.stream() 컨텍스트 매니저 사용
"""
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import anthropic

from services import usage_tracker
from config import settings
from .base import LLMAPIError, LLMProvider

logger = logging.getLogger(__name__)


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
        tool_defs = [
            anthropic.types.ToolParam(
                name=t["name"], description=t["description"], input_schema=t["input_schema"]
            )
            for t in tools
        ]
        messages: list[dict] = list(history or [])
        messages.append({"role": "user", "content": question})
        tool_calls_trace: list[dict] = []

        for _ in range(max_iterations):
            try:
                response = await self._client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=system,
                    messages=messages,  # type: ignore[arg-type]
                    tools=tool_defs,
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

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_use_blocks:
                text = "".join(b.text for b in response.content if hasattr(b, "text"))
                return {"text": text, "tool_calls": tool_calls_trace}

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in tool_use_blocks:
                is_error = False
                try:
                    result = await tool_executor(block.name, block.input)
                except (RuntimeError, LLMAPIError) as e:
                    # 도구 하나가 실패해도(예: 선택한 임베딩 provider로 프로필이 아직 색인 안 됨)
                    # 전체 요청을 죽이지 않는다 — 실패 사실을 Claude에게 tool_result로 알려서
                    # AGENT_SYSTEM의 "근거 부족하면 솔직히 말하세요" 지시대로 대응하게 한다.
                    # 이 두 예외 타입은 이미 사용자에게 보여줘도 안전한 메시지로 만들어져 있어
                    # 그대로 전달한다(예: "이 provider로 프로필이 아직 임베딩되지 않았습니다").
                    is_error = True
                    result = {"error": str(e)}
                except Exception as e:
                    # 예상 못 한 예외(SQL 오류 등)는 원문에 테이블명·쿼리 같은 내부 정보가 담길 수
                    # 있어 Claude/프론트 도구 트레이스에 그대로 노출하지 않는다(Codex 리뷰 2026-07-29
                    # 지적) — 서버 로그에만 전체를 남기고 도구에는 일반화된 메시지만 준다.
                    logger.exception("도구 실행 중 예상 못 한 오류 (%s)", block.name)
                    is_error = True
                    result = {"error": "내부 도구 실행 오류가 발생했습니다."}
                tool_calls_trace.append({"tool": block.name, "args": block.input, "result": result})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": tool_results})

        return {
            "text": "죄송합니다, 이 질문에 답하는 데 필요한 정보를 정리하지 못했습니다(도구 호출 반복 한도 초과).",
            "tool_calls": tool_calls_trace,
        }

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
