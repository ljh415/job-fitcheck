"""Plan B 4단계 — "벡터만" vs "RRF 하이브리드(관계형 정확 매칭+벡터)" 비교.

**중요한 방법론적 한계**: `rag.evaluate.QUESTIONS` 12개는 전부 `TRACKED_SKILLS`(13개 고정 기술)
기준이고, ground truth(`_ground_truth()`)도 정확 매칭 채널(`posting_skill`)과 완전히 같은
테이블에서 나온다. 즉 이 평가에서 RRF의 "정확 매칭" 채널은 정답 그 자체와 동일해서, 하이브리드가
벡터 단독보다 recall이 높게 나오는 건 당연한 결과이지 "하이브리드가 실전에서 도움이 된다"는 증거가
아니다 — 이 스크립트는 **RRF 결합 로직 자체가 올바르게 동작하는지(정확 채널이 있으면 그 결과가
누락되지 않고 우선 반영되는지) 확인하는 배관 점검**이지, 진짜 자유 키워드 질문에 대한 하이브리드
효과 검증이 아니다. 후자를 하려면 `skill_alias`를 임베딩 클러스터링으로 채우는 재설계(Stage 4
범위에서 제외, 별도 논의)가 먼저 필요하다.

실행: backend/ 에서 `python3 -m rag.postgres.evaluate_hybrid`
"""
from rag.embed.google import GoogleEmbeddingProvider
from rag.embed.local import LocalEmbeddingProvider
from rag.evaluate import PRECISION_K, QUESTIONS, RECALL_K, TOP_K_CHUNKS, precision_at_k, recall_at_k
from rag.postgres.db import get_connection
from rag.postgres.evaluate import _ground_truth, search_embedding
from rag.postgres.hybrid import rrf_search


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
                vector_ranked = search_embedding(conn, provider, question)
                hybrid_ranked = rrf_search(conn, provider, question, skill, top_k_chunks=TOP_K_CHUNKS)
                row[f"{name}_vec_p5"] = precision_at_k(vector_ranked, truth, PRECISION_K)
                row[f"{name}_vec_r10"] = recall_at_k(vector_ranked, truth, RECALL_K)
                row[f"{name}_hybrid_p5"] = precision_at_k(hybrid_ranked, truth, PRECISION_K)
                row[f"{name}_hybrid_r10"] = recall_at_k(hybrid_ranked, truth, RECALL_K)
            rows.append(row)
    finally:
        local.close()

    def avg(key: str) -> float:
        return sum(r[key] for r in rows) / len(rows)

    print("주의: 정확 매칭 채널이 ground truth와 동일한 테이블이라 하이브리드가 항상 유리하게")
    print("나옴 — RRF 결합 로직 배관 점검용이지 실전 효과 검증이 아님(파일 상단 docstring 참고).\n")
    for name in ("google", "local"):
        print(f"-- {name}: 벡터만 vs RRF 하이브리드 --")
        for r in rows:
            print(
                f"{r['qid']:6}{r['skill']:14} | "
                f"vec {r[f'{name}_vec_p5']:.2f}/{r[f'{name}_vec_r10']:.2f} | "
                f"hybrid {r[f'{name}_hybrid_p5']:.2f}/{r[f'{name}_hybrid_r10']:.2f}"
            )
        print(
            f"{'평균':20} | "
            f"vec {avg(f'{name}_vec_p5'):.2f}/{avg(f'{name}_vec_r10'):.2f} | "
            f"hybrid {avg(f'{name}_hybrid_p5'):.2f}/{avg(f'{name}_hybrid_r10'):.2f}"
        )


if __name__ == "__main__":
    run()
