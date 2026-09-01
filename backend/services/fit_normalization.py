"""정규화 판정 배열 처리 — 적합도 평가 구조 개편(docs/fit-eval-structural-redesign/PLAN.md)
1단계 산출물을 다룬다. 완결성 검증·코드 fallback·gaps/strengths 파생·표 렌더링을
provider(Claude/OpenAI/Gemini) 공통으로 담당해, 각 provider가 같은 규칙을 따로
구현하지 않게 한다."""
import logging

logger = logging.getLogger(__name__)

# id 접두사 → (company_data의 원본 배열 키, 강점 등급)
_ID_PREFIX_MAP = {
    "required": ("required_skills", "상"),
    "preferred": ("preferred_skills", "중"),
    "responsibility": ("key_responsibilities", "중"),
}

_VALID_VERDICTS = {"met", "unmet", "unclear"}
_VALID_EVIDENCE_BASIS = {"explicit", "assumed", "해당없음"}
_VALID_SEVERITY = {"상", "중", "하"}  # None("없음")은 met일 때만 허용, 별도 처리


def build_input_items(company_data: dict) -> list[dict]:
    """required_skills/preferred_skills/key_responsibilities를 {id, source_item}
    목록으로 변환한다. id는 코드가 부여한다 — LLM은 판정 시 이 id만 그대로 돌려준다."""
    items = []
    for prefix, (key, _grade) in _ID_PREFIX_MAP.items():
        for i, text in enumerate(company_data.get(key, [])):
            items.append({"id": f"{prefix}:{i}", "source_item": text})
    return items


def _code_fallback(item_id: str, source_item: str) -> dict:
    return {
        "id": item_id,
        "source_item": source_item,
        "verdict": "verify",
        "evidence_basis": None,
        "evidence_summary": "시스템이 이 항목에 대한 판정을 받지 못해 자동 표시됨 — 이력서 원문 직접 확인 필요",
        "evidence_source": "",
        "evidence_excerpt": "",
        "reason": "system_no_response",
        "severity": None,
        "filled_by": "code_fallback",
    }


def _is_valid_judgment(item_id: str, verdict, evidence_basis, severity) -> bool:
    """조건부 불변조건 검증 — ID가 정확히 한 번 등장해도 필드 조합 자체가 규칙을
    어기면 무효로 본다.

    항목 종류별 verdict 제약(예: required는 unclear 불가)은 검사하지 않는다 —
    복합 자격요건의 연결어 불명확 예외([복합 자격요건 판정])가 required 항목에도
    합법적으로 unclear를 허용하므로, 코드가 항목 종류만으로 그 예외를 구분할 수
    없다. severity도 명확히 판별 가능한 규칙만 강제한다(2026-09-02, 3차 리뷰로
    정정 — responsibility를 preferred와 같이 묶어 중/하만 허용했던 게 회귀였음):
    - required + unmet → severity는 반드시 "상"
    - preferred + unmet·unclear → severity는 "중" 또는 "하"만
    - responsibility + unmet·unclear → **강제하지 않음**. [심각도 기준]의
      2축 매트릭스(업무 핵심성×경험 일치도)에 따라 "핵심 업무 + 인접 경험뿐"이면
      상도 합법이라, required+unclear와 같은 이유로 항목 id만으로는 검증 불가
      (원문 의미 판단이 필요함).
    """
    if verdict not in _VALID_VERDICTS:
        return False
    if verdict == "met":
        return evidence_basis in ("explicit", "assumed")
    # unmet | unclear — evidence_basis는 met 전용이므로 "해당없음"이어야 한다
    if evidence_basis != "해당없음":
        return False
    if severity not in _VALID_SEVERITY:
        return False
    prefix = item_id.split(":", 1)[0]
    if prefix == "required" and verdict == "unmet" and severity != "상":
        return False
    if prefix == "preferred" and severity not in ("중", "하"):
        return False
    return True


