"""Plan B 4단계 — pgvector exact search vs HNSW 근사검색 비교.

같은 12개 질문(`rag.evaluate.QUESTIONS`)에 대해 두 모드로 검색한다.
- exact: `enable_indexscan`/`enable_bitmapscan`을 꺼서 강제로 순차 스캔(HNSW 인덱스 미사용)
- hnsw: `enable_seqscan`을 꺼서 강제로 HNSW 인덱스 사용

이 corpus(청크 194개)는 매우 작아서 플래너가 별다른 강제 없이는 두 모드 모두 순차 스캔을
고를 수 있다(비용 추정상 순차 스캔이 더 쌀 정도로 작음) — `enable_seqscan`까지 같이 제어해야
실제로 HNSW 인덱스를 타는지 검증할 수 있다.

실행: backend/ 에서 `python3 -m rag.postgres.hnsw_eval`
"""
import time

import psycopg

from rag.embed.google import GoogleEmbeddingProvider
from rag.embed.local import LocalEmbeddingProvider
from rag.evaluate import PRECISION_K, QUESTIONS, RECALL_K, TOP_K_CHUNKS, precision_at_k, recall_at_k
from rag.postgres.db import get_connection
from rag.postgres.evaluate import _ground_truth
from rag.postgres.retrieval import ranked_postings_by_score, search_chunks

REPEATS = 5  # 지연시간 p50/p95 측정용 반복 횟수


def _set_mode(conn: psycopg.Connection, exact: bool) -> None:
    if exact:
        conn.execute("SET LOCAL enable_indexscan = off")
        conn.execute("SET LOCAL enable_bitmapscan = off")
        conn.execute("SET LOCAL enable_seqscan = on")
    else:
        conn.execute("SET LOCAL enable_indexscan = on")
        conn.execute("SET LOCAL enable_bitmapscan = on")
        conn.execute("SET LOCAL enable_seqscan = off")


def _percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    idx = min(len(values) - 1, int(len(values) * p))
    return values[idx]


def _timed_search(conn, provider, query: str, exact: bool) -> tuple[list[int], list[float]]:
    durations = []
    ranked: list[int] = []
    for _ in range(REPEATS):
        _set_mode(conn, exact)
        start = time.perf_counter()
        scored = search_chunks(conn, provider, query, source_type="posting_raw", top_k=TOP_K_CHUNKS)
        ranked = ranked_postings_by_score(conn, [(score, chunk_id) for score, chunk_id, _text in scored])
        durations.append(time.perf_counter() - start)
    return ranked, durations


def run() -> None:
    conn = get_connection()
    google = GoogleEmbeddingProvider()
    local = LocalEmbeddingProvider()

    rows = []
    try:
        for qid, question, skill, _keyword in QUESTIONS:
            truth = _ground_truth(conn, skill)
            row = {"qid": qid, "skill": skill}
            for name, provider in [("google", google), ("local", local)]:
                for mode, exact in [("exact", True), ("hnsw", False)]:
                    ranked, durations = _timed_search(conn, provider, question, exact)
                    row[f"{name}_{mode}_p5"] = precision_at_k(ranked, truth, PRECISION_K)
                    row[f"{name}_{mode}_r10"] = recall_at_k(ranked, truth, RECALL_K)
                    row[f"{name}_{mode}_p50"] = _percentile(durations, 0.5) * 1000
                    row[f"{name}_{mode}_p95"] = _percentile(durations, 0.95) * 1000
            rows.append(row)
    finally:
        local.close()

    def avg(key: str) -> float:
        return sum(r[key] for r in rows) / len(rows)

    print(f"{'':6}{'':14} | {'exact P@5/R@10':>16} | {'hnsw P@5/R@10':>16} | {'exact p50/p95(ms)':>18} | {'hnsw p50/p95(ms)':>18}")
    for name in ("google", "local"):
        print(f"-- {name} --")
        for r in rows:
            print(
                f"{r['qid']:6}{r['skill']:14} | "
                f"{r[f'{name}_exact_p5']:.2f}/{r[f'{name}_exact_r10']:.2f}{'':>8} | "
                f"{r[f'{name}_hnsw_p5']:.2f}/{r[f'{name}_hnsw_r10']:.2f}{'':>8} | "
                f"{r[f'{name}_exact_p50']:.1f}/{r[f'{name}_exact_p95']:.1f}{'':>8} | "
                f"{r[f'{name}_hnsw_p50']:.1f}/{r[f'{name}_hnsw_p95']:.1f}"
            )
        print(
            f"{'평균':20} | "
            f"{avg(f'{name}_exact_p5'):.2f}/{avg(f'{name}_exact_r10'):.2f}{'':>8} | "
            f"{avg(f'{name}_hnsw_p5'):.2f}/{avg(f'{name}_hnsw_r10'):.2f}{'':>8} | "
            f"{avg(f'{name}_exact_p50'):.1f}/{avg(f'{name}_exact_p95'):.1f}{'':>8} | "
            f"{avg(f'{name}_hnsw_p50'):.1f}/{avg(f'{name}_hnsw_p95'):.1f}"
        )


if __name__ == "__main__":
    run()
