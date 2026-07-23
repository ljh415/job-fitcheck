"""Plan A 5단계 — 검색 정식 평가 (01b_evaluation_set.md의 A/B 카테고리, 12개 질문).

C~F(집계·개인 gap·행동계획·답변불가)는 검색 결과 위에 LLM 추론이 필요해서 6~7단계
(Gap 엔진·답변 생성) 몫으로 남기고, 이번엔 순수 검색 품질(Recall@10, Precision@5)만
FTS5(키워드)·Google·Local 세 방식으로 비교한다.

정답(어떤 공고가 그 기술과 관련 있는지)은 새로 만들지 않고 2단계에서 이미 채워둔
`posting_skill` 테이블(skills.py의 TRACKED_SKILLS 기준)을 그대로 정답지로 쓴다 —
01b의 EX/SY 기대값과 이 테이블 집계가 이미 검증된 상태였음(verify_step2.py).

실행: backend/ 에서 `python3 -m rag.evaluate`
"""
import sqlite3

from rag.embed.base import EmbeddingProvider
from rag.embed.google import GoogleEmbeddingProvider
from rag.embed.local import LocalEmbeddingProvider
from rag.ingest import DB_PATH
from rag.retrieval import ensure_fts5, ranked_postings_by_score, search_chunks, search_fts5


class _BgeM3Query(EmbeddingProvider):
    """BGE-M3 비교용 — 이미 채워진 chunk_embedding(provider='local', model='BAAI/bge-m3')을
    조회만 하면 되므로 embed_documents()는 안 쓰고 embed_query()만 구현한다.
    3050Ti 추론 서버가 지금 BGE-M3로 떠 있어야 동작한다(임시 실험용, local.py의
    EXPECTED_MODEL 검증을 우회하려고 별도 클래스로 둠 — 정식 provider로 승격 전까지는
    이렇게 둔다)."""
    provider_name = "local"
    model = "BAAI/bge-m3"
    dimensions = 1024

    def __init__(self):
        import subprocess
        import time
        self._tunnel = subprocess.Popen([
            "ssh", "-N", "-L", "8501:127.0.0.1:8500",
            "-i", "/home/jhlee/.ssh/id_ed25519_rag3050ti", "-p", "10222",
            "jaeho_rog@121.131.168.179",
        ])
        time.sleep(3)

    def embed_documents(self, texts):
        raise NotImplementedError("이미 채워진 임베딩만 조회 — 문서 재임베딩은 try_bge_m3.py 참고")

    def embed_query(self, text: str) -> list[float]:
        import httpx
        r = httpx.post("http://127.0.0.1:8501/embed_query", json={"text": text}, timeout=30)
        r.raise_for_status()
        return r.json()["vector"]

    def close(self):
        self._tunnel.terminate()

# (질문ID, 01b 질문 문장, posting_skill.skill 값, FTS5용 키워드-only 질의)
# FTS5는 의미 확장을 못 하므로 질문 문장이 아니라 표면 키워드 하나만 넣어서
# "동의어를 놓치는지"를 그대로 드러낸다(SY 질문에서 의도적으로 원어/약어 한쪽만 사용).
QUESTIONS: list[tuple[str, str, str, str]] = [
    ("EX-01", "FastAPI를 명시한 공고 중 관련성이 높은 근거 5개를 찾아줘.", "FastAPI", "FastAPI"),
    ("EX-02", "Python을 명시한 공고 중 관련성이 높은 근거 5개를 찾아줘.", "Python", "Python"),
    ("EX-03", "Docker를 명시한 공고 중 관련성이 높은 근거 5개를 찾아줘.", "Docker", "Docker"),
    ("EX-04", "Airflow를 명시한 공고 중 관련성이 높은 근거 5개를 찾아줘.", "Airflow", "Airflow"),
    ("EX-05", "Terraform을 명시한 공고 중 관련성이 높은 근거 5개를 찾아줘.", "Terraform", "Terraform"),
    ("EX-06", "Redis를 명시한 공고의 근거를 찾아줘.", "Redis", "Redis"),
    ("SY-01", "K8s와 관련된 공고를 찾아줘.", "Kubernetes", "K8s"),
    ("SY-02", "Postgres 경험과 관련된 공고를 찾아줘.", "PostgreSQL", "Postgres"),
    # SY-03/04는 여러 단어로 된 구(phrase) 검색이라 원래부터 직접 큰따옴표를 넣어뒀는데,
    # search_fts5()가 이제 fts5_literal()로 항상 자동 감싸므로 여기서 중복으로 감싸면 안 된다
    # (Codex 리뷰로 인한 FTS5 안전 처리 변경, 2026-07-23 — 상세는 retrieval.py 참고).
    ("SY-03", "Amazon Web Services 경험과 관련된 공고를 찾아줘.", "AWS", "Amazon Web Services"),
    ("SY-04", "Google Cloud 경험과 관련된 공고를 찾아줘.", "GCP", "Google Cloud"),
    ("SY-05", "배포 자동화나 CI/CD 경험과 관련된 공고를 찾아줘.", "CI/CD", "배포 자동화"),
    ("SY-06", "서비스 관측성과 관련된 공고를 찾아줘.", "Observability", "관측성"),
]

