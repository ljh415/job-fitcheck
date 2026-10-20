"""
Anthropic Claude provider 구현.

구조화 추출(extract_structured): 모델별로 경로가 갈린다.
  - 5.5 계열(_JSON_THINKING의 모델): JSON outputs(output_config.format) — 강제 도구 호출을 400으로 거부하므로
  - 그 외(Sonnet 5, Haiku 4.5): tool_choice={"type": "tool"} 로 특정 툴 강제 호출
  어느 경로에서도 temperature는 보내지 않는다. 쓸 수 있는 모델은 ALLOWED_CLAUDE_MODELS뿐이다.
스트리밍: messages.stream() 컨텍스트 매니저 사용
"""
import copy
import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import anthropic

from services import usage_tracker
from config import settings
from .base import LLMAPIError, LLMProvider

logger = logging.getLogger(__name__)

# 5.5 계열은 tool_choice 강제 호출(type tool/any)을 400으로 거부한다 — 구조화 응답은
# JSON outputs(output_config.format)로 받는다. 값은 요청에 넣을 thinking 설정이다.
# Sonnet 5.5는 thinking을 끌 수 없어(disabled 400) 최저값 between_tools(도구가 없으면
# 0토큰)를 쓰고, Haiku 5.5는 between_tools가 400이라 disabled(effort high 이하에서 허용)를 쓴다 —
# 필드를 생략하면 adaptive thinking으로 출력이 2~3배 늘고 판정이 max_tokens에 걸려 재시도한다.
# None이면 thinking 필드를 생략한다. 시험 결과는 docs/sonnet-5-5-migration/PLAN.md.
_JSON_THINKING: dict[str, dict | None] = {
    "claude-sonnet-5-5": {"type": "between_tools"},
    "claude-haiku-5-5": {"type": "disabled"},
}
_JSON_OUTPUT_MODELS = frozenset(_JSON_THINKING)

# 설정 화면에서 고를 수 있는 Claude 모델 = 시험을 마친 모델. 새 모델은 시험 후 여기에 추가한다.
# JSON 경로 모델이 아니면 옛 도구 호출 경로로 가며, 이 경로는 temperature를 보내지 않는다.
ALLOWED_CLAUDE_MODELS = _JSON_OUTPUT_MODELS | {"claude-sonnet-5", "claude-haiku-4-5-20251001"}


def _to_json_outputs_schema(schema: dict) -> dict:
    """tool input_schema를 JSON outputs 스키마로 변환한 사본을 반환한다(5.5 시험에서 확인한 거부 조건).
    - 모든 object에 additionalProperties: false
    - 모든 object의 필드를 required로 — 선택(optional) 필드가 많으면(추출 스키마 22개) 문법 컴파일이
      'Schema is too complex'로 실패하거나 60초 넘게 걸린다. nullable 필드는 type 배열 그대로 두면
      null 값을 돌려준다.
    - minimum/maximum 제거(400) — 범위 검증은 후처리 코드가 이미 한다
    - type 배열 + enum 조합(예: ["string","null"] + enum)은 검증기가 거부 → anyOf로 변환"""
    def walk(n: Any) -> None:
        if isinstance(n, dict):
            if n.get("type") == "object":
                n["additionalProperties"] = False
                if "properties" in n:
                    n["required"] = list(n["properties"])
            n.pop("minimum", None)
            n.pop("maximum", None)
            if isinstance(n.get("type"), list) and isinstance(n.get("enum"), list):
                branches = [
                    {"type": t, "enum": [v for v in n["enum"] if v is not None]}
                    for t in n["type"] if t != "null"
                ]
                if "null" in n["type"]:
                    branches.append({"type": "null"})
                n.pop("type")
                n.pop("enum")
                n["anyOf"] = branches
            for v in list(n.values()):
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    converted = copy.deepcopy(schema)
    walk(converted)
    return converted


