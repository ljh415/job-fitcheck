"""LLM 호출이 정상 완료되지 않은 시도를 data/llm_incidents.jsonl에 한 줄씩 남긴다.

재시도로 결국 성공한 호출도 실패한 시도마다 남겨야 재시도가 실패를 가리지 않는다.
요청 본문(프로필·공고 등 개인정보)은 저장하지 않고 입력 글자 수만 남긴다. 기록 실패가 호출을 깨뜨리면
안 되므로 어떤 예외도 밖으로 내보내지 않는다. 예외로 dump_full()만 요청 본문과 응답 전체를 별도 파일에 둔다.
"""
import json
import logging
from datetime import datetime
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

_MESSAGE_PREVIEW_CHARS = 500
_MAX_MESSAGES = 5


def summarize_response(response: Any) -> dict:
    """Responses API 응답에서 진단에 필요한 값만 뽑는다(본문 전체는 넣지 않는다)."""
    items = list(getattr(response, "output", None) or [])
    messages = []
    for it in items:
        if getattr(it, "type", None) != "message":
            continue
        text = "".join(getattr(c, "text", "") or "" for c in (getattr(it, "content", None) or []))
        messages.append({"phase": getattr(it, "phase", None), "text": text[:_MESSAGE_PREVIEW_CHARS]})
    usage = getattr(response, "usage", None)
    details = getattr(response, "incomplete_details", None)
    reasoning_tokens = getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", None)
    return {
        "response_id": getattr(response, "id", None),
        "request_id": getattr(response, "_request_id", None),
        "status": getattr(response, "status", None),
        "incomplete_reason": getattr(details, "reason", None),
        "item_types": [getattr(it, "type", None) for it in items],
        "messages": messages[:_MAX_MESSAGES],
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "reasoning_tokens": reasoning_tokens,
    }


def record(operation: str, model: str, attempt: int, reason: str, input_chars: int | None = None,
           reasoning_effort: str | None = None, response: Any = None) -> None:
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "operation": operation,
        "model": model,
        "attempt": attempt,
        "reason": reason,
        "input_chars": input_chars,
        "reasoning_effort": reasoning_effort,
    }
    try:
        if response is not None:
            entry.update(summarize_response(response))
        logger.warning(
            "[%s] %s 응답이 정상 완료되지 않음 (시도 %d, %s, response_id=%s)",
            operation, model, attempt, reason, entry.get("response_id"),
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        with open(settings.data_dir / "llm_incidents.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        logger.warning("LLM 이상 응답 기록 실패: %s", e)


# ── 원인 진단용 전체 기록 ────────────────────────────────────────────────────
# sol이 함수 호출 없이 끝나는 원인을 응답 내용으로 확인하려고 둔 것이다. 원인을 확인하고 나면 이 함수와
# openai.py의 호출 한 줄(incidents.dump_full)만 지우면 되고, 위의 record()·summarize_response()는
# 이 함수에 의존하지 않는다.
def dump_full(operation: str, attempt: int, response: Any, request: dict | None) -> None:
    """실패한 시도의 요청 본문과 응답 전체를 data/llm_incidents/ 아래 파일 하나로 저장한다.
    요청에 프로필·공고(개인정보)가 들어 있으므로 data/ 밖으로 내보내지 않는다."""
    try:
        dump_dir = settings.data_dir / "llm_incidents"
        dump_dir.mkdir(parents=True, exist_ok=True)
        full = response.model_dump(mode="json") if hasattr(response, "model_dump") else None
        name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{getattr(response, 'id', None) or 'no_response_id'}.json"
        with open(dump_dir / name, "w", encoding="utf-8") as f:
            json.dump(
                {"operation": operation, "attempt": attempt, "request": request, "response": full},
                f, ensure_ascii=False, indent=1, default=str,
            )
    except Exception as e:
        logger.warning("LLM 이상 응답 전체 기록 실패: %s", e)