def reconcile_judgments(input_items: list[dict], llm_items: list[dict]) -> tuple[list[dict], bool]:
    """LLM이 반환한 판정 배열을 입력 기준으로 검증·복원한다.

    - 모르는 id(입력에 없던 값)는 폐기한다(로그만 남김).
    - 중복 id는 임의로 하나를 고르지 않고 그 id 전체를 무효화한 뒤 fallback으로 채운다.
    - verdict·evidence_basis·severity 조합이 규칙을 어기면(예: met인데 evidence_basis
      불명, unmet인데 severity 없음) 필드 개수가 맞아도 무효로 보고 fallback으로 채운다.
    - source_item은 LLM 반환값을 신뢰하지 않고 입력값으로 강제 복원한다.
    - severity는 met일 때 null로 강제, "없음" 문자열은 None으로 정규화한다.
    - 최종 순서는 LLM이 돌려준 순서가 아니라 입력 순서를 따른다.
    - 빠진/무효 id는 code_fallback으로 채운다(재요청하지 않음).

    반환: (정규화된 배열, evaluation_incomplete 여부 — fallback이 하나라도 있으면 True)
    """
    by_id: dict[str, list[dict]] = {}
    for raw in llm_items:
        raw_id = raw.get("id")
        if raw_id is None:
            continue
        by_id.setdefault(raw_id, []).append(raw)

    known_ids = {item["id"] for item in input_items}
    unknown_ids = set(by_id) - known_ids
    if unknown_ids:
        logger.warning("reconcile_judgments: 입력에 없는 id 폐기 — %s", sorted(unknown_ids))

    result = []
    incomplete = False
    for item in input_items:
        item_id, source_item = item["id"], item["source_item"]
        candidates = by_id.get(item_id, [])
        if len(candidates) != 1:
            reason = "누락" if not candidates else "중복"
            logger.warning("reconcile_judgments: id=%s %s → code_fallback", item_id, reason)
            result.append(_code_fallback(item_id, source_item))
            incomplete = True
            continue
        raw = candidates[0]
        verdict = raw.get("verdict")
        evidence_basis = raw.get("evidence_basis")
        # 스키마는 severity를 "없음" 문자열로 받는다(도구 호출 스키마의 null 처리 회피,
        # docs/fit-eval-structural-redesign/PLAN.md 스키마 실측 참고) — 내부적으로는 None으로 통일.
        raw_severity = raw.get("severity")
        normalized_severity = None if raw_severity == "없음" else raw_severity

        if not _is_valid_judgment(item_id, verdict, evidence_basis, normalized_severity):
            logger.warning(
                "reconcile_judgments: id=%s 조건 위반(verdict=%r, evidence_basis=%r, severity=%r) → code_fallback",
                item_id, verdict, evidence_basis, raw_severity,
            )
            result.append(_code_fallback(item_id, source_item))
            incomplete = True
            continue

        severity = None if verdict == "met" else normalized_severity
        result.append({
            "id": item_id,
            "source_item": source_item,
            "verdict": verdict,
            "evidence_basis": evidence_basis,
            "evidence_summary": raw.get("evidence_summary", ""),
            "evidence_source": raw.get("evidence_source", ""),
            "evidence_excerpt": raw.get("evidence_excerpt", ""),
            "reason": raw.get("reason", ""),
            "severity": severity,
            "filled_by": "llm",
        })
    return result, incomplete


_LABEL_THRESHOLDS = (
    (85, "강력추천"),
    (70, "추천"),
    (55, "조건부추천"),
    (40, "보류"),
)


def label_from_score(fit_score: int) -> str:
    """점수→라벨 매핑은 이미 완전히 결정적이므로 LLM 응답을 신뢰하지 않고 코드가
    직접 계산한다(EVALUATE_FIT_JUDGE_USER_TEMPLATE의 점수→라벨 기준표와 동일)."""
    for threshold, label in _LABEL_THRESHOLDS:
        if fit_score >= threshold:
            return label
    return "비추천"


