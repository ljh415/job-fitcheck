"""
LLM API 사용량 추적 및 비용 계산.

각 LLM 호출 후 토큰 수와 비용을 data/usage_log.jsonl에 한 줄씩 기록한다.
"""
import json
import logging
from datetime import datetime

from config import settings

logger = logging.getLogger(__name__)

# 모델별 단가 (USD / 1M tokens)
PRICING: dict[str, dict[str, float]] = {
    # Anthropic Claude
    "claude-opus-4-7":              {"input": 5.00,  "output": 25.00},
    "claude-opus-4-6":              {"input": 5.00,  "output": 25.00},
    "claude-sonnet-4-6":            {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5":             {"input": 1.00,  "output":  5.00},
    "claude-haiku-4-5-20251001":    {"input": 1.00,  "output":  5.00},
    # OpenAI GPT-4 계열
    "gpt-4o":                       {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":                  {"input": 0.15,  "output":  0.60},
    "gpt-4.1":                      {"input": 2.00,  "output":  8.00},
    "gpt-4.1-mini":                 {"input": 0.40,  "output":  1.60},
    "gpt-4.1-nano":                 {"input": 0.10,  "output":  0.40},
    # OpenAI GPT-5 계열
    "gpt-5-nano":                   {"input": 0.05,  "output":  0.20},
    "gpt-5-mini":                   {"input": 0.25,  "output":  1.00},
    "gpt-5":                        {"input": 1.25,  "output":  5.00},
    "gpt-5.1":                      {"input": 1.25,  "output":  5.00},
    "gpt-5.2":                      {"input": 1.75,  "output":  7.00},
    "gpt-5.4-nano":                 {"input": 0.20,  "output":  0.80},
    "gpt-5.4-mini":                 {"input": 0.75,  "output":  3.00},
    "gpt-5.4":                      {"input": 2.50,  "output": 10.00},
    "gpt-5.5":                      {"input": 5.00,  "output": 20.00},
    "gpt-5.5-pro":                  {"input": 30.00, "output": 120.00},
    # Google Gemini (유료 티어 근사값 — 무료 티어는 비용 미발생)
    "gemini-2.5-pro":               {"input": 1.25,  "output": 10.00},
    "gemini-2.5-flash":             {"input": 0.30,  "output":  2.50},
    "gemini-2.5-flash-lite":        {"input": 0.10,  "output":  0.40},
    "gemini-2.0-flash":             {"input": 0.10,  "output":  0.40},
    "gemini-2.0-flash-lite":        {"input": 0.075, "output":  0.30},
}


def calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price = PRICING.get(model)
    if not price:
        # 알 수 없는 모델 — 단가 0으로 처리 (비용 미집계)
        return 0.0
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


def append_usage(operation: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """사용 기록을 JSONL 파일에 추가하고 비용을 반환한다."""
    cost = calc_cost(model, input_tokens, output_tokens)
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "operation": operation,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 6),
    }
    try:
        path = settings.data_dir / "usage_log.jsonl"
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("사용량 기록 실패: %s", e)
    return cost


def read_usage(limit: int = 200) -> dict:
    """최근 사용 이력과 합계를 반환한다.
    total_* 는 전체 누적 기준, entries 는 최근 limit 건만 포함한다."""
    path = settings.data_dir / "usage_log.jsonl"
    if not path.exists():
        return {
            "entries": [], "total_log_count": 0,
            "total_cost_usd": 0.0, "total_input_tokens": 0, "total_output_tokens": 0,
        }

    all_entries = []
    for line in path.read_text(encoding="utf-8").strip().splitlines():
        try:
            all_entries.append(json.loads(line))
        except Exception:
            pass

    total_cost = sum(e.get("cost_usd", 0) for e in all_entries)
    total_input = sum(e.get("input_tokens", 0) for e in all_entries)
    total_output = sum(e.get("output_tokens", 0) for e in all_entries)

    return {
        "entries": list(reversed(all_entries[-limit:])),
        "total_log_count": len(all_entries),
        "total_cost_usd": round(total_cost, 6),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
    }
