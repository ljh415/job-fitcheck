"""
OpenAI ChatGPT provider 구현.

구조화 추출: tool_choice={"type": "function"} 로 특정 함수 강제 호출
스트리밍: stream=True + async context manager 사용
"""
import json
from collections.abc import AsyncIterator

import openai as openai_lib
from openai import AsyncOpenAI

import usage_tracker
from config import settings
from .base import LLMAPIError, LLMProvider


class OpenAIProvider(LLMProvider):

    def __init__(self) -> None:
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

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
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "description": tool_description,
                            "parameters": tool_schema,
                        },
                    }
                ],
                # 특정 함수를 반드시 호출하도록 강제
                tool_choice={"type": "function", "function": {"name": tool_name}},
            )
        except openai_lib.AuthenticationError:
            raise LLMAPIError("LLM API 인증 실패 — 설정에서 OpenAI API 키를 확인해주세요.", 401)
        except openai_lib.RateLimitError:
            raise LLMAPIError("LLM API 요청 한도 초과 — 잠시 후 다시 시도해주세요.", 429)
        except openai_lib.APIStatusError as e:
            raise LLMAPIError(f"LLM 서비스 오류 ({e.status_code}) — 잠시 후 다시 시도해주세요.", 503)
        except openai_lib.APIConnectionError:
            raise LLMAPIError("LLM 서비스 연결 실패 — 네트워크 상태를 확인해주세요.", 503)
        if response.usage:
            usage_tracker.append_usage(
                operation=operation,
                model=model,
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            )
        tool_call = response.choices[0].message.tool_calls
        if not tool_call:
            raise RuntimeError("No function call returned")
        return json.loads(tool_call[0].function.arguments)

    @staticmethod
    def _to_openai_content(blocks: list[dict]) -> list[dict]:
        """Anthropic content block 형식을 OpenAI 형식으로 변환."""
        result = []
        for b in blocks:
            if b.get("type") == "image":
                src = b["source"]
                url = f"data:{src['media_type']};base64,{src['data']}"
                result.append({"type": "image_url", "image_url": {"url": url, "detail": "high"}})
            elif b.get("type") == "text":
                result.append({"type": "text", "text": b["text"]})
        return result

    async def complete(
        self,
        system: str,
        user: str,
        model: str,
        operation: str = "",
        content: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> str:
        msg_content = self._to_openai_content(content) if content is not None else user
        try:
            response = await self._client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": msg_content},  # type: ignore[arg-type]
                ],
            )
        except openai_lib.AuthenticationError:
            raise LLMAPIError("LLM API 인증 실패 — 설정에서 OpenAI API 키를 확인해주세요.", 401)
        except openai_lib.RateLimitError:
            raise LLMAPIError("LLM API 요청 한도 초과 — 잠시 후 다시 시도해주세요.", 429)
        except openai_lib.APIStatusError as e:
            raise LLMAPIError(f"LLM 서비스 오류 ({e.status_code}) — 잠시 후 다시 시도해주세요.", 503)
        except openai_lib.APIConnectionError:
            raise LLMAPIError("LLM 서비스 연결 실패 — 네트워크 상태를 확인해주세요.", 503)
        if response.usage:
            usage_tracker.append_usage(
                operation=operation,
                model=model,
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
            )
        finish_reason = response.choices[0].finish_reason
        if finish_reason == "length":
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "[%s] 응답이 max_tokens(%d)에 의해 잘렸습니다 (출력 %d토큰). 내용이 불완전할 수 있습니다.",
                operation, max_tokens, response.usage.completion_tokens if response.usage else -1,
            )
        return response.choices[0].message.content or ""

    async def stream(
        self,
        system: str,
        messages: list[dict],
        model: str,
        operation: str = "",
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        # OpenAI는 system을 messages 배열 첫 번째 항목으로 넣어야 함
        # content block 형태일 때 Anthropic 전용 cache_control 필드 제거
        cleaned = []
        for msg in messages:
            if isinstance(msg.get("content"), list):
                content = [{k: v for k, v in b.items() if k != "cache_control"} for b in msg["content"]]
                cleaned.append({**msg, "content": content})
            else:
                cleaned.append(msg)
        full_messages = [{"role": "system", "content": system}, *cleaned]
        input_tokens = 0
        output_tokens = 0
        try:
            async with await self._client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=full_messages,  # type: ignore[arg-type]
                stream=True,
                stream_options={"include_usage": True},
            ) as s:
                async for chunk in s:
                    if chunk.usage:
                        input_tokens = chunk.usage.prompt_tokens
                        output_tokens = chunk.usage.completion_tokens
                    delta = chunk.choices[0].delta.content if chunk.choices else None
                    if delta:
                        yield delta
        except openai_lib.AuthenticationError:
            raise LLMAPIError("LLM API 인증 실패 — 설정에서 OpenAI API 키를 확인해주세요.", 401)
        except openai_lib.RateLimitError:
            raise LLMAPIError("LLM API 요청 한도 초과 — 잠시 후 다시 시도해주세요.", 429)
        except openai_lib.APIStatusError as e:
            raise LLMAPIError(f"LLM 서비스 오류 ({e.status_code}) — 잠시 후 다시 시도해주세요.", 503)
        except openai_lib.APIConnectionError:
            raise LLMAPIError("LLM 서비스 연결 실패 — 네트워크 상태를 확인해주세요.", 503)
        if input_tokens or output_tokens:
            usage_tracker.append_usage(
                operation=operation,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