def safe_fit_score(raw_score) -> tuple[int | None, bool]:
    """`fit_score`를 안전하게 정수로 변환한다(2026-09-01, 2차 리뷰 반영) — provider가
    도구 응답 스키마를 서버에서 강제 검증하지 않으므로, 값이 없거나 숫자로 변환
    불가능할 수 있다. 이 경우 0점 같은 그럴듯한 잘못된 기본값으로 조용히 넘어가지
    않고 (None, True)를 반환해 `evaluation_incomplete`를 강제한다."""
    try:
        score = int(raw_score)
    except (TypeError, ValueError):
        logger.warning("safe_fit_score: fit_score 변환 실패 — raw=%r", raw_score)
        return None, True
    return max(0, min(100, score)), False


_DECISION_FACTOR_KEYS = ("career_years", "location", "stability", "jobplanet", "salary", "custom_criteria")
_VALID_LEVEL = {"상", "중", "하", "없음"}

# 구 CompanyFrontmatter salary_check/stability_check(Pydantic Literal)와 같은 어휘 —
# validate_decision_factors()의 enum 검증과 derive_legacy_checks()의 매핑 양쪽에서 쓴다.
_SALARY_CHECK_VALUES = {"양호", "미확인", "낮음"}
_STABILITY_CHECK_VALUES = {"충족", "조건부", "미달"}
_STATUS_ENUM_BY_KEY = {"salary": _SALARY_CHECK_VALUES, "stability": _STABILITY_CHECK_VALUES}


def validate_decision_factors(decision_factors: dict) -> tuple[dict, bool]:
    """decision_factors의 6개 요인이 전부 존재하고 `level`이 유효한 enum,
    `status`/`note`가 문자열인지, salary/stability는 status가 구 필드와 같은
    enum인지 검증한다(2026-09-01 2차 리뷰, 2026-09-02 4·5차 리뷰 반영). 위반
    시 임의로 등급을 매기지 않고 안전한 기본값(level="없음", 확인 필요 note)
    으로 교체한 뒤 incomplete로 표시한다. 최상위 `decision_factors` 자체가
    dict가 아니면(provider가 list/문자열/숫자를 반환하는 등) `.get()` 호출이
    `AttributeError`로 죽어 1단계 LLM 호출 비용만 쓰고 평가 전체가 예외로
    끝나는 것을 막기 위해 먼저 빈 dict로 정규화한다(5차 리뷰 반영)."""
    if not isinstance(decision_factors, dict):
        logger.warning("validate_decision_factors: 최상위 타입이 dict 아님 — raw=%r", decision_factors)
        decision_factors = {}
    result = {}
    incomplete = False
    for key in _DECISION_FACTOR_KEYS:
        factor = decision_factors.get(key)
        allowed_status = _STATUS_ENUM_BY_KEY.get(key)
        if (
            not isinstance(factor, dict)
            or factor.get("level") not in _VALID_LEVEL
            or not isinstance(factor.get("status"), str)
            or not isinstance(factor.get("note"), str)
            or (allowed_status is not None and factor.get("status") not in allowed_status)
        ):
            logger.warning("validate_decision_factors: %s 누락 또는 level/status/note 유효하지 않음 — raw=%r", key, factor)
            result[key] = {"status": "확인필요", "level": "없음", "note": "시스템이 이 요인을 판정하지 못함"}
            incomplete = True
        else:
            result[key] = factor
    return result, incomplete


def strength_grade(item_id: str) -> str:
    """항목 id 접두사로 강점 등급을 코드가 결정한다(LLM에 재차 묻지 않음) —
    required:* → 상, preferred:*/responsibility:* → 중 (기존 H 프롬프트의
    [강점 등급 기준]을 그대로 이관)."""
    prefix = item_id.split(":", 1)[0]
    return _ID_PREFIX_MAP.get(prefix, ("", "중"))[1]


