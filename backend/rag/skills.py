"""Plan A 2단계 — 정확 기술명·동의어 그룹 정의.

`docs/rag-project-plans/01b_evaluation_set.md`의 EX/SY/AG/GP 기대값과 실제 원문(`.raw.txt`)을
grep으로 직접 대조해 검증된 패턴만 담는다(2026-07-22). 패턴은 원문 소문자 검색 기준이며
`re.IGNORECASE`로 매칭한다.
"""

TRACKED_SKILLS: dict[str, list[str]] = {
    # 정확 기술명 (01b EX-01~06) — 기대값과 정확히 일치 검증됨
    "FastAPI": [r"fastapi"],
    "Python": [r"\bpython\b"],
    "Docker": [r"docker"],
    "Airflow": [r"airflow"],
    "Terraform": [r"terraform"],
    "Redis": [r"redis"],
    # 동의어 그룹 (01b SY-01~06)
    "Kubernetes": [r"k8s", r"kubernetes"],
    "PostgreSQL": [r"postgres"],  # "postgres"가 "postgresql"의 부분 문자열이라 패턴 하나로 둘 다 커버
    "AWS": [r"aws", r"amazon web services"],
    "GCP": [r"gcp", r"google cloud"],
    "CI/CD": [r"ci/cd", r"jenkins", r"github actions", r"argocd", r"배포 자동화"],
    "Observability": [r"observability", r"모니터링", r"메트릭", r"트레이싱", r"prometheus", r"grafana", r"elasticsearch"],
    # 01b GP-01/GP-04(개인 gap)에서만 쓰는 별도 그룹.
    # Terraform 단독(정확 기술명, 6건)과는 다른 개념 — "IaC"라는 포괄적 표현만 쓴 공고 1건이 더 있어 7건.
    "IaC": [r"terraform", r"iac"],
}

_SKILL_LOOKUP = {k.lower(): k for k in TRACKED_SKILLS}


def normalize_skill(skill: str) -> str:
    """`TRACKED_SKILLS`와 대소문자만 다른 입력을 정확한 키로 정규화한다. 정규화 없이
    `skill in TRACKED_SKILLS`로 바로 비교하면 "observability"(소문자)가 정확 집계 대신
    `DEMAND_CANDIDATE_MAX`(25건) 상한이 걸린 추정 경로로 빠져 같은 질문인데도 결과가 구조적으로
    달라진다(Codex 리뷰로 발견, 2026-07-29). 매치 안 되면 앞뒤 공백만 제거한 문자열을 반환한다
    (자유 텍스트 주제로 처리) — 원본을 그대로 반환하면 " RAG " 같은 입력이 공백 포함 그대로
    남고, 공백만 있는 입력("   ")도 truthy라 빈 입력 가드를 우회했다(Codex 재검증으로 발견,
    2026-07-29)."""
    stripped = skill.strip()
    return _SKILL_LOOKUP.get(stripped.lower(), stripped)

# CI/CD의 "배포 자동화" 항목은 01b SY-05 기대값(20건)과 AG-05 교집합(11건)을 동시에 만족하는
# 유일한 후보는 아니었음 — "배포 파이프라인"도 독립적으로 20건을 만족했다. 정확한 원 용어는
# Codex에게 확인 필요(00_claude_handoff.md 피드백란 참고).
