"""Plan B 4단계 — pgvector exact search vs HNSW 근사검색 비교.

**2026-07-23 재작성(Codex 리뷰로 발견된 두 가지 결함 수정):**
1. 기존 코드는 타이머 **안에서** 매번 `provider.embed_query()`를 다시 호출해서, 잰 시간의
   대부분이 DB 검색이 아니라 원격 임베딩 API/SSH 왕복이었다 — 질의 임베딩은 질문당 한 번만
   계산해서 재사용한다.
2. `enable_seqscan=off`만으로는 HNSW 사용이 보장되지 않는다 — 실제로 `EXPLAIN`을 찍어보면
   `gap.py`가 쓰는 실제 질의(provider/model/source_type 필터 + document_chunk 조인)는 필터 후
   후보가 194건 중 수십 건뿐이라, 인덱스 스캔을 강제해도 플래너가 HNSW보다 저렴한 다른 인덱스
   (`chunk_embedding_chunk_id_provider_model_dimensions_key`)나 Nested Loop을 골라버려서
   HNSW를 실제로 쓴 적이 없었다. 그래서 이번엔 (a) 실제 질의에서 어떤 스캔 방식이 쓰였는지
   EXPLAIN으로 매번 확인해서 그대로 보고하고, (b) HNSW 인덱스 자체는 정상 동작하는지 확인하는
   별도의 최소 조건 질의(필터 없이 `vector IS NOT NULL`만)로 한 번 검증한다.

실행: backend/ 에서 `python3 -m rag.postgres.hnsw_eval`
"""
import re
import time

import psycopg

from rag.embed.google import GoogleEmbeddingProvider
from rag.embed.local import LocalEmbeddingProvider
from rag.evaluate import PRECISION_K, QUESTIONS, RECALL_K, TOP_K_CHUNKS, precision_at_k, recall_at_k
from rag.postgres.db import get_connection
from rag.postgres.evaluate import _ground_truth
from rag.postgres.pipeline import _VECTOR_COLUMN
from rag.postgres.retrieval import ranked_postings_by_score

REPEATS = 5  # 지연시간 p50/p95 측정용 반복 횟수

_QUERY_SQL = (
    "SELECT 1 - (ce.{column} <=> %s::vector) AS score, ce.chunk_id, dc.text"
    " FROM chunk_embedding ce JOIN document_chunk dc ON dc.id = ce.chunk_id"
    " WHERE ce.provider = %s AND ce.model = %s AND ce.dimensions = %s AND dc.source_type = %s"
    " ORDER BY ce.{column} <=> %s::vector LIMIT %s"
)


def _set_mode(conn: psycopg.Connection, exact: bool) -> None:
    if exact:
        conn.execute("SET LOCAL enable_indexscan = off")
        conn.execute("SET LOCAL enable_bitmapscan = off")
        conn.execute("SET LOCAL enable_seqscan = on")
    else:
        conn.execute("SET LOCAL enable_indexscan = on")
        conn.execute("SET LOCAL enable_bitmapscan = on")
        conn.execute("SET LOCAL enable_seqscan = off")


def _search_by_vector(conn, provider, qvec: list[float], top_k: int, source_type: str) -> list[tuple[float, int, str]]:
    """이미 계산된 질의 벡터로 검색한다(질문마다 임베딩을 한 번만 계산해 재사용하기 위함)."""
    column = _VECTOR_COLUMN[provider.dimensions]
    sql = _QUERY_SQL.format(column=column)
    params = (qvec, provider.provider_name, provider.model, provider.dimensions, source_type, qvec, top_k)
    return conn.execute(sql, params).fetchall()


def _explain_scan_node(conn, provider, qvec: list[float], top_k: int, source_type: str) -> str:
    """실제 질의가 chunk_embedding을 어떤 방식으로 스캔했는지 EXPLAIN에서 추출한다."""
    column = _VECTOR_COLUMN[provider.dimensions]
    sql = "EXPLAIN (FORMAT TEXT) " + _QUERY_SQL.format(column=column)
    params = (qvec, provider.provider_name, provider.model, provider.dimensions, source_type, qvec, top_k)
    plan = "\n".join(r[0] for r in conn.execute(sql, params).fetchall())
    m = re.search(r"(Seq Scan|Bitmap Heap Scan|Index Scan using \S+) on chunk_embedding", plan)
    return m.group(1) if m else "(불명)"


