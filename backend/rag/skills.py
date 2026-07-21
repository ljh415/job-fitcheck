"""Plan A 2단계 — 정확 기술명·동의어 그룹 정의.

`docs/rag-project-plans/01b_evaluation_set.md`의 EX/SY/AG/GP 기대값과 실제 원문(`.raw.txt`)을
grep으로 직접 대조해 검증된 패턴만 담는다(2026-07-22). 패턴은 원문 소문자 검색 기준이며
`re.IGNORECASE`로 매칭한다.

CANDIDATE_EVIDENCE는 `data/candidate_profile.md` 원문을 직접 읽고 분류한 결과다.
개인정보 보호를 위해 프로필 원문은 이 파일에 복사하지 않고 분류 근거만 남긴다.
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

# CI/CD의 "배포 자동화" 항목은 01b SY-05 기대값(20건)과 AG-05 교집합(11건)을 동시에 만족하는
# 유일한 후보는 아니었음 — "배포 파이프라인"도 독립적으로 20건을 만족했다. 정확한 원 용어는
# Codex에게 확인 필요(00_claude_handoff.md 피드백란 참고).

CANDIDATE_EVIDENCE: dict[str, dict] = {}
