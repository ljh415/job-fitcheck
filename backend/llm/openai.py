"""
OpenAI ChatGPT provider 구현.

구조화 추출: tool_choice={"type": "function"} 로 특정 함수 강제 호출
스트리밍: stream=True + async context manager 사용

모델별 경로: gpt-6 계열은 Responses API(`_*_responses` 메서드), 그 밖의 모델은 chat/completions.
gpt-6 계열은 chat/completions에서 max_tokens·temperature를 거부하고 함수 도구와 reasoning을 같이 쓸 수 없다.
Responses 경로는 요청을 OpenAI 서버에 저장하지 않고(store=False), 함수 호출이 없거나 정상 완료가 아닌
응답은 다시 요청하며 실패한 시도를 llm/incidents.py로 기록한다.
"""
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import openai as openai_lib
from openai import AsyncOpenAI

from services import usage_tracker
from config import get_reasoning_effort, settings
from . import incidents
from .base import LLMAPIError, LLMProvider

logger = logging.getLogger(__name__)


def _max_tokens_kwarg(model: str, max_tokens: int) -> dict:
    """gpt-5.x / o-series 모델은 max_completion_tokens를 요구한다."""
    if any(model.startswith(p) for p in ("gpt-5", "o1", "o3", "o4")):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


# temperature 기본값(1)만 지원하는 모델 — 0.5 등 지정 시 400 오류
_NO_TEMPERATURE_MODELS = frozenset({
    "gpt-5", "gpt-5-nano", "gpt-5-mini",
    "gpt-5.5", "gpt-5.5-pro",
})


def _reasoning_effort_kwarg(model: str, reasoning_effort: str | None = None) -> dict:
    """gpt-5 계열(하이픈/점 모두) 및 o-series 모델에 reasoning_effort를 적용한다.
    gpt-5-mini/nano는 low/medium에서 reasoning_tokens=0 (파라미터 수용, 추론 미작동),
    high에서는 실제 추론이 활성화된다.
    gpt-4o 계열은 400 오류가 발생하므로 제외.
    reasoning_effort가 주어지면(파이프라인 스냅샷) 그 값을 쓰고, 없으면 현재 설정을 조회한다."""
    if not (model.startswith("gpt-5") or model.startswith("o1") or model.startswith("o3") or model.startswith("o4")):
        return {}
    return {"extra_body": {"reasoning_effort": reasoning_effort or get_reasoning_effort()}}


_RESPONSES_MODEL_PREFIXES = ("gpt-6",)
# 설정 화면에서 고를 수 있는 OpenAI 모델 = 시험을 마친 모델. 새 모델은 시험 후 여기에 추가한다.
# 의료 특화(-healthcare) 모델은 일반 용도가 아니라 뺀다.
ALLOWED_OPENAI_HIGH_MODELS = frozenset({
    "gpt-6.2-sol", "gpt-6.1-sol", "gpt-6-sol", "gpt-6.2-astra", "gpt-6-astra",
})
ALLOWED_OPENAI_LIGHT_MODELS = frozenset({"gpt-6-luna", "gpt-6.1-terra"})
ALLOWED_OPENAI_MODELS = ALLOWED_OPENAI_HIGH_MODELS | ALLOWED_OPENAI_LIGHT_MODELS
_RESPONSES_MAX_ATTEMPTS = 3  # 첫 시도 + 재시도 2회
# 출력 상한(max_output_tokens)에는 reasoning 토큰이 포함되므로 낮게 잡으면 보이는 출력 전에 끝나 응답이 빈다.
_RESPONSES_MIN_OUTPUT_TOKENS = 16384
_RESPONSES_OUTPUT_TOKENS_CAP = 32768


def _uses_responses(model: str) -> bool:
    return model.startswith(_RESPONSES_MODEL_PREFIXES)


