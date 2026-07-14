def build_message(materials: dict, bold=lambda s: s) -> str:
    """알림 재료(dict)를 채널 공통 텍스트로 조립한다. bold()로 채널별 강조 문법만 주입."""
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
