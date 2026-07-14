def build_message(materials: dict, bold=lambda s: s) -> str:
    """알림 재료(dict)를 채널 공통 텍스트로 조립한다. bold()로 채널별 강조 문법만 주입."""
    if materials.get("kind") == "weekly_summary":
        return _build_weekly_summary(materials, bold)
    return _build_analysis(materials, bold)


def _build_analysis(materials: dict, bold) -> str:
    header = [bold(f"✅ {materials['company']} 분석 완료")]
    if materials.get("job_title"):
        header.append(materials["job_title"])
    if materials.get("score"):
        header.append(f"{materials['score']}점, {materials.get('label', '')}")
    blocks = ["\n".join(header)]

    if materials.get("strengths"):
        titles = "\n".join(f"• {t}" for t in materials["strengths"])
        blocks.append(f"{bold('👍 강점')}\n{titles}")
    if materials.get("gaps"):
        titles = "\n".join(f"• {t}" for t in materials["gaps"])
        blocks.append(f"{bold('👎 갭')}\n{titles}")

    extra = []
    if materials.get("jobplanet"):
        extra.append(f"⭐ 잡플래닛 {materials['jobplanet']}")
    if materials.get("employee_count"):
        extra.append(f"👥 임직원 {materials['employee_count']}")
    if extra:
        blocks.append("\n".join(extra))

    return "\n\n".join(blocks)


def _build_weekly_summary(materials: dict, bold) -> str:
    blocks = [f"{bold('📅 주간 지원 현황 요약')}\n{materials.get('period', '')}"]

    blocks.append(f"🆕 이번 주 신규 등록: {materials.get('new_count', 0)}건")

    status_counts = materials.get("status_counts") or {}
    if status_counts:
        counts = "\n".join(f"• {status}: {count}건" for status, count in status_counts.items())
        blocks.append(f"{bold('📊 상태별 현황')}\n{counts}")

    neglected = materials.get("neglected") or []
    if neglected:
        items = "\n".join(f"• {c['name']} ({c['status']}, {c['days']}일 경과)" for c in neglected)
        blocks.append(f"{bold('⏰ 방치된 항목 — 어떻게 진행중인가요?')}\n{items}")

    return "\n\n".join(blocks)