def _replace_tool_call_phrase(user: str, tool_name: str) -> str:
    """프롬프트의 '<툴 이름> 툴(함수)을 호출하(여|세요)'를 JSON 응답 지시로 바꾼다. JSON 경로에서는
    호출할 도구가 없다(다른 provider의 같은 프롬프트는 그대로 둔다)."""
    return re.sub(
        rf"{re.escape(tool_name)} 툴\(함수\)을 호출(하여|하세요)",
        lambda m: "스키마에 맞는 JSON으로 응답" + m.group(1),
        user,
    )


def _raise_status_error(e: anthropic.APIStatusError) -> None:
    """APIStatusError를 사용자 친화적 메시지로 변환해 raise한다.
    크레딧/빌링 소진(400, "credit balance" 문구)은 "잠시 후 다시 시도"가 아니라
    명확히 다른 조치(충전)가 필요하므로 별도 메시지로 분기한다 — RateLimitError만
    따로 처리하고 나머지 APIStatusError를 뭉뚱그리면, 크레딧 소진 시에도 "잠시 후
    다시 시도해주세요"라는 틀린 안내가 나간다."""
    body = e.body if isinstance(e.body, dict) else {}
    error_info = body.get("error") if isinstance(body, dict) else None
    error_msg = error_info.get("message", "") if isinstance(error_info, dict) else ""
    if "credit balance" in error_msg.lower():
        raise LLMAPIError(
            "Anthropic 크레딧이 모두 소진되었습니다 — Anthropic 콘솔(console.anthropic.com)에서 결제/크레딧을 충전해주세요.",
            402,
        )
    raise LLMAPIError(f"LLM 서비스 오류 ({e.status_code}) — 잠시 후 다시 시도해주세요.", 503)


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
        if model in _JSON_OUTPUT_MODELS:
            return await self._extract_structured_json(system, user, tool_name, tool_schema, model, operation, max_tokens)
        tool_def = anthropic.types.ToolParam(
            name=tool_name,
            description=tool_description,
            input_schema=tool_schema,  # type: ignore[arg-type]
        )
        try:
            with usage_tracker.timer() as t:
                response = await self._client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
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
            _raise_status_error(e)
        except anthropic.APIConnectionError:
            raise LLMAPIError("LLM 서비스 연결 실패 — 네트워크 상태를 확인해주세요.", 503)
        usage_tracker.append_usage(
            operation=operation,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            duration_ms=t["ms"],
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

    async def _extract_structured_json(
        self,
        system: str,
        user: str,
        tool_name: str,
        tool_schema: dict,
        model: str,
        operation: str,
        max_tokens: int,
    ) -> dict:
        schema = _to_json_outputs_schema(tool_schema)
        user = _replace_tool_call_phrase(user, tool_name)
        extra_body: dict[str, Any] = {"output_config": {"effort": "high", "format": {"type": "json_schema", "schema": schema}}}
        if _JSON_THINKING[model] is not None:
            extra_body["thinking"] = _JSON_THINKING[model]
        current_max_tokens = max_tokens
        # 응답이 잘리면(max_tokens) 잘린 JSON은 파싱이 안 되므로 complete()처럼 한 번 늘려 재시도한다
        for attempt in range(2):
            try:
                with usage_tracker.timer() as t:
                    response = await self._client.messages.create(
                        model=model,
                        max_tokens=current_max_tokens,
                        system=system,
                        messages=[{"role": "user", "content": user}],
                        extra_body=extra_body,
                    )
            except anthropic.AuthenticationError:
                raise LLMAPIError("LLM API 인증 실패 — 설정에서 Anthropic API 키를 확인해주세요.", 401)
            except anthropic.RateLimitError:
                raise LLMAPIError("LLM API 요청 한도 초과 — 잠시 후 다시 시도해주세요.", 429)
            except anthropic.APIStatusError as e:
                _raise_status_error(e)
            except anthropic.APIConnectionError:
                raise LLMAPIError("LLM 서비스 연결 실패 — 네트워크 상태를 확인해주세요.", 503)
            usage_tracker.append_usage(
                operation=operation,
                model=model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                duration_ms=t["ms"],
            )
            if response.stop_reason == "max_tokens" and attempt == 0 and current_max_tokens < 32768:
                current_max_tokens = min(current_max_tokens * 2, 32768)
                logger.info("[%s] max_tokens에 잘림 — %d로 늘려 재시도", operation, current_max_tokens)
                continue
            text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                result = None
            if not isinstance(result, dict):
                raise RuntimeError(
                    f"JSON outputs 응답을 파싱하지 못했습니다 (stop_reason={response.stop_reason}, 출력 {response.usage.output_tokens}토큰)"
                )
            return result
        raise RuntimeError("JSON outputs 응답을 얻지 못했습니다")

    async def complete(
        self,
        system: str,
        user: str,
        model: str,
        operation: str = "",
        content: list[dict] | None = None,
        max_tokens: int = 8192,
        reasoning_effort: str | None = None,  # ponytail: OpenAI 전용, Anthropic은 미사용. ABC 시그니처 일치용
    ) -> str:
        msg_content: str | list = content if content is not None else user
        import logging as _logging
        logger = _logging.getLogger(__name__)
        current_max_tokens = max_tokens
        # 잘린 응답을 그대로 반환하는 대신, max_tokens를 2배로 늘려 한 번 더 시도한다
        # (32768 도달 시 더 늘려도 소용없으니 중단) — 사용자가 직접 재시도하지 않아도 되게 함.
        for attempt in range(2):
            try:
                with usage_tracker.timer() as t:
                    response = await self._client.messages.create(
                        model=model,
                        max_tokens=current_max_tokens,
                        system=system,
                        messages=[{"role": "user", "content": msg_content}],  # type: ignore[arg-type]
                    )
            except anthropic.AuthenticationError:
                raise LLMAPIError("LLM API 인증 실패 — 설정에서 Anthropic API 키를 확인해주세요.", 401)
            except anthropic.RateLimitError:
                raise LLMAPIError("LLM API 요청 한도 초과 — 잠시 후 다시 시도해주세요.", 429)
            except anthropic.APIStatusError as e:
                _raise_status_error(e)
            except anthropic.APIConnectionError:
                raise LLMAPIError("LLM 서비스 연결 실패 — 네트워크 상태를 확인해주세요.", 503)
            usage_tracker.append_usage(
                operation=operation,
                model=model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                duration_ms=t["ms"],
            )
            stop_reason = response.stop_reason
            if stop_reason == "max_tokens" and attempt == 0 and current_max_tokens < 32768:
                retry_max_tokens = min(current_max_tokens * 2, 32768)
                logger.info(
                    "[%s] max_tokens(%d)에 잘림 — %d로 늘려 재시도",
                    operation, current_max_tokens, retry_max_tokens,
                )
                current_max_tokens = retry_max_tokens
                continue
            if stop_reason == "max_tokens":
                logger.warning(
                    "[%s] 응답이 max_tokens(%d)에 의해 잘렸습니다(재시도 후에도 부족, 출력 %d토큰). 내용이 불완전할 수 있습니다.",
                    operation, current_max_tokens, response.usage.output_tokens,
                )
            # adaptive thinking(Sonnet 5 등)이 켜진 모델은 content 맨 앞에 type="thinking"
            # 블록이 먼저 오는데, 이것도 hasattr(block, "text")가 True이면서 .text는 None이라
            # 예전 방식(hasattr만 확인)으로는 실제 답변 대신 None을 그대로 반환해버렸다
            # (2026-09-02, LLM Judge 스크립트 작성 중 실제로 재현해 발견). type이 정확히
            # "text"인 블록만 골라야 한다.
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    return block.text  # type: ignore[union-attr]
            return ""
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
                with usage_tracker.timer() as t:
                    response = await self._client.messages.create(
                        model=model,
                        max_tokens=8192,
                        system=system,
                        messages=messages,  # type: ignore[arg-type]
                        tools=tool_defs,
                    )
            except anthropic.AuthenticationError:
                raise LLMAPIError("LLM API 인증 실패 — 설정에서 Anthropic API 키를 확인해주세요.", 401)
            except anthropic.RateLimitError:
                raise LLMAPIError("LLM API 요청 한도 초과 — 잠시 후 다시 시도해주세요.", 429)
            except anthropic.APIStatusError as e:
                _raise_status_error(e)
            except anthropic.APIConnectionError:
                raise LLMAPIError("LLM 서비스 연결 실패 — 네트워크 상태를 확인해주세요.", 503)

            usage_tracker.append_usage(
                operation=operation,
                model=model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                duration_ms=t["ms"],
            )

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_use_blocks:
                # complete()와 같은 이유 — thinking 블록도 hasattr(b, "text")가 True이면서
                # .text는 None이라 "".join()이 TypeError로 죽는다(2026-09-02 발견).
                text = "".join(b.text for b in response.content if b.type == "text")
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
                    # 있어 Claude/프론트 도구 트레이스에 그대로 노출하지 않는다 — 서버 로그에만
                    # 전체를 남기고 도구에는 일반화된 메시지만 준다.
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
        max_tokens: int = 8192,
    ) -> AsyncIterator[str]:
        try:
            with usage_tracker.timer() as t:
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
                duration_ms=t["ms"],
            )
        except anthropic.AuthenticationError:
            raise LLMAPIError("LLM API 인증 실패 — 설정에서 Anthropic API 키를 확인해주세요.", 401)
        except anthropic.RateLimitError:
            raise LLMAPIError("LLM API 요청 한도 초과 — 잠시 후 다시 시도해주세요.", 429)
        except anthropic.APIStatusError as e:
            _raise_status_error(e)
        except anthropic.APIConnectionError:
            raise LLMAPIError("LLM 서비스 연결 실패 — 네트워크 상태를 확인해주세요.", 503)


