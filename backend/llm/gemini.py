"""
Google Gemini provider 구현.

구조화 추출: function_calling (mode=ANY) 으로 특정 함수 강제 호출
스트리밍: generate_content_stream 사용
"""
import asyncio
import base64
import logging
from collections.abc import AsyncIterator
from typing import NoReturn

from google import genai
from google.genai import types

import usage_tracker
from config import settings
from .base import LLMAPIError, LLMProvider

logger = logging.getLogger(__name__)

_TYPE_MAP = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


def _to_gemini_schema(schema: dict) -> dict:
    """JSON Schema → Gemini Schema 변환.

    Gemini는 타입명 대문자를 요구하며, ["string", "null"] union을 지원하지 않는다.
    union 타입은 nullable: true + 단일 타입으로 변환한다.
    """
    if not isinstance(schema, dict):
        return schema
    result = {}
    for key, value in schema.items():
        if key == "type":
            if isinstance(value, list):
                non_null = [t for t in value if t != "null"]
                t = non_null[0] if non_null else "string"
                result["type"] = _TYPE_MAP.get(t, t.upper())
                result["nullable"] = True
            else:
                result["type"] = _TYPE_MAP.get(value, value.upper())
        elif key == "properties":
            result["properties"] = {k: _to_gemini_schema(v) for k, v in value.items()}
        elif key == "items":
            result["items"] = _to_gemini_schema(value)
        elif key == "anyOf":
            non_null = [s for s in value if s.get("type") != "null"]
            if non_null:
                result.update(_to_gemini_schema(non_null[0]))
                result["nullable"] = True
        else:
            result[key] = value
    return result