def _as_llm_error(e: Exception) -> LLMAPIError | None:
    """OpenAI SDK 예외를 사용자용 LLMAPIError로 바꾼다. 해당하지 않는 예외는 None."""
    if isinstance(e, openai_lib.AuthenticationError):
        return LLMAPIError("LLM API 인증 실패 — 설정에서 OpenAI API 키를 확인해주세요.", 401)
    if isinstance(e, openai_lib.RateLimitError):
        return LLMAPIError("LLM API 요청 한도 초과 — 잠시 후 다시 시도해주세요.", 429)
    if isinstance(e, openai_lib.APIStatusError):
        return LLMAPIError(f"LLM 서비스 오류 ({e.status_code}) — 잠시 후 다시 시도해주세요.", 503)
    if isinstance(e, openai_lib.APIConnectionError):
        return LLMAPIError("LLM 서비스 연결 실패 — 네트워크 상태를 확인해주세요.", 503)
    return None


def _to_responses_content(blocks: list[dict], role: str = "user") -> list[dict]:
    """Anthropic 형식 content block을 Responses 입력 형식으로 바꾼다. 어시스턴트 글은 output_text 형식이다."""
    text_type = "output_text" if role == "assistant" else "input_text"
    parts = []
    for b in blocks:
        if b.get("type") == "image":
            src = b["source"]
            parts.append({"type": "input_image", "image_url": f"data:{src['media_type']};base64,{src['data']}", "detail": "high"})
        elif b.get("type") == "text":
            parts.append({"type": text_type, "text": b["text"]})
    return parts


def _messages_to_responses(messages: list[dict]) -> list[dict]:
    """대화 이력을 Responses 입력 항목으로 바꾼다. Anthropic 전용 cache_control 필드는 보내지 않는다."""
    items = []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, list):
            content = _to_responses_content([{k: v for k, v in b.items() if k != "cache_control"} for b in content], msg["role"])
        items.append({"role": msg["role"], "content": content})
    return items


def _input_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    return sum(len(b.get("text", "")) for b in value or [] if isinstance(b, dict))


def _hit_output_limit(response: Any) -> bool:
    return getattr(getattr(response, "incomplete_details", None), "reason", None) == "max_output_tokens"