def derive_gaps_strengths(items: list[dict]) -> tuple[list[str], list[str]]:
    """정규화 배열에서 gaps/strengths 문자열 배열을 파생한다.

    근거 있는 met       → strength
    assumed met          → 어느 목록에도 안 넣음(결격사유형 "충족 간주")
    unmet / unclear      → 일반 gap (severity 등급 표기)
    code_fallback verify → "(확인필요)" gap (일반 심각도 갭과 구분)
    """
    gaps, strengths = [], []
    for it in items:
        verdict = it["verdict"]
        if verdict == "met":
            if it.get("evidence_basis") == "assumed":
                continue
            grade = strength_grade(it["id"])
            strengths.append(f"({grade}) {it['source_item']} - {it['evidence_summary']}")
        elif verdict == "verify":
            gaps.append(f"(확인필요) {it['source_item']} - {it['evidence_summary']}")
        else:  # unmet | unclear
            severity = it.get("severity") or "중"
            reason = it.get("reason") or it.get("evidence_summary", "")
            gaps.append(f"({severity}) {it['source_item']} - {reason}")
    return gaps, strengths


_DECISION_FACTOR_LABELS = {
    "career_years": "경력 연수",
    "location": "근무지",
    "stability": "기업 안정성",
    "jobplanet": "잡플래닛 평점",
    "custom_criteria": "사용자 지정 평가 기준",
}


def derive_decision_factor_gaps(decision_factors: dict) -> list[str]:
    """decision_factors에서 gaps 문자열을 파생한다. `level`이 "없음"이 아닌
    요인만(=1단계가 문제 있다고 판단한 것만) gap으로 포함한다. `salary`는 이
    맵에 없으므로 절대 파생되지 않는다(기존 규칙: 연봉은 감점 근거로 안 씀)."""
    gaps = []
    for key, label in _DECISION_FACTOR_LABELS.items():
        factor = decision_factors.get(key) or {}
        level = factor.get("level")
        if not level or level == "없음":
            continue
        note = factor.get("note", "")
        status = factor.get("status", "")
        gaps.append(f"({level}) {label} {status} - {note}".replace("  ", " "))
    return gaps


def derive_legacy_checks(decision_factors: dict) -> dict:
    """decision_factors → 구 CompanyFrontmatter 필드(salary_check/stability_check/
    location_check) 매핑. CSV 내보내기·MCP 계약 하위 호환용(4번 열린 질문, 결정:
    B — decision_factors로 대체하지 않고 계속 채운다). status는 프롬프트가 이미
    이 구 필드들과 같은 어휘로 채우도록 지시돼 있으므로(EVALUATE_FIT_JUDGE_SYSTEM
    [decision_factors 판정]) 그대로 옮기되, enum을 벗어나면 버린다(salary_check/
    stability_check는 Pydantic Literal이라 임의 문자열을 넣으면 저장이 깨진다).
    validate_decision_factors()가 status를 문자열로 보장하지만(4차 리뷰 반영),
    이 함수 자체도 비문자열 status가 들어와도 안전하도록 isinstance로 한 번 더
    막는다(set 멤버십 검사에 dict 등 unhashable 값이 들어오면 TypeError)."""
    salary_status = (decision_factors.get("salary") or {}).get("status")
    stability_status = (decision_factors.get("stability") or {}).get("status")
    location_status = (decision_factors.get("location") or {}).get("status")
    return {
        "salary_check": salary_status if isinstance(salary_status, str) and salary_status in _SALARY_CHECK_VALUES else None,
        "stability_check": stability_status if isinstance(stability_status, str) and stability_status in _STABILITY_CHECK_VALUES else None,
        "location_check": location_status if isinstance(location_status, str) and location_status else None,
    }


def _escape_cell(text: str) -> str:
    """마크다운 표 셀 이스케이프 — '|'와 줄바꿈이 표 구조를 깨는 것을 방지한다."""
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


_VERDICT_SYMBOL = {"met": "✅ 충족", "unmet": "❌ 미충족", "unclear": "🔲 불명확", "verify": "🔲 확인필요"}