class GeminiProvider(LLMProvider):

    def __init__(self) -> None:
        self._client = genai.Client(api_key=settings.google_api_key)

    @staticmethod
    def _to_parts(blocks: list[dict]) -> list[types.Part]:
        """Anthropic content block 형식을 Gemini Part 목록으로 변환."""
        parts = []
        for b in blocks:
            if b.get("type") == "image":
                src = b["source"]
                parts.append(types.Part.from_bytes(
                    data=base64.b64decode(src["data"]),
                    mime_type=src["media_type"],
                ))
            elif b.get("type") == "text":
                parts.append(types.Part.from_text(text=b["text"]))
        return parts

    def _raise(self, e: Exception) -> NoReturn:
        logger.exception("Gemini API 오류: %s", e)
        msg = str(e)
        code = getattr(e, "status_code", None)
        if code in (401, 403) or "API_KEY_INVALID" in msg or "PERMISSION_DENIED" in msg:
            raise LLMAPIError("LLM API 인증 실패 — 설정에서 Google API 키를 확인해주세요.", 401)
        if code == 429 or "RESOURCE_EXHAUSTED" in msg:
            raise LLMAPIError("LLM API 요청 한도 초과 — 잠시 후 다시 시도해주세요.", 429)
        raise LLMAPIError("LLM 서비스 오류 — 잠시 후 다시 시도해주세요.", 503)

    @staticmethod
    def _is_retryable(e: Exception) -> bool:
        msg = str(e)
        code = getattr(e, "status_code", None)
        return code == 503 or "UNAVAILABLE" in msg or "high demand" in msg

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
    ) -> dict:
        tool = types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=tool_name,
                    description=tool_description,
                    parameters=_to_gemini_schema(tool_schema),
                )
            ]
        )
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[tool],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="ANY",
                    allowed_function_names=[tool_name],
                )
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=max_tokens,
            temperature=0.3,
        )
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.aio.models.generate_content(
                    model=model,
                    contents=[types.Content(role="user", parts=[types.Part.from_text(text=user)])],
                    config=config,
                )
                break
            except Exception as e:
                last_exc = e
                if self._is_retryable(e) and attempt < 2:
                    wait = 5 * (attempt + 1)
                    logger.warning("Gemini 503 재시도 %d/2 (%d초 대기)", attempt + 1, wait)
                    await asyncio.sleep(wait)
                else:
                    self._raise(e)
        else:
            self._raise(last_exc)

        if response.usage_metadata:
            usage_tracker.append_usage(
                operation=operation,
                model=model,
                input_tokens=response.usage_metadata.prompt_token_count or 0,
                output_tokens=response.usage_metadata.candidates_token_count or 0,
            )

        candidates = response.candidates or []
        if not candidates:
            raise RuntimeError("Gemini 응답에 candidates가 없습니다")
        candidate = candidates[0]
        finish_reason = getattr(candidate.finish_reason, "name", str(candidate.finish_reason))
        if candidate.content is None:
            logger.error("Gemini content=None (finish_reason=%s)", finish_reason)
            raise LLMAPIError(f"Gemini 응답이 차단되었습니다 (finish_reason={finish_reason})", 503)
        for part in candidate.content.parts:
            if part.function_call:
                return dict(part.function_call.args)
        raise RuntimeError(f"Gemini function call 없음 (finish_reason={finish_reason})")

    async def complete(
        self,
        system: str,
        user: str,
        model: str,
        operation: str = "",
        content: list[dict] | None = None,
        max_tokens: int = 4096,
    ) -> str:
        parts = self._to_parts(content) if content is not None else [types.Part.from_text(text=user)]
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
        )
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.aio.models.generate_content(
                    model=model,
                    contents=[types.Content(role="user", parts=parts)],
                    config=config,
                )
                break
            except Exception as e:
                last_exc = e
                if self._is_retryable(e) and attempt < 2:
                    wait = 5 * (attempt + 1)
                    logger.warning("Gemini 503 재시도 %d/2 (%d초 대기)", attempt + 1, wait)
                    await asyncio.sleep(wait)
                else:
                    self._raise(e)
        else:
            self._raise(last_exc)

        if response.usage_metadata:
            candidates = response.candidates or []
            if candidates and getattr(candidates[0].finish_reason, "name", None) == "MAX_TOKENS":
                logger.warning(
                    "[%s] 응답이 max_tokens(%d)에 의해 잘렸습니다. 내용이 불완전할 수 있습니다.",
                    operation, max_tokens,
                )
            usage_tracker.append_usage(
                operation=operation,
                model=model,
                input_tokens=response.usage_metadata.prompt_token_count or 0,
                output_tokens=response.usage_metadata.candidates_token_count or 0,
            )
        return response.text or ""

    async def stream(
        self,
        system: str,
        messages: list[dict],
        model: str,
        operation: str = "",
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        contents = []
        for msg in messages:
            role = "model" if msg["role"] == "assistant" else "user"
            raw = msg.get("content")
            if isinstance(raw, list):
                parts = self._to_parts([{k: v for k, v in b.items() if k != "cache_control"} for b in raw])
            else:
                parts = [types.Part.from_text(text=str(raw))]
            contents.append(types.Content(role=role, parts=parts))

        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
        )
        input_tokens = 0
        output_tokens = 0
        last_exc: Exception | None = None
        yielded_any = False
        for attempt in range(3):
            try:
                stream_resp = await self._client.aio.models.generate_content_stream(
                    model=model,
                    contents=contents,
                    config=config,
                )
                async for chunk in stream_resp:
                    if chunk.usage_metadata:
                        input_tokens = chunk.usage_metadata.prompt_token_count or 0
                        output_tokens = chunk.usage_metadata.candidates_token_count or 0
                    if chunk.text:
                        yielded_any = True
                        yield chunk.text
                break
            except LLMAPIError:
                raise
            except Exception as e:
                last_exc = e
                # 이미 청크를 출력한 뒤라면 처음부터 재시도할 경우 응답이 중복 출력되므로,
                # 첫 청크 이전 실패에 대해서만 재시도한다.
                if not yielded_any and self._is_retryable(e) and attempt < 2:
                    wait = 5 * (attempt + 1)
                    logger.warning("Gemini 503 재시도 %d/2 (%d초 대기)", attempt + 1, wait)
                    await asyncio.sleep(wait)
                else:
                    self._raise(e)
        else:
            self._raise(last_exc)
        if input_tokens or output_tokens:
            usage_tracker.append_usage(
                operation=operation,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
