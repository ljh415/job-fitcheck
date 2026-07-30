"""Plan B 4단계 — RRF(Reciprocal Rank Fusion)로 관계형 정확 매칭(posting_skill)과
pgvector 검색 결과를 결합한다. `02_structured_career_intelligence_rag.md`의 검색 구조
("정확 기술 질문 → skill/alias 관계형 검색, 의미 질문 → pgvector 검색 → RRF 결합")를 그대로
구현한 것.
"""
import psycopg

from rag.embed.base import EmbeddingProvider
from rag.postgres.retrieval import ranked_postings_by_score, search_chunks

RRF_K = 60


def _exact_skill_postings(conn: psycopg.Connection, skill: str) -> list[int]:
    """posting_skill에서 정확히 매칭된 posting_id 리스트 — 순서가 없는 집합이라 전부 동일한
    exact-match 순위(rank=1)로 취급한다."""
    rows = conn.execute("SELECT posting_id FROM posting_skill WHERE skill = %s", (skill,)).fetchall()
    return [r[0] for r in rows]


def rrf_search(
    conn: psycopg.Connection,
    provider: EmbeddingProvider,
    query: str,
    skill: str | None,
    source_type: str = "posting_raw",
    top_k_chunks: int = 60,
) -> list[int]:
    """벡터 검색 순위와 관계형 정확 매칭(skill이 주어졌을 때)을 RRF로 합쳐 posting_id 랭킹을
    반환한다. skill이 None이면 정확 매칭 채널이 없는 질문이라 벡터 검색 결과만 반환한다."""
    scored = search_chunks(conn, provider, query, source_type=source_type, top_k=top_k_chunks)
    vector_ranked = ranked_postings_by_score(conn, [(score, chunk_id) for score, chunk_id, _text in scored])

    if not skill:
        return vector_ranked

    exact_postings = _exact_skill_postings(conn, skill)

    rrf_scores: dict[int, float] = {}
    for rank, posting_id in enumerate(vector_ranked, start=1):
        rrf_scores[posting_id] = rrf_scores.get(posting_id, 0.0) + 1.0 / (RRF_K + rank)
    for posting_id in exact_postings:
        rrf_scores[posting_id] = rrf_scores.get(posting_id, 0.0) + 1.0 / (RRF_K + 1)

    return sorted(rrf_scores, key=lambda pid: rrf_scores[pid], reverse=True)