TOP_K_CHUNKS = 60  # posting 중복 제거 전 넉넉히 가져올 청크 수
PRECISION_K = 5
RECALL_K = 10


def _ground_truth(conn: sqlite3.Connection, skill: str) -> set[int]:
    rows = conn.execute("SELECT posting_id FROM posting_skill WHERE skill = ?", (skill,)).fetchall()
    return {r[0] for r in rows}


def search_embedding(conn: sqlite3.Connection, provider: EmbeddingProvider, query: str) -> list[int]:
    scored = search_chunks(conn, provider, query, source_type="posting_raw", top_k=TOP_K_CHUNKS)
    return ranked_postings_by_score(conn, [(score, chunk_id) for score, chunk_id, _text in scored])


def precision_at_k(ranked: list[int], truth: set[int], k: int) -> float:
    top = ranked[:k]
    if not top:
        return 0.0
    return len(set(top) & truth) / len(top)


def recall_at_k(ranked: list[int], truth: set[int], k: int) -> float:
    if not truth:
        return 0.0
    top = set(ranked[:k])
    return len(top & truth) / len(truth)


def run() -> None:
    conn = sqlite3.connect(DB_PATH)
    ensure_fts5(conn)

    google = GoogleEmbeddingProvider()
    local = LocalEmbeddingProvider()

    results = []
    try:
        for qid, question, skill, keyword in QUESTIONS:
            truth = _ground_truth(conn, skill)
            fts_ranked = search_fts5(conn, keyword, top_k=TOP_K_CHUNKS)
            google_ranked = search_embedding(conn, google, question)
            local_ranked = search_embedding(conn, local, question)

            row = {"qid": qid, "skill": skill, "truth_n": len(truth)}
            for name, ranked in [("fts5", fts_ranked), ("google", google_ranked), ("local", local_ranked)]:
                row[f"{name}_p5"] = precision_at_k(ranked, truth, PRECISION_K)
                row[f"{name}_r10"] = recall_at_k(ranked, truth, RECALL_K)
            results.append(row)
    finally:
        local.close()

    header = f"{'ID':6}{'skill':14}{'정답수':>6} | {'FTS5 P@5/R@10':>16} | {'Google P@5/R@10':>18} | {'Local P@5/R@10':>16}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['qid']:6}{r['skill']:14}{r['truth_n']:>6} | "
            f"{r['fts5_p5']:.2f}/{r['fts5_r10']:.2f}{'':>8} | "
            f"{r['google_p5']:.2f}/{r['google_r10']:.2f}{'':>10} | "
            f"{r['local_p5']:.2f}/{r['local_r10']:.2f}"
        )

    def avg(key: str) -> float:
        return sum(r[key] for r in results) / len(results)

    print("-" * len(header))
    print(
        f"{'평균':20}{'':>6} | "
        f"{avg('fts5_p5'):.2f}/{avg('fts5_r10'):.2f}{'':>8} | "
        f"{avg('google_p5'):.2f}/{avg('google_r10'):.2f}{'':>10} | "
        f"{avg('local_p5'):.2f}/{avg('local_r10'):.2f}"
    )


if __name__ == "__main__":
    run()
