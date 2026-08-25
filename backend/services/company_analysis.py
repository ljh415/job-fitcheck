"""회사 적합도 평가 — provider별 분기를 하나로 통합한다. 최초 평가(_process_company)와
재평가(refit_company)가 이 함수를 공유해, 한쪽만 고쳐서 결과가 어긋나는 걸 방지한다."""
import json
import re

import prompts
import storage
from llm.router import LLMSnapshot, high_from_snapshot


async def evaluate_fit(
    snap: LLMSnapshot,
    profile_text: str,
    company_data: dict,
    raw_text: str,
    operation: str,
) -> tuple[dict, str]:
    """반환: (fit_data, fit_report). fit_data엔 fit_report_body가 없다(항상 fit_report로
    분리해 반환) — 호출부가 frontmatter 조립·body 섹션 삽입을 각자 맡는다.

    operation은 usage_tracker 비용 로그 구분용 — 최초 평가는 "적합도 평가", 재평가는
    "적합도 재평가"를 넘긴다(usage_log.jsonl의 operation 필드, 설정 화면 비용 집계 근거).

    profile_text/raw_text는 호출부가 이미 [점수 제외] 제거·태그 이스케이프까지 끝낸 값을
    준다(호출부마다 프로필/원문을 얻는 경로가 다름 — 재평가는 storage에서 slug로 다시 읽음).
    """
    high, high_model = high_from_snapshot(snap)
    eval_criteria = storage.read_eval_criteria().strip()
    custom_criteria_section = (
        f"\n\n## 추가 평가 기준 (사용자 지정)\n{eval_criteria}{prompts.CUSTOM_CRITERIA_BOUNDARY_NOTICE}"
        if eval_criteria else ""
    )
    user_fit = prompts.EVALUATE_FIT_USER_TEMPLATE.format(
        candidate_profile=profile_text,
        company_json=json.dumps(company_data, ensure_ascii=False, indent=2),
        raw_text=raw_text[:4000],
        custom_criteria=custom_criteria_section,
    )
    # Gemini는 function call 내에 장문 마크다운 생성 시 MALFORMED_FUNCTION_CALL이 발생함.
    # 구조화 데이터(점수·라벨·강점·갭)만 tool call로 추출하고, 리포트 본문은 complete()로 분리 생성.
    if snap.provider_name == "gemini":
        gemini_fit_schema = {
            **prompts.EVALUATE_FIT_TOOL_SCHEMA,
            "properties": {k: v for k, v in prompts.EVALUATE_FIT_TOOL_SCHEMA["properties"].items() if k != "fit_report_body"},
            "required": [r for r in prompts.EVALUATE_FIT_TOOL_SCHEMA.get("required", []) if r != "fit_report_body"],
        }
        fit_result = await high.extract_structured(
            system=prompts.evaluate_fit_system(snap.provider_name),
            user=user_fit,
            tool_name=prompts.EVALUATE_FIT_TOOL_NAME,
            tool_description=prompts.EVALUATE_FIT_TOOL_DESCRIPTION,
            tool_schema=gemini_fit_schema,
            model=high_model,
            operation=operation,
            reasoning_effort=snap.reasoning_effort,
        )
        # Gemini 전용: location_check → gaps 자동 브릿지. 예전엔 최초 평가에만 있고 재평가엔
        # 빠져 있던 버그를 이 통합 과정에서 발견 — 이제 두 경로 모두 동일하게 보정된다.
        loc = fit_result.get("location_check", "")
        gaps = fit_result.get("gaps", [])
        if loc and ("조건부" in loc or "미달" in loc):
            if not any(kw in g for g in gaps for kw in ("근무지", "위치", "출퇴근", "판교", "location")):
                fit_result["gaps"] = gaps + [f"(하) 근무지 조건부 - {loc}"]
        fit_report_raw = await high.complete(
            system=prompts.evaluate_fit_system(snap.provider_name),
            user=user_fit + f"\n\n평가 결과 (참고용):\n{json.dumps(fit_result, ensure_ascii=False)}\n\n위 평가 결과를 바탕으로 fit_report_body 전체를 아래 형식에 맞게 작성하세요. ## 4. 적합도 리포트 로 시작하고, ## 5. 종합 의견 (핵심 근거 + 지원 전략)까지 빠짐없이 작성하세요.",
            model=high_model,
            operation="적합도 리포트 본문 생성",
            max_tokens=8192,
            reasoning_effort=snap.reasoning_effort,
        )
        fit_report = re.sub(r'^##\s*4\.\s*적합도 리포트[^\n]*\n+', '', fit_report_raw.strip()).strip()
    else:
        fit_result = await high.extract_structured(
            system=prompts.evaluate_fit_system(snap.provider_name),
            user=user_fit,
            tool_name=prompts.EVALUATE_FIT_TOOL_NAME,
            tool_description=prompts.EVALUATE_FIT_TOOL_DESCRIPTION,
            tool_schema=prompts.EVALUATE_FIT_TOOL_SCHEMA,
            model=high_model,
            operation=operation,
            reasoning_effort=snap.reasoning_effort,
        )
        fit_report = re.sub(r'^##\s*4\.\s*적합도 리포트[^\n]*\n+', '', fit_result.pop("fit_report_body", "").strip()).strip()
    return fit_result, fit_report


