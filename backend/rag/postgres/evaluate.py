"""Plan B 3단계 — pgvector exact search 검색 품질 평가.

`rag/evaluate.py`(SQLite, Plan A)의 질문 세트·지표 함수는 그대로 재사용하고(순수 파이썬이라
저장소와 무관), 검색 자체만 pgvector exact nearest-neighbor로 바꿔서 돌린다. FTS5/하이브리드
비교는 이번 범위에서 뺐다(Stage 4 몫) — Plan A의 FTS5 수치(`01d_step5_retrieval_evaluation.md`)를
그대로 비교 기준선으로 참고한다.

종료 조건: 같은 임베딩 벡터로 같은 코사인 계산을 하므로, Google/Local 수치가 Plan A(SQLite)
결과와 사실상 동일해야 한다 — 다르면 포팅 버그가 있다는 뜻.

실행: backend/ 에서 `python3 -m rag.postgres.evaluate`
"""
import psycopg

from rag.embed.base import EmbeddingProvider
from rag.embed.google import GoogleEmbeddingProvider
from rag.embed.local import LocalEmbeddingProvider
from rag.evaluate import PRECISION_K, QUESTIONS, RECALL_K, TOP_K_CHUNKS, precision_at_k, recall_at_k
from rag.postgres.db import get_connection
from rag.postgres.retrieval import ranked_postings_by_score, search_chunks

# Plan A(SQLite) 기준선 — 01d_step5_retrieval_evaluation.md, EX+SY 12개 질문 평균
PLAN_A_BASELINE = {"fts5": (0.75, 0.41), "google": (0.68, 0.33), "local": (0.65, 0.42)}


def _ground_truth(conn: psycopg.Connection, skill: str) -> set[int]:
    rows = conn.execute("SELECT posting_id FROM posting_skill WHERE skill = %s", (skill,)).fetchall()
    return {r[0] for r in rows}


def search_embedding(conn: psycopg.Connection, provider: EmbeddingProvider, query: str) -> list[int]:
    scored = search_chunks(conn, provider, query, source_type="posting_raw", top_k=TOP_K_CHUNKS)
    return ranked_postings_by_score(conn, [(score, chunk_id) for score, chunk_id, _text in scored])


def run() -> None:
    conn = get_connection()
    google = GoogleEmbeddingProvider()
    local = LocalEmbeddingProvider()

    results = []
    try:
        for qid, question, skill, _keyword in QUESTIONS:
            truth = _ground_truth(conn, skill)
            google_ranked = search_embedding(conn, google, question)
            local_ranked = search_embedding(conn, local, question)

            row = {"qid": qid, "skill": skill, "truth_n": len(truth)}
            for name, ranked in [("google", google_ranked), ("local", local_ranked)]:
                row[f"{name}_p5"] = precision_at_k(ranked, truth, PRECISION_K)
                row[f"{name}_r10"] = recall_at_k(ranked, truth, RECALL_K)
            results.append(row)
    finally:
        local.close()

    header = f"{'ID':6}{'skill':14}{'정답수':>6} | {'Google P@5/R@10':>18} | {'Local P@5/R@10':>16}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['qid']:6}{r['skill']:14}{r['truth_n']:>6} | "
            f"{r['google_p5']:.2f}/{r['google_r10']:.2f}{'':>10} | "
            f"{r['local_p5']:.2f}/{r['local_r10']:.2f}"
        )

    def avg(key: str) -> float:
        return sum(r[key] for r in results) / len(results)

    print("-" * len(header))
    print(
        f"{'평균(Postgres)':20}{'':>6} | "
        f"{avg('google_p5'):.2f}/{avg('google_r10'):.2f}{'':>10} | "
        f"{avg('local_p5'):.2f}/{avg('local_r10'):.2f}"
    )
    print(
        f"{'평균(Plan A/SQLite)':20}{'':>6} | "
        f"{PLAN_A_BASELINE['google'][0]:.2f}/{PLAN_A_BASELINE['google'][1]:.2f}{'':>10} | "
        f"{PLAN_A_BASELINE['local'][0]:.2f}/{PLAN_A_BASELINE['local'][1]:.2f}"
        f"  (FTS5 기준선: {PLAN_A_BASELINE['fts5'][0]:.2f}/{PLAN_A_BASELINE['fts5'][1]:.2f}, Stage 4에서 재구현 예정)"
    )


if __name__ == "__main__":
    run()