def _verify_hnsw_index_itself_works(conn, provider) -> str:
    """provider/source_type 필터 없이(HNSW partial index 조건만 만족) 순수 벡터 검색을 돌려,
    이 corpus에서도 최소한 인덱스 자체는 정상적으로 쓰일 수 있는지 한 번 확인한다."""
    column = _VECTOR_COLUMN[provider.dimensions]
    qvec = provider.embed_query("스모크 테스트")
    conn.execute("SET LOCAL enable_seqscan = off")
    conn.execute("SET LOCAL enable_bitmapscan = off")
    plan = "\n".join(
        r[0] for r in conn.execute(
            f"EXPLAIN (FORMAT TEXT) SELECT chunk_id FROM chunk_embedding"
            f" WHERE {column} IS NOT NULL ORDER BY {column} <=> %s::vector LIMIT 10",
            (qvec,),
        ).fetchall()
    )
    m = re.search(r"Index Scan using (\S+) on chunk_embedding", plan)
    return m.group(1) if m else "(HNSW 미사용 — 인덱스 자체도 이 조건에서 안 쓰임)"


def _percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    idx = min(len(values) - 1, int(len(values) * p))
    return values[idx]


def _timed_search(conn, provider, qvec: list[float], exact: bool) -> tuple[list[int], list[float], str]:
    """벡터 검색 SQL 자체만 타이머로 잰다 — `ranked_postings_by_score()`가 chunk_id마다 별도
    SQL(N+1)을 실행해서, 원래는 그것까지 타이머 안에 포함돼 exact/HNSW 인덱스 자체의 차이를
    가리고 있었다(Codex 재리뷰로 발견, 2026-07-23). posting 매핑은 반복 밖에서 한 번만 한다."""
    durations = []
    scored: list[tuple[float, int, str]] = []
    scan_node = ""
    for i in range(REPEATS):
        _set_mode(conn, exact)
        start = time.perf_counter()
        scored = _search_by_vector(conn, provider, qvec, TOP_K_CHUNKS, "posting_raw")
        durations.append(time.perf_counter() - start)
        if i == 0:
            scan_node = _explain_scan_node(conn, provider, qvec, TOP_K_CHUNKS, "posting_raw")
    ranked = ranked_postings_by_score(conn, [(score, chunk_id) for score, chunk_id, _text in scored])
    return ranked, durations, scan_node


def run() -> None:
    conn = get_connection()
    google = GoogleEmbeddingProvider()
    local = LocalEmbeddingProvider()

    print("=== HNSW 인덱스 자체 동작 확인(필터 없는 최소 조건 질의) ===")
    for name, provider in [("google", google), ("local", local)]:
        node = _verify_hnsw_index_itself_works(conn, provider)
        print(f"{name}: {node}")
    print()

    rows = []
    scan_nodes: dict[str, set[str]] = {"google_exact": set(), "google_hnsw": set(), "local_exact": set(), "local_hnsw": set()}
    try:
        for qid, question, skill, _keyword in QUESTIONS:
            truth = _ground_truth(conn, skill)
            row = {"qid": qid, "skill": skill}
            for name, provider in [("google", google), ("local", local)]:
                qvec = provider.embed_query(question)  # 질문당 한 번만 계산(타이머 밖)
                for mode, exact in [("exact", True), ("hnsw", False)]:
                    ranked, durations, scan_node = _timed_search(conn, provider, qvec, exact)
                    scan_nodes[f"{name}_{mode}"].add(scan_node)
                    row[f"{name}_{mode}_p5"] = precision_at_k(ranked, truth, PRECISION_K)
                    row[f"{name}_{mode}_r10"] = recall_at_k(ranked, truth, RECALL_K)
                    row[f"{name}_{mode}_p50"] = _percentile(durations, 0.5) * 1000
                    row[f"{name}_{mode}_p95"] = _percentile(durations, 0.95) * 1000
            rows.append(row)
    finally:
        local.close()

    print("=== 실제 gap-check 질의 형태(provider/model/source_type 필터 포함)에서 쓰인 스캔 방식 ===")
    for key, nodes in scan_nodes.items():
        print(f"{key}: {', '.join(sorted(nodes))}")
    print("(Index Scan using idx_chunk_embedding_hnsw_* 가 안 보이면 이 질의는 HNSW를 안 쓴 것 — 아래 recall이 두 모드가 같은 이유)\n")

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
                f"{r[f'{name}_exact_p50']:.2f}/{r[f'{name}_exact_p95']:.2f}{'':>8} | "
                f"{r[f'{name}_hnsw_p50']:.2f}/{r[f'{name}_hnsw_p95']:.2f}"
            )
        print(
            f"{'평균':20} | "
            f"{avg(f'{name}_exact_p5'):.2f}/{avg(f'{name}_exact_r10'):.2f}{'':>8} | "
            f"{avg(f'{name}_hnsw_p5'):.2f}/{avg(f'{name}_hnsw_r10'):.2f}{'':>8} | "
            f"{avg(f'{name}_exact_p50'):.2f}/{avg(f'{name}_exact_p95'):.2f}{'':>8} | "
            f"{avg(f'{name}_hnsw_p50'):.2f}/{avg(f'{name}_hnsw_p95'):.2f}"
        )


if __name__ == "__main__":
    run()