def render_requirement_table(items: list[dict], header: str) -> str:
    """정규화 배열에서 자격요건/우대사항 표를 코드가 직접 렌더링한다 — LLM이 표를
    다시 쓰면 근거 없이 unmet을 완화 서술하거나 항목을 누락시킬 수 있어서, 사실
    영역은 코드가 결정적으로 만든다."""
    lines = [f"### {header}", "", "| 항목 | 충족 여부 | 근거 |", "|------|----------|------|"]
    for it in items:
        symbol = _VERDICT_SYMBOL.get(it["verdict"], it["verdict"])
        evidence = it.get("evidence_summary") or it.get("reason") or ""
        lines.append(f"| {_escape_cell(it['source_item'])} | {symbol} | {_escape_cell(evidence)} |")
    return "\n".join(lines)


if __name__ == "__main__":
    # self-check — mock 기반, 실제 LLM 호출 없음
    company_data = {
        "required_skills": ["RDB/MongoDB/Elasticsearch 개발 경험", "TensorFlow 또는 PyTorch"],
        "preferred_skills": ["문제 정의 및 해결 의지"],
        "key_responsibilities": [],
    }

    # 1. build_input_items — id 부여 확인
    inputs = build_input_items(company_data)
    assert inputs == [
        {"id": "required:0", "source_item": "RDB/MongoDB/Elasticsearch 개발 경험"},
        {"id": "required:1", "source_item": "TensorFlow 또는 PyTorch"},
        {"id": "preferred:0", "source_item": "문제 정의 및 해결 의지"},
    ], inputs

    # 2. reconcile_judgments — 정상/누락/중복/미지 id 각각 처리
    llm_items = [
        {"id": "required:0", "verdict": "unmet", "evidence_basis": "해당없음", "severity": "상",
         "reason": "MongoDB 경험 없음", "evidence_summary": "MongoDB 경험 없음",
         "source_item": "LLM이 바꿔치기하려는 값 — 무시돼야 함"},
        # required:1 누락
        {"id": "preferred:0", "verdict": "met", "evidence_basis": "explicit",
         "evidence_summary": "Job FitCheck에서 문제 정의·기획·검증 주도"},
        {"id": "preferred:0", "verdict": "unmet"},  # 중복 반환 — 전체 무효화 대상
        {"id": "unknown:99", "verdict": "met"},  # 모르는 id — 폐기
    ]
    result, incomplete = reconcile_judgments(inputs, llm_items)
    assert incomplete is True, "누락·중복이 있으니 evaluation_incomplete=True여야 함"
    by_id = {r["id"]: r for r in result}
    assert by_id["required:0"]["source_item"] == "RDB/MongoDB/Elasticsearch 개발 경험", \
        "source_item은 LLM 반환값이 아니라 입력값으로 복원돼야 함"
    assert by_id["required:0"]["severity"] == "상"
    assert by_id["required:1"]["filled_by"] == "code_fallback", "누락된 id는 fallback"
    assert by_id["required:1"]["verdict"] == "verify"
    assert by_id["required:1"]["severity"] is None, "fallback은 severity 상급 고정 금지"
    assert by_id["preferred:0"]["filled_by"] == "code_fallback", "중복 반환은 전체 무효화 후 fallback"
    assert "unknown:99" not in by_id, "모르는 id는 결과에 없어야 함"
    assert [r["id"] for r in result] == ["required:0", "required:1", "preferred:0"], \
        "순서는 LLM 반환 순서가 아니라 입력 순서"

    # 3. met인데 severity가 왔으면 코드가 null로 강제
    inputs2 = [{"id": "required:0", "source_item": "A"}]
    llm2 = [{"id": "required:0", "verdict": "met", "evidence_basis": "explicit",
             "severity": "상", "evidence_summary": "근거"}]
    result2, incomplete2 = reconcile_judgments(inputs2, llm2)
    assert incomplete2 is False
    assert result2[0]["severity"] is None, "met은 severity를 코드가 null로 강제해야 함"

    # 3-1. "없음" 문자열은 severity로 넘어오면 None으로 정규화된다는 것 자체는 met 케이스로 이미 확인됨(위 2번,
    # required:0의 최초 llm_items에는 severity="상"이 있었으므로 met이 아닌 케이스로 별도 확인:
    # met + evidence_basis 정상 + severity="없음"(→None 정규화, met이라 어차피 무시됨)은 유효해야 함
    llm3 = [{"id": "required:0", "verdict": "met", "evidence_basis": "explicit", "severity": "없음",
             "evidence_summary": "근거"}]
    result3, incomplete3 = reconcile_judgments(inputs2, llm3)
    assert incomplete3 is False
    assert result3[0]["severity"] is None

    # 3-2. 조건부 불변조건 위반은 필드가 다 채워져 있어도 무효 → fallback (Codex 리뷰 2026-09-01 지적)
    # met인데 evidence_basis가 "해당없음"(met에는 explicit/assumed만 허용)
    llm4 = [{"id": "required:0", "verdict": "met", "evidence_basis": "해당없음", "evidence_summary": "근거 없음"}]
    result4, incomplete4 = reconcile_judgments(inputs2, llm4)
    assert incomplete4 is True, "met+evidence_basis=해당없음은 무효로 fallback 처리돼야 함"
    assert result4[0]["filled_by"] == "code_fallback"
    # unmet인데 severity가 "없음"(정규화 후 None) — unmet/unclear는 severity가 상/중/하 중 하나여야 함
    llm5 = [{"id": "required:0", "verdict": "unmet", "severity": "없음", "reason": "이유"}]
    result5, incomplete5 = reconcile_judgments(inputs2, llm5)
    assert incomplete5 is True, "unmet인데 severity가 없으면 무효로 fallback 처리돼야 함"
    assert result5[0]["filled_by"] == "code_fallback"
    # verdict가 enum 밖 값
    llm6 = [{"id": "required:0", "verdict": "확실히충족", "severity": "없음"}]
    result6, incomplete6 = reconcile_judgments(inputs2, llm6)
    assert incomplete6 is True, "enum 밖 verdict는 무효로 fallback 처리돼야 함"

    # 3-3. label_from_score — 점수→라벨 매핑은 코드가 결정
    assert label_from_score(85) == "강력추천"
    assert label_from_score(84) == "추천"
    assert label_from_score(70) == "추천"
    assert label_from_score(69) == "조건부추천"
    assert label_from_score(55) == "조건부추천"
    assert label_from_score(54) == "보류"
    assert label_from_score(40) == "보류"
    assert label_from_score(39) == "비추천"
    assert label_from_score(0) == "비추천"

    # 3-4. 항목 종류별 severity 강제 (2026-09-01, 2차 리뷰 반영, 2026-09-02 3차 리뷰로
    # responsibility 범위 정정) — required+unmet은 반드시 상, preferred+unmet·unclear는
    # 중/하만 허용. responsibility는 상/중/하 전부 합법이라 강제하지 않음.
    llm7 = [{"id": "required:0", "verdict": "unmet", "evidence_basis": "해당없음", "severity": "하", "reason": "이유"}]
    result7, incomplete7 = reconcile_judgments(inputs2, llm7)
    assert incomplete7 is True, "required+unmet인데 severity가 상이 아니면 무효(fallback)여야 함"

    inputs_pref = [{"id": "preferred:0", "source_item": "P"}]
    llm8 = [{"id": "preferred:0", "verdict": "unmet", "evidence_basis": "해당없음", "severity": "상", "reason": "이유"}]
    result8, incomplete8 = reconcile_judgments(inputs_pref, llm8)
    assert incomplete8 is True, "preferred+unmet인데 severity가 상이면 무효(fallback)여야 함"

    # 3-4-1. responsibility+unmet+severity="상"은 합법(핵심 업무+인접 경험뿐인 경우) — 회귀 방지
    inputs_resp = [{"id": "responsibility:0", "source_item": "R"}]
    llm10 = [{"id": "responsibility:0", "verdict": "unmet", "evidence_basis": "해당없음", "severity": "상", "reason": "핵심 업무+인접 경험뿐"}]
    result10, incomplete10 = reconcile_judgments(inputs_resp, llm10)
    assert incomplete10 is False, "responsibility+unmet+severity=상은 [심각도 기준]상 합법이라 fallback 안 돼야 함(2026-09-02 3차 리뷰 회귀 수정)"
    assert result10[0]["severity"] == "상"

    # 3-5. unmet인데 evidence_basis가 "assumed"(met 전용)면 무효
    llm9 = [{"id": "required:0", "verdict": "unmet", "evidence_basis": "assumed", "severity": "상"}]
    result9, incomplete9 = reconcile_judgments(inputs2, llm9)
    assert incomplete9 is True, "unmet에 evidence_basis=assumed는 무효(fallback)여야 함"

    # 3-6. safe_fit_score — 누락·비숫자는 None+incomplete, 정상값은 그대로, 범위 밖은 clamp
    assert safe_fit_score(72) == (72, False)
    assert safe_fit_score(None) == (None, True)
    assert safe_fit_score("not-a-number") == (None, True)
    assert safe_fit_score(150) == (100, False), "범위 밖 점수는 clamp"
    assert safe_fit_score(-5) == (0, False)

    # 3-7. validate_decision_factors — 누락되거나 level이 enum 밖이면 안전한 기본값+incomplete
    valid_factors = {k: {"status": "충족", "level": "없음", "note": ""} for k in _DECISION_FACTOR_KEYS}
    valid_factors["salary"] = {"status": "양호", "level": "없음", "note": ""}
    valid_factors["stability"] = {"status": "충족", "level": "없음", "note": ""}
    result_ok, incomplete_ok = validate_decision_factors(valid_factors)
    assert incomplete_ok is False
    assert result_ok == valid_factors

    # 3-7-2. 최상위 decision_factors 자체가 dict가 아니면 AttributeError 없이
    # 전부 fallback+incomplete로 정규화(5차 리뷰 반영 — provider가 list/문자열/
    # 숫자를 반환해도 죽지 않아야 함)
    for bad_top in ([], "bad", 1, None):
        result_bad_top, incomplete_bad_top = validate_decision_factors(bad_top)
        assert incomplete_bad_top is True, bad_top
        assert all(result_bad_top[k]["level"] == "없음" for k in _DECISION_FACTOR_KEYS), bad_top

    # 3-7-3. salary/stability는 status가 구 필드 enum 밖이면 level이 정상이어도 무효
    # (schema상 허용되는 임의 문자열이 evaluation_incomplete=false로 통과해 legacy
    # 필드만 조용히 None이 되는 정보 유실 방지, 5차 리뷰 반영)
    enum_violation = dict(valid_factors)
    enum_violation["salary"] = {"status": "협의", "level": "없음", "note": ""}
    enum_violation["stability"] = {"status": "강", "level": "없음", "note": ""}
    result_enum, incomplete_enum = validate_decision_factors(enum_violation)
    assert incomplete_enum is True
    assert result_enum["salary"]["status"] == "확인필요"
    assert result_enum["stability"]["status"] == "확인필요"

    broken_factors = dict(valid_factors)
    broken_factors["jobplanet"] = {"status": "낮음", "level": "최상", "note": "2.4점"}  # enum 밖 값
    del broken_factors["salary"]  # 통째로 누락
    result_broken, incomplete_broken = validate_decision_factors(broken_factors)
    assert incomplete_broken is True
    assert result_broken["jobplanet"]["level"] == "없음", "enum 밖 level은 그대로 노출되면 안 됨"
    assert result_broken["salary"]["level"] == "없음", "누락된 요인은 안전한 기본값으로 채워져야 함"

    # 3-7-1. status가 dict 등 비문자열이면 level이 유효해도 무효 처리(4차 리뷰 반영 —
    # 안 걸러지면 derive_legacy_checks()가 그대로 옮겨 location_check(str) 저장이 깨짐)
    bad_status_factors = dict(valid_factors)
    bad_status_factors["location"] = {"status": {"nested": "dict"}, "level": "없음", "note": ""}
    result_bad_status, incomplete_bad_status = validate_decision_factors(bad_status_factors)
    assert incomplete_bad_status is True
    assert result_bad_status["location"]["status"] == "확인필요", "status가 비문자열이면 안전한 기본값으로 교체"

    # 4. derive_gaps_strengths — met(explicit)→strength, met(assumed)→제외, unmet→gap, verify→확인필요 gap
    items = [
        {"id": "required:0", "source_item": "A", "verdict": "met", "evidence_basis": "explicit",
         "evidence_summary": "근거A"},
        {"id": "required:1", "source_item": "B", "verdict": "met", "evidence_basis": "assumed",
         "evidence_summary": "평가상 충족 간주"},
        {"id": "preferred:0", "source_item": "C", "verdict": "unmet", "severity": "중", "reason": "이유C"},
        {"id": "preferred:1", "source_item": "D", "verdict": "verify", "evidence_summary": "안내문구",
         "filled_by": "code_fallback"},
    ]
    gaps, strengths = derive_gaps_strengths(items)
    assert strengths == ["(상) A - 근거A"], strengths
    assert gaps == ["(중) C - 이유C", "(확인필요) D - 안내문구"], gaps

    # 5. render_requirement_table — 이스케이프 확인
    table_items = [
        {"id": "required:0", "source_item": "RDB/Mongo | 위험문자", "verdict": "unmet",
         "evidence_summary": "근거\n줄바꿈 포함"},
    ]
    table = render_requirement_table(table_items, "자격요건 충족 현황")
    assert "RDB/Mongo \\| 위험문자" in table, table
    assert "근거 줄바꿈 포함" in table, "셀 안 줄바꿈은 공백으로 치환돼야 함"

    # 6. derive_decision_factor_gaps — level이 "없음"이면 제외, salary는 애초에 안 넣으면 파생 안 됨
    decision_factors = {
        "career_years": {"status": "미달", "level": "중", "note": "요구 3년, 보유 1년"},
        "location": {"status": "충족", "level": "없음", "note": "서울 일치"},
        "jobplanet": {"status": "낮음", "level": "상", "note": "2.4점"},
        "salary": {"status": "낮음", "level": "상", "note": "이 level은 무시돼야 함"},
    }
    df_gaps = derive_decision_factor_gaps(decision_factors)
    assert any("경력 연수" in g and g.startswith("(중)") for g in df_gaps), df_gaps
    assert not any("근무지" in g for g in df_gaps), "level=없음은 파생되면 안 됨"
    assert not any("연봉" in g or "salary" in g.lower() for g in df_gaps), \
        "salary는 _DECISION_FACTOR_LABELS에 없으므로 절대 파생되면 안 됨"
    assert any("잡플래닛" in g and g.startswith("(상)") for g in df_gaps), df_gaps

    # 7. derive_legacy_checks — status를 구 필드 어휘로 그대로 옮기되 enum 밖이면 버림
    legacy = derive_legacy_checks(decision_factors)
    assert legacy == {"salary_check": "낮음", "stability_check": None, "location_check": "충족"}, legacy
    assert derive_legacy_checks({}) == {"salary_check": None, "stability_check": None, "location_check": None}
    bad = derive_legacy_checks({"salary": {"status": "괜찮음"}})  # enum 밖 값은 버림
    assert bad["salary_check"] is None, bad
    # status가 비문자열이면(validate_decision_factors가 걸러야 정상이지만, 이 함수
    # 자체도 방어해야 함 — set 멤버십에 dict가 들어오면 TypeError로 죽을 수 있음)
    unsafe = derive_legacy_checks({"salary": {"status": {"x": 1}}, "location": {"status": {"y": 2}}})
    assert unsafe == {"salary_check": None, "stability_check": None, "location_check": None}, unsafe

    print("fit_normalization self-check 통과")
