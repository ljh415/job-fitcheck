"""회사 적합도 평가 — provider별 분기를 하나로 통합한다. 최초 평가(_process_company)와
재평가(refit_company)가 이 함수를 공유해, 한쪽만 고쳐서 결과가 어긋나는 걸 방지한다."""
import json
import logging
import re

import prompts
import storage
from llm.router import LLMSnapshot, high_from_snapshot
from services import fit_normalization

logger = logging.getLogger(__name__)


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
    `_process_company()`/`refit_company()`(backend/routers/companies.py)가 실제로
    호출하는 현재 활성 경로다 — `evaluate_fit()`은 비교·롤백용으로 코드에 남아있을
    뿐 런타임 호출자가 없다(2026-09-02, 실호출부 연결 커밋 반영).

    1단계(판정 전용) 호출 → 코드가 완결성 검증·jobplanet/location 등급 강제·표
    렌더링·gaps/strengths 파생 → 2단계(보고서 산문 전용) 호출, LLM 호출 2회.

    반환값은 `evaluate_fit()`과 필드 이름이 다르다(`decision_factors`에 중첩된
    값에서 `salary_check`/`stability_check`/`location_check`를
    `fit_normalization.derive_legacy_checks()`로 파생해 같이 채움) — 그래서 옛
    호출부 코드를 그대로 재사용할 수는 없고, 실제로 `routers/companies.py`의
    두 호출부가 이 함수 전용으로 작성돼 있다.
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
    # judge_result 자체가 dict가 아니면(provider가 tool 인자를 배열 등으로 잘못
    # 반환) 바로 아래 .get() 호출에서 AttributeError로 평가 전체가 죽어 fallback
    # 계약에 도달 못 한다 — 유료 1단계 호출은 이미 나간 뒤라 손실이 더 크다.
    # 빈 dict로 정규화해 이후 세 normalizer(reconcile_judgments/
    # validate_decision_factors/safe_fit_score)가 항상 하던 대로 fallback+
    # incomplete 처리하게 한다(2026-09-02 7차 리뷰 반영).
    if not isinstance(judge_result, dict):
        logger.warning("evaluate_fit_structured: judge_result가 dict 아님 — raw=%r", judge_result)
        judge_result = {}

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
    # jobplanet/location은 고정 매핑 가능한 요인이라 LLM의 level 판단을 신뢰하지 않고
    # 회사 원본 데이터(jobplanet_score)·판정 status 텍스트로 코드가 다시 강제한다
    # (PLAN.md 3.2, 2026-09-02 브랜치 전체 리뷰 반영).
    decision_factors = fit_normalization.enforce_deterministic_levels(decision_factors, company_data)
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
            [it for it in normalized if it["id"].startswith(f"{prefix}:")], header, table_kind=prefix
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
        # 구 salary_check/stability_check/location_check 필드 하위 호환(CSV 내보내기·
        # MCP 계약, 4번 열린 질문 결정: B). decision_factors로 대체하지 않고 계속 채운다.
        **fit_normalization.derive_legacy_checks(decision_factors),
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

        # 4. evaluate_fit_structured() — 실제 활성 경로(_process_company/refit_company가
        # 호출하는 함수)를 검증. 위 1~3번은 전부 호출자 없는 evaluate_fit()만 exercise
        # 했었음(2026-09-02 브랜치 전체 리뷰 지적). 1단계 판정 mock에 jobplanet 낮은
        # 점수인데 LLM이 level="없음"으로 놓친 상황을 넣어 enforce_deterministic_levels()
        # 연결까지 같이 확인한다.
        company_data_structured = {
            "required_skills": ["Python 경험"],
            "preferred_skills": [],
            "key_responsibilities": [],
            "jobplanet_score": 2.4,
        }
        fake_high_structured = SimpleNamespace(
            extract_structured=AsyncMock(return_value={
                "fit_score": 65,
                "item_judgments": [
                    {"id": "required:0", "verdict": "met", "evidence_basis": "explicit",
                     "evidence_summary": "프로젝트 X에서 Python 사용", "evidence_source": "X",
                     "evidence_excerpt": "", "reason": "", "severity": "없음"},
                ],
                "decision_factors": {
                    "career_years": {"status": "충족", "level": "없음", "note": ""},
                    "location": {"status": "충족", "level": "없음", "note": ""},
                    "stability": {"status": "충족", "level": "없음", "note": ""},
                    # LLM이 임계값 지시를 놓친 상황(2.4점인데 level="없음") — 코드가 강제해야 함
                    "jobplanet": {"status": "낮음", "level": "없음", "note": "2.4점"},
                    "salary": {"status": "미확인", "level": "없음", "note": ""},
                    "custom_criteria": {"status": "해당없음", "level": "없음", "note": ""},
                },
            }),
            complete=AsyncMock(return_value="종합의견 텍스트"),
        )
        with patch.object(_this, "high_from_snapshot", return_value=(fake_high_structured, "model")), \
             patch.object(storage, "read_eval_criteria", return_value=""):
            fit_result, fit_report = await evaluate_fit_structured(
                _snap("claude"), "프로필", company_data_structured, "원문", operation="적합도 평가",
            )
        assert fit_result["item_judgments"][0]["id"] == "required:0", fit_result["item_judgments"]
        assert fit_result["evaluation_incomplete"] is False, fit_result
        assert fit_result["decision_factors"]["jobplanet"]["level"] == "상", fit_result["decision_factors"]
        assert any(g.startswith("(상)") and "잡플래닛" in g for g in fit_result["gaps"]), fit_result["gaps"]
        assert "종합의견 텍스트" in fit_report, fit_report
        assert fake_high_structured.extract_structured.call_args.kwargs["operation"] == "적합도 평가"
        assert fake_high_structured.complete.call_args.kwargs["operation"] == "적합도 평가(보고서)"

        # 5. judge_result(1단계 응답) 자체가 dict가 아니면(provider가 tool 인자를 배열
        # 등으로 잘못 반환) 예외 없이 fallback+incomplete=true로 넘어가야 함(2026-09-02
        # 7차 리뷰 반영 — 전에는 바로 다음 줄 judge_result.get()에서 AttributeError로
        # 평가 전체가 죽었음).
        fake_high_bad_toplevel = SimpleNamespace(
            extract_structured=AsyncMock(return_value=["이건 배열임 — dict가 아님"]),
            complete=AsyncMock(return_value="종합의견"),
        )
        with patch.object(_this, "high_from_snapshot", return_value=(fake_high_bad_toplevel, "model")), \
             patch.object(storage, "read_eval_criteria", return_value=""):
            fit_result_bad, _ = await evaluate_fit_structured(
                _snap("claude"), "프로필", company_data_structured, "원문", operation="적합도 평가",
            )
        assert fit_result_bad["evaluation_incomplete"] is True, fit_result_bad
        assert fit_result_bad["item_judgments"][0]["filled_by"] == "code_fallback", fit_result_bad["item_judgments"]

    asyncio.run(_check())
    print("company_analysis self-check 통과")