if __name__ == "__main__":
    import asyncio
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    # python3 -m services.company_analysis로 실행하면 이 모듈이 __main__으로 로드되는데,
    # patch("services.company_analysis.high_from_snapshot", ...) 같은 문자열 경로는 별도로
    # "services.company_analysis"를 다시 import해 서로 다른 두 모듈 인스턴스가 생긴다 —
    # sys.modules[__name__]으로 지금 실행 중인 이 인스턴스를 직접 잡아야 실제로 evaluate_fit()이
    # 참조하는 이름이 바뀐다.
    _this = sys.modules[__name__]

    def _snap(provider):
        return SimpleNamespace(provider_name=provider, reasoning_effort=None)

    async def _check():
        # 1. 비-Gemini: fit_report_body가 fit_data에서 빠지고 fit_report로 분리되는지
        fake_high = SimpleNamespace(
            extract_structured=AsyncMock(return_value={
                "fit_score": 80, "fit_label": "적합", "gaps": [], "strengths": [],
                "fit_report_body": "## 4. 적합도 리포트\n\n내용",
            }),
        )
        with patch.object(_this, "high_from_snapshot", return_value=(fake_high, "model")), \
             patch.object(storage, "read_eval_criteria", return_value=""):
            fit_data, fit_report = await evaluate_fit(_snap("claude"), "프로필", {}, "원문", operation="적합도 평가")
        assert "fit_report_body" not in fit_data, fit_data
        assert fit_report == "내용", fit_report
        assert fake_high.extract_structured.call_args.kwargs["operation"] == "적합도 평가"

        # 2. Gemini: location_check 브릿지가 gaps 없을 때 자동 추가되는지
        fake_high_g = SimpleNamespace(
            extract_structured=AsyncMock(return_value={
                "fit_score": 70, "fit_label": "보통", "gaps": [], "strengths": [],
                "location_check": "조건부 - 판교 상주",
            }),
            complete=AsyncMock(return_value="## 4. 적합도 리포트\n\n지역 보정 확인용"),
        )
        with patch.object(_this, "high_from_snapshot", return_value=(fake_high_g, "model")), \
             patch.object(storage, "read_eval_criteria", return_value=""):
            fit_data, fit_report = await evaluate_fit(_snap("gemini"), "프로필", {}, "원문", operation="적합도 재평가")
        assert any("근무지" in g for g in fit_data["gaps"]), fit_data["gaps"]
        assert fake_high_g.extract_structured.call_args.kwargs["operation"] == "적합도 재평가"

        # 3. Gemini: gaps에 이미 근무지 항목이 있으면 중복 추가 안 됨
        fake_high_g2 = SimpleNamespace(
            extract_structured=AsyncMock(return_value={
                "fit_score": 70, "fit_label": "보통", "gaps": ["(하) 근무지 미달 - 지방 근무"], "strengths": [],
                "location_check": "미달",
            }),
            complete=AsyncMock(return_value="## 4. 적합도 리포트\n\n본문"),
        )
        with patch.object(_this, "high_from_snapshot", return_value=(fake_high_g2, "model")), \
             patch.object(storage, "read_eval_criteria", return_value=""):
            fit_data, _ = await evaluate_fit(_snap("gemini"), "프로필", {}, "원문", operation="적합도 평가")
        assert len(fit_data["gaps"]) == 1, fit_data["gaps"]

    asyncio.run(_check())
    print("company_analysis self-check 통과")
