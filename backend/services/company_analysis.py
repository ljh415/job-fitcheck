"""회사 적합도 평가 — provider별 분기를 하나로 통합한다. 최초 평가(_process_company)와
재평가(refit_company)가 이 함수를 공유해, 한쪽만 고쳐서 결과가 어긋나는 걸 방지한다."""
import json
import re

import prompts
import storage
from llm.router import LLMSnapshot, high_from_snapshot
from services import fit_normalization


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
        company_json=json.dumps(company_data, ensure_ascii=False),
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


async def evaluate_fit_structured(
    snap: LLMSnapshot,
    profile_text: str,
    company_data: dict,
    raw_text: str,
    operation: str,
) -> tuple[dict, str]:
    """적합도 평가 구조 개편(docs/fit-eval-structural-redesign/PLAN.md) 파이프라인.

    1단계(판정 전용) 호출 → 코드가 완결성 검증·표 렌더링·gaps/strengths 파생 →
    2단계(보고서 산문 전용) 호출. 아직 검증 전이라 `_process_company`/`refit_company`
    호출부에는 연결하지 않았다 — `evaluate_fit()`과 나란히 존재하며 회귀 검증
    (4개 사례) 통과 후 교체 예정.

    주의(Codex 리뷰 2026-09-01 반영): 이 함수의 반환값은 `evaluate_fit()`과
    필드 이름이 다르다(`salary_check`/`stability_check`/`location_check` 대신
    `decision_factors`에 중첩) — "호출부를 그대로 재사용 가능"은 아직 사실이
    아니다. `item_judgments`/`decision_factors`/`evaluation_incomplete`도
    `CompanyFrontmatter`에 선언되지 않아 그대로 저장하면 조용히 버려진다.
    저장 여부·기존 필드 호환은 구현 순서 6번(저장·이력·QnA 제외 계약 확인)에서
    확정한다 — 지금은 함수 자체의 판정 로직만 검증하는 단계.
    """
    high, high_model = high_from_snapshot(snap)
    eval_criteria = storage.read_eval_criteria().strip()
    custom_criteria_section = (
        f"\n\n## 추가 평가 기준 (사용자 지정)\n{eval_criteria}{prompts.CUSTOM_CRITERIA_BOUNDARY_NOTICE}"
        if eval_criteria else ""
    )

    input_items = fit_normalization.build_input_items(company_data)
    item_list_text = "\n".join(f"- {it['id']}: {it['source_item']}" for it in input_items)
    judge_user = prompts.EVALUATE_FIT_JUDGE_USER_TEMPLATE.format(
        candidate_profile=profile_text,
        company_json=json.dumps(company_data, ensure_ascii=False),
        raw_text=raw_text[:4000],
        item_list=item_list_text or "(판정할 항목 없음)",
        tool_name=prompts.EVALUATE_FIT_JUDGE_TOOL_NAME,
        custom_criteria=custom_criteria_section,
    )
    judge_result = await high.extract_structured(
        system=prompts.EVALUATE_FIT_JUDGE_SYSTEM,
        user=judge_user,
        tool_name=prompts.EVALUATE_FIT_JUDGE_TOOL_NAME,
        tool_description=prompts.EVALUATE_FIT_JUDGE_TOOL_DESCRIPTION,
        tool_schema=prompts.EVALUATE_FIT_JUDGE_TOOL_SCHEMA,
        model=high_model,
        operation=operation,
        reasoning_effort=snap.reasoning_effort,
    )

    normalized, items_incomplete = fit_normalization.reconcile_judgments(
        input_items, judge_result.get("item_judgments", [])
    )
    gaps, strengths = fit_normalization.derive_gaps_strengths(normalized)

    # 1단계 응답의 최상위 필드(fit_score/decision_factors)도 provider가 스키마를
    # 서버에서 강제하지 않으므로 여기서 직접 검증한다(2026-09-01, 2차 리뷰 반영) —
    # 값이 없거나 이상해도 조용히 그럴듯한 기본값으로 넘어가지 않고 incomplete로 표시.
    decision_factors, factors_incomplete = fit_normalization.validate_decision_factors(
        judge_result.get("decision_factors") or {}
    )
    gaps = gaps + fit_normalization.derive_decision_factor_gaps(decision_factors)

    fit_score, score_incomplete = fit_normalization.safe_fit_score(judge_result.get("fit_score"))
    incomplete = items_incomplete or factors_incomplete or score_incomplete
    # 점수·라벨은 이미 완전히 결정적인 매핑이므로 LLM 값을 신뢰하지 않고 코드가 계산한다.
    # fit_score를 못 구했으면(위에서 이미 incomplete=True로 표시됨) 라벨 계산을 위한
    # 최소한의 폴백으로만 0을 쓴다.
    fit_label = fit_normalization.label_from_score(fit_score if fit_score is not None else 0)
    fit_score = fit_score if fit_score is not None else 0

    tables = "\n\n".join(
        fit_normalization.render_requirement_table(
            [it for it in normalized if it["id"].startswith(f"{prefix}:")], header
        )
        for prefix, header in (
            ("required", "자격요건 충족 현황"),
            ("preferred", "우대사항 충족 현황"),
            ("responsibility", "직무 적합도 분석"),
        )
        if any(it["id"].startswith(f"{prefix}:") for it in normalized)
    )

    report_user = prompts.EVALUATE_FIT_REPORT_USER_TEMPLATE.format(
        fit_score=fit_score,
        fit_label=fit_label,
        strengths_text="\n".join(f"- {s}" for s in strengths) or "(없음)",
        gaps_text="\n".join(f"- {g}" for g in gaps) or "(없음)",
        decision_factors_text=json.dumps(decision_factors, ensure_ascii=False),
    )
    report_prose = await high.complete(
        system=prompts.EVALUATE_FIT_REPORT_SYSTEM,
        user=report_user,
        model=high_model,
        operation=f"{operation}(보고서)",
        max_tokens=4096,
        reasoning_effort=snap.reasoning_effort,
    )

    fit_report = f"{tables}\n\n{report_prose.strip()}"
    fit_result = {
        "fit_score": fit_score,
        "fit_label": fit_label,
        "gaps": gaps,
        "strengths": strengths,
        "evaluation_incomplete": incomplete,
        "item_judgments": normalized,
        "decision_factors": decision_factors,
    }
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