def _responses_kwargs(
    model: str, system: str, input_items: Any, reasoning_effort: str, max_output_tokens: int,
    tools: list[dict] | None, tool_choice: dict | None,
) -> dict[str, Any]:
    """responses.create에 넘기는 인자. 실제 호출과 이상 응답 기록(요청 본문 보관)이 같은 값을 쓰게 한 곳에서 만든다."""
    kwargs: dict[str, Any] = {
        "model": model, "instructions": system, "input": input_items, "store": False,
        "reasoning": {"effort": reasoning_effort}, "max_output_tokens": max_output_tokens,
    }
    if tools:
        kwargs["tools"] = tools
    if tool_choice:
        kwargs["tool_choice"] = tool_choice
    return kwargs


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
        max_tokens: int = 8192,
        reasoning_effort: str | None = None,
    ) -> dict:
        if _uses_responses(model):
            return await self._extract_structured_responses(
                system, user, tool_name, tool_description, tool_schema, model, operation, max_tokens, reasoning_effort,
            )
        temperature_kwarg = {} if model in _NO_TEMPERATURE_MODELS else {"temperature": 0.5}
        reasoning_kwarg = _reasoning_effort_kwarg(model, reasoning_effort)
        # reasoning 모델은 max_completion_tokens 한도를 충분히 줘야 reasoning 후 출력 가능
        if reasoning_kwarg:
            max_tokens = max(max_tokens, 16384)
        try:
            with usage_tracker.timer() as t:
                response = await self._client.chat.completions.create(
                    model=model,
                    **temperature_kwarg,
                    **_max_tokens_kwarg(model, max_tokens),
                    **reasoning_kwarg,
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
                duration_ms=t["ms"],
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
        reasoning_effort: str | None = None,
    ) -> str:
        if _uses_responses(model):
            return await self._complete_responses(system, user, model, operation, content, max_tokens, reasoning_effort)
        msg_content = self._to_openai_content(content) if content is not None else user
        reasoning_kwarg = _reasoning_effort_kwarg(model, reasoning_effort)
        if reasoning_kwarg:
            max_tokens = max(max_tokens, 8192)
        import logging as _logging
        logger = _logging.getLogger(__name__)
        current_max_tokens = max_tokens
        # 잘린 응답을 그대로 반환하는 대신, max_tokens를 2배로 늘려 한 번 더 시도한다
        # (32768 도달 시 더 늘려도 소용없으니 중단) — 사용자가 직접 재시도하지 않아도 되게 함.
        for attempt in range(2):
            try:
                with usage_tracker.timer() as t:
                    response = await self._client.chat.completions.create(
                        model=model,
                        **_max_tokens_kwarg(model, current_max_tokens),
                        **reasoning_kwarg,
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
                    duration_ms=t["ms"],
                )
            finish_reason = response.choices[0].finish_reason
            if finish_reason == "length" and attempt == 0 and current_max_tokens < 32768:
                retry_max_tokens = min(current_max_tokens * 2, 32768)
                logger.info(
                    "[%s] max_tokens(%d)에 잘림 — %d로 늘려 재시도",
                    operation, current_max_tokens, retry_max_tokens,
                )
                current_max_tokens = retry_max_tokens
                continue
            if finish_reason == "length":
                logger.warning(
                    "[%s] 응답이 max_tokens(%d)에 의해 잘렸습니다(재시도 후에도 부족, 출력 %d토큰). 내용이 불완전할 수 있습니다.",
                    operation, current_max_tokens, response.usage.completion_tokens if response.usage else -1,
                )
            return response.choices[0].message.content or ""

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
        if _uses_responses(model):
            return await self._run_agent_responses(
                system, question, tools, tool_executor, model, operation, history, max_iterations,
            )
        tool_defs = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]
        messages: list[dict] = [{"role": "system", "content": system}, *(history or [])]
        messages.append({"role": "user", "content": question})
        tool_calls_trace: list[dict] = []

        temperature_kwarg = {} if model in _NO_TEMPERATURE_MODELS else {"temperature": 0.5}
        reasoning_kwarg = _reasoning_effort_kwarg(model)
        max_tokens = 16384 if reasoning_kwarg else 4096

        for _ in range(max_iterations):
            try:
                with usage_tracker.timer() as t:
                    response = await self._client.chat.completions.create(
                        model=model,
                        **temperature_kwarg,
                        **_max_tokens_kwarg(model, max_tokens),
                        **reasoning_kwarg,
                        messages=messages,  # type: ignore[arg-type]
                        tools=tool_defs,
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
                    duration_ms=t["ms"],
                )

            message = response.choices[0].message
            if not message.tool_calls:
                return {"text": message.content or "", "tool_calls": tool_calls_trace}

            messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in message.tool_calls
                ],
            })
            for tc in message.tool_calls:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                try:
                    result = await tool_executor(tc.function.name, args)
                except (RuntimeError, LLMAPIError) as e:
                    # Claude provider(llm/anthropic.py)의 run_agent()와 동일한 원칙 —
                    # 도구 하나 실패로 전체 요청을 죽이지 않고, 이미 사용자 노출 안전한
                    # 메시지를 tool 결과로 모델에 돌려줘서 스스로 대응하게 한다.
                    result = {"error": str(e)}
                except Exception:
                    logger.exception("도구 실행 중 예상 못 한 오류 (%s)", tc.function.name)
                    result = {"error": "내부 도구 실행 오류가 발생했습니다."}
                tool_calls_trace.append({"tool": tc.function.name, "args": args, "result": result})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })

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
        if _uses_responses(model):
            async for chunk in self._stream_responses(system, messages, model, operation, max_tokens):
                yield chunk
            return
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
            with usage_tracker.timer() as t:
                async with await self._client.chat.completions.create(
                    model=model,
                    **_max_tokens_kwarg(model, max_tokens),
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
                duration_ms=t["ms"],
            )

    # ── Responses API 경로 (gpt-6 계열) ─────────────────────────────────────────

    async def _responses_call(
        self, model: str, system: str, input_items: Any, operation: str, reasoning_effort: str,
        max_output_tokens: int, tools: list[dict] | None = None, tool_choice: dict | None = None,
    ) -> Any:
        kwargs = _responses_kwargs(model, system, input_items, reasoning_effort, max_output_tokens, tools, tool_choice)
        try:
            with usage_tracker.timer() as t:
                response = await self._client.responses.create(**kwargs)
        except openai_lib.OpenAIError as e:
            raise _as_llm_error(e) or e
        if response.usage:
            usage_tracker.append_usage(
                operation=operation, model=model, input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens, duration_ms=t["ms"], response_id=response.id,
            )
        return response

    async def _responses_with_retry(
        self, model: str, system: str, input_items: Any, operation: str, reasoning_effort: str,
        max_output_tokens: int, accept: Callable[[Any], str | None], tools: list[dict] | None = None,
        tool_choice: dict | None = None,
    ) -> Any:
        """accept(response)가 None이면 정상, 문자열이면 그 이유로 실패한 시도로 보고 다시 요청한다.
        실패한 시도는 재시도로 결국 성공해도 기록한다. 출력 상한에 걸렸으면 상한을 두 배로 늘린다."""
        for attempt in range(1, _RESPONSES_MAX_ATTEMPTS + 1):
            response = await self._responses_call(
                model, system, input_items, operation, reasoning_effort, max_output_tokens, tools, tool_choice,
            )
            reason = accept(response)
            if reason is None:
                return response
            incidents.record(operation, model, attempt, reason, _input_chars(input_items), reasoning_effort, response)
            incidents.dump_full(  # 원인 진단용 — 원인 확인 후 이 호출만 지운다
                operation, attempt, response,
                _responses_kwargs(model, system, input_items, reasoning_effort, max_output_tokens, tools, tool_choice),
            )
            if _hit_output_limit(response) and max_output_tokens < _RESPONSES_OUTPUT_TOKENS_CAP:
                max_output_tokens = min(max_output_tokens * 2, _RESPONSES_OUTPUT_TOKENS_CAP)
        raise LLMAPIError("LLM이 결과를 만들지 못했습니다 — 잠시 후 다시 시도해주세요.", 503)

    async def _extract_structured_responses(
        self, system: str, user: str, tool_name: str, tool_description: str, tool_schema: dict,
        model: str, operation: str, max_tokens: int, reasoning_effort: str | None,
    ) -> dict:
        tools = [{"type": "function", "name": tool_name, "description": tool_description, "parameters": tool_schema, "strict": False}]
        parsed: dict[str, Any] = {}

        def accept(response: Any) -> str | None:
            call = next((it for it in response.output if it.type == "function_call" and it.name == tool_name), None)
            if response.status != "completed" or call is None:
                return f"status={response.status}, 함수 호출 {'있음' if call else '없음'}"
            try:
                parsed["value"] = json.loads(call.arguments)
            except json.JSONDecodeError:
                return "함수 인자가 JSON이 아님"
            return None

        await self._responses_with_retry(
            model, system, user, operation, reasoning_effort or get_reasoning_effort(),
            max(max_tokens, _RESPONSES_MIN_OUTPUT_TOKENS), accept, tools,
            {"type": "function", "name": tool_name},  # 특정 함수를 반드시 호출하도록 강제
        )
        return parsed["value"]

    async def _complete_responses(
        self, system: str, user: str, model: str, operation: str, content: list[dict] | None,
        max_tokens: int, reasoning_effort: str | None,
    ) -> str:
        input_items: Any = [{"role": "user", "content": _to_responses_content(content)}] if content is not None else user
        last_text = ""

        def accept(response: Any) -> str | None:
            nonlocal last_text
            text = response.output_text or ""
            last_text = text or last_text
            if response.status != "completed" or not text:
                return f"status={response.status}, 출력 글자 {len(text)}"
            return None

        try:
            response = await self._responses_with_retry(
                model, system, input_items, operation, reasoning_effort or get_reasoning_effort(),
                max(max_tokens, _RESPONSES_MIN_OUTPUT_TOKENS), accept,
            )
        except LLMAPIError:
            if not last_text:
                raise
            # 잘린 응답을 그대로 반환하는 기존 동작과 같다 — 호출자가 불완전한 글이라도 쓸 수 있게 한다.
            logger.warning("[%s] 응답이 끝까지 완성되지 못했습니다(재시도 후에도 부족). 내용이 불완전할 수 있습니다.", operation)
            return last_text
        return response.output_text or ""

    async def _stream_responses(
        self, system: str, messages: list[dict], model: str, operation: str, max_tokens: int,
    ) -> AsyncIterator[str]:
        effort = get_reasoning_effort()
        final = None
        try:
            with usage_tracker.timer() as t:
                stream = await self._client.responses.create(
                    model=model, instructions=system, input=_messages_to_responses(messages), store=False,
                    reasoning={"effort": effort}, max_output_tokens=max(max_tokens, _RESPONSES_MIN_OUTPUT_TOKENS), stream=True,
                )
                async for event in stream:
                    if event.type == "response.output_text.delta":
                        yield event.delta
                    elif event.type in ("response.completed", "response.incomplete", "response.failed"):
                        final = event.response
        except openai_lib.OpenAIError as e:
            raise _as_llm_error(e) or e
        if final is None:
            # 이미 글자를 내보낸 뒤라 다시 요청할 수 없다. 사유만 남긴다.
            incidents.record(operation, model, 1, "스트림이 완료 이벤트 없이 끝남", _input_chars(messages), effort)
            return
        if final.usage:
            usage_tracker.append_usage(
                operation=operation, model=model, input_tokens=final.usage.input_tokens,
                output_tokens=final.usage.output_tokens, duration_ms=t["ms"], response_id=final.id,
            )
        if final.status != "completed":
            incidents.record(operation, model, 1, f"스트림 종료 status={final.status}", _input_chars(messages), effort, final)

    async def _run_agent_responses(
        self, system: str, question: str, tools: list[dict], tool_executor: Callable[[str, dict], Awaitable[Any]],
        model: str, operation: str, history: list[dict] | None, max_iterations: int,
    ) -> dict:
        tool_defs = [
            {"type": "function", "name": t["name"], "description": t["description"], "parameters": t["input_schema"], "strict": False}
            for t in tools
        ]
        input_items: list[dict] = [*_messages_to_responses(history or []), {"role": "user", "content": question}]
        tool_calls_trace: list[dict] = []
        effort = get_reasoning_effort()

        def accept(response: Any) -> str | None:
            has_call = any(it.type == "function_call" for it in response.output)
            if response.status != "completed" or not (has_call or response.output_text):
                return f"status={response.status}, 함수 호출 {'있음' if has_call else '없음'}, 출력 글자 {len(response.output_text or '')}"
            return None

        for _ in range(max_iterations):
            response = await self._responses_with_retry(
                model, system, input_items, operation, effort, _RESPONSES_MIN_OUTPUT_TOKENS, accept, tool_defs,
            )
            calls = [it for it in response.output if it.type == "function_call"]
            if not calls:
                return {"text": response.output_text or "", "tool_calls": tool_calls_trace}
            # 저장하지 않는 요청(store=False)이라 reasoning을 포함한 이번 턴의 출력 항목 전부를 순서대로 다음 요청에 다시 넣어야 한다.
            input_items.extend(it.model_dump(exclude_none=True) for it in response.output)
            for call in calls:
                args = json.loads(call.arguments) if call.arguments else {}
                try:
                    result = await tool_executor(call.name, args)
                except (RuntimeError, LLMAPIError) as e:
                    # chat/completions 경로·Claude provider의 run_agent()와 같은 원칙 — 도구 하나가 실패해도
                    # 전체 요청을 죽이지 않고 사용자에게 보여도 안전한 메시지를 도구 결과로 모델에 돌려준다.
                    result = {"error": str(e)}
                except Exception:
                    logger.exception("도구 실행 중 예상 못 한 오류 (%s)", call.name)
                    result = {"error": "내부 도구 실행 오류가 발생했습니다."}
                tool_calls_trace.append({"tool": call.name, "args": args, "result": result})
                input_items.append({
                    "type": "function_call_output", "call_id": call.call_id,
                    "output": json.dumps(result, ensure_ascii=False, default=str),
                })

        return {
            "text": "죄송합니다, 이 질문에 답하는 데 필요한 정보를 정리하지 못했습니다(도구 호출 반복 한도 초과).",
            "tool_calls": tool_calls_trace,
        }