if __name__ == "__main__":
    from types import SimpleNamespace

    credit_error = SimpleNamespace(
        status_code=400,
        body={"error": {"message": "Your credit balance is too low to access the Anthropic API."}},
    )
    try:
        _raise_status_error(credit_error)  # type: ignore[arg-type]
        assert False, "예외가 발생했어야 함"
    except LLMAPIError as e:
        assert e.status_code == 402, e.status_code
        assert "크레딧" in str(e), str(e)

    other_error = SimpleNamespace(status_code=500, body={"error": {"message": "internal error"}})
    try:
        _raise_status_error(other_error)  # type: ignore[arg-type]
        assert False, "예외가 발생했어야 함"
    except LLMAPIError as e:
        assert e.status_code == 503, e.status_code
        assert "500" in str(e), str(e)

    print("OK: 크레딧 소진 400 -> 402 전용 메시지, 그 외는 기존 503 메시지 유지")

    # 허용 모델·경로 분기 — Sonnet/Haiku 4개만, Opus·Fable·구형은 허용 밖
    assert ALLOWED_CLAUDE_MODELS == {
        "claude-sonnet-5", "claude-sonnet-5-5", "claude-haiku-4-5-20251001", "claude-haiku-5-5",
    }, ALLOWED_CLAUDE_MODELS
    for model in ("claude-opus-5-5", "claude-fable-5-1", "claude-opus-4-8", "claude-sonnet-4-6"):
        assert model not in ALLOWED_CLAUDE_MODELS, model
    assert _JSON_OUTPUT_MODELS == {"claude-sonnet-5-5", "claude-haiku-5-5"}
    assert _JSON_THINKING["claude-sonnet-5-5"] == {"type": "between_tools"}
    print("OK: 허용 모델 4개, JSON 경로는 5.5 두 모델")

    # JSON outputs 경로(5.5) — 스키마 변환·문구 치환
    import prompts

    toy = {
        "type": "object",
        "properties": {
            "a": {"type": ["string", "null"], "enum": ["x", "y"]},
            "b": {"type": "integer", "minimum": 0, "maximum": 10},
            "c": {"type": "array", "items": {"anyOf": [
                {"type": "string"},
                {"type": "object", "properties": {"t": {"type": "string"}}},
            ]}},
        },
        "required": ["b"],
    }
    conv = _to_json_outputs_schema(toy)
    assert toy["required"] == ["b"] and "minimum" in toy["properties"]["b"], "원본 스키마는 바뀌면 안 됨"
    assert conv["additionalProperties"] is False and conv["required"] == ["a", "b", "c"]
    assert "minimum" not in conv["properties"]["b"] and "maximum" not in conv["properties"]["b"]
    assert conv["properties"]["a"] == {"anyOf": [{"type": "string", "enum": ["x", "y"]}, {"type": "null"}]}
    inner = conv["properties"]["c"]["items"]["anyOf"][1]
    assert inner["additionalProperties"] is False and inner["required"] == ["t"]

    def _assert_json_outputs_ok(n: Any) -> None:
        if isinstance(n, dict):
            if n.get("type") == "object":
                assert n.get("additionalProperties") is False, n
                assert n.get("required") == list(n.get("properties", {})), n
            assert "minimum" not in n and "maximum" not in n, n
            assert not (isinstance(n.get("type"), list) and "enum" in n), n
            for v in n.values():
                _assert_json_outputs_ok(v)
        elif isinstance(n, list):
            for v in n:
                _assert_json_outputs_ok(v)

    for schema_name in ("EXTRACT_COMPANY_TOOL_SCHEMA", "EVALUATE_FIT_JUDGE_TOOL_SCHEMA", "EVALUATE_FIT_TOOL_SCHEMA", "EXTRACT_PROFILE_TOOL_SCHEMA"):
        _assert_json_outputs_ok(_to_json_outputs_schema(getattr(prompts, schema_name)))

    assert _replace_tool_call_phrase("판정 submit_x 툴(함수)을 호출하세요.", "submit_x") == "판정 스키마에 맞는 JSON으로 응답하세요."
    assert (
        _replace_tool_call_phrase("추출 후 extract_company_info 툴(함수)을 호출하여 결과를 제출하세요.", "extract_company_info")
        == "추출 후 스키마에 맞는 JSON으로 응답하여 결과를 제출하세요."
    )
    assert _replace_tool_call_phrase("다른 툴(함수)을 호출하세요.", "submit_x") == "다른 툴(함수)을 호출하세요."
    judge_user = prompts.EVALUATE_FIT_JUDGE_USER_TEMPLATE.format(
        candidate_profile="p", company_json="{}", raw_text="r", item_list="- required:0: x",
        tool_name=prompts.EVALUATE_FIT_JUDGE_TOOL_NAME, custom_criteria="",
    )
    assert "툴(함수)" not in _replace_tool_call_phrase(judge_user, prompts.EVALUATE_FIT_JUDGE_TOOL_NAME)
    assert "툴(함수)" not in _replace_tool_call_phrase(
        prompts.EXTRACT_COMPANY_USER_TEMPLATE.format(raw_text="r"), prompts.EXTRACT_COMPANY_TOOL_NAME
    )
    assert "툴(함수)" not in _replace_tool_call_phrase(
        prompts.EXTRACT_PROFILE_USER_TEMPLATE.format(pdf_text="r", extra_section=""), prompts.EXTRACT_PROFILE_TOOL_NAME
    )
    print("OK: 5.5는 JSON outputs 경로, 스키마 변환(additionalProperties·전 필드 required·min/max 제거·type 배열+enum→anyOf)과 툴 호출 문구 치환")
