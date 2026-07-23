"""Plan B 6단계 — Gap 및 행동 엔진, PostgreSQL판. `rag/gap.py`(SQLite, Plan A)의 포팅.

프롬프트 상수(`GAP_ASSESS_SYSTEM` 등)와 순수 함수(`_recognized_scope` — DB 안 건드림)는 새로
만들지 않고 `rag.gap`에서 그대로 재사용한다. `conn`을 받는 함수만 Postgres 방언으로 새로 쓴다.

실행: backend/ 에서 `python3 -m rag.postgres.gap` — CANDIDATE_EVIDENCE의 10개 기술 전체를
실시간 판정해서 정답지와 비교한다(SQLite `rag/gap.py`와 같은 결과가 나와야 포팅이 맞다는 뜻).
"""
import asyncio
import itertools

import psycopg

from llm.router import capture_snapshot, high_from_snapshot
from rag.embed.base import EmbeddingProvider
from rag.embed.local import LocalEmbeddingProvider
from rag.gap import (
    GAP_ASSESS_SYSTEM,
    GAP_ASSESS_TOOL_DESCRIPTION,
    GAP_ASSESS_TOOL_NAME,
    GAP_ASSESS_TOOL_SCHEMA,
    JUDGE_CANDIDATES_SYSTEM,
    JUDGE_CANDIDATES_TOOL_DESCRIPTION,
    JUDGE_CANDIDATES_TOOL_NAME,
    JUDGE_CANDIDATES_TOOL_SCHEMA,
    PROFILE_TOP_K,
    _recognized_scope,
)
from rag.postgres.db import get_connection
from rag.postgres.fts import search_fts
from rag.postgres.pipeline import _VECTOR_COLUMN
from rag.postgres.retrieval import search_chunks
from rag.skills import CANDIDATE_EVIDENCE, TRACKED_SKILLS

DEMAND_CANDIDATE_MAX = 25  # LLM 판정에 넘길 후보 공고 상한(비용·프롬프트 크기 제어)
DEMAND_EMBED_TOP_K = 40    # 임베딩 검색 청크 수(공고 단위로 접기 전)
DEMAND_FTS_TOP_K = 20      # 전문검색 청크 수


def market_demand(conn: psycopg.Connection, skill: str) -> dict:
    """SQL로 계산 — LLM이 이 숫자를 만들지 않는다."""
    (total,) = conn.execute("SELECT count(*) FROM posting").fetchone()
    (matched,) = conn.execute(
        "SELECT count(DISTINCT posting_id) FROM posting_skill WHERE skill = %s", (skill,)
    ).fetchone()
    ratio = matched / total if total else 0.0
    return {"matched": matched, "total": total, "ratio": ratio, "method": "exact"}


def _candidate_postings(conn: psycopg.Connection, skill: str, embed_provider: EmbeddingProvider) -> list[dict]:
    """임베딩 검색 + 전문검색으로 후보 공고를 모은다(중복 제거, 최대 DEMAND_CANDIDATE_MAX개).
    `rag/gap.py`의 동일 함수와 로직이 같다 — 임베딩은 이 corpus에서 유사도 크기로 관련성을
    못 가르고, 전문검색은 정확한 표현이 아니면 아예 못 찾으니 후보를 넉넉히 모으는 역할만 하고
    실제 판정은 LLM이 발췌문을 직접 읽고 한다."""
    embed_queue = [
        (chunk_id, text)
        for _score, chunk_id, text in search_chunks(
            conn, embed_provider, skill, source_type="posting_raw", top_k=DEMAND_EMBED_TOP_K
        )
    ]

    fts_chunk_ids = search_fts(conn, skill, top_k=DEMAND_FTS_TOP_K, source_type="posting_raw")
    fts_queue = []
    for chunk_id in fts_chunk_ids:
        row = conn.execute("SELECT text FROM document_chunk WHERE id = %s", (chunk_id,)).fetchone()
        if row:
            fts_queue.append((chunk_id, row[0]))

    candidates: dict[int, dict] = {}

    def _add(chunk_id: int, text: str):
        if len(candidates) >= DEMAND_CANDIDATE_MAX:
            return
        row = conn.execute(
            "SELECT po.id, po.company_name, po.job_title FROM document_chunk dc"
            " JOIN posting po ON po.slug = dc.source_id WHERE dc.id = %s",
            (chunk_id,),
        ).fetchone()
        if not row or row[0] in candidates:
            return
        candidates[row[0]] = {"posting_id": row[0], "company_name": row[1], "job_title": row[2], "excerpt": text}

    # 임베딩 채널이 먼저 상한을 다 채우면 전문검색 결과가 하나도 안 섞이는 걸 막기 위해 인터리브
    for embed_item, fts_item in itertools.zip_longest(embed_queue, fts_queue):
        if len(candidates) >= DEMAND_CANDIDATE_MAX:
            break
        if embed_item is not None:
            _add(*embed_item)
        if len(candidates) >= DEMAND_CANDIDATE_MAX:
            break
        if fts_item is not None:
            _add(*fts_item)

    return list(candidates.values())


async def market_demand_hybrid(conn: psycopg.Connection, skill: str, embed_provider: EmbeddingProvider) -> dict:
    """`TRACKED_SKILLS`에 있는 13개 기술은 정확 매칭을 그대로 쓰고, 그 외 자유 키워드는 후보를
    모아 LLM이 실제로 관련 있는지 개별 판정한 결과를 센다."""
    if skill in TRACKED_SKILLS:
        return market_demand(conn, skill)

    (total,) = conn.execute("SELECT count(*) FROM posting").fetchone()
    candidates = _candidate_postings(conn, skill, embed_provider)
    if not candidates:
        return {"matched": 0, "total": total, "ratio": 0.0, "method": "estimated", "candidate_count": 0}

    listing = "\n".join(
        f"{i+1}. {c['company_name']} / {c['job_title']} — 발췌: {c['excerpt'][:200]}"
        for i, c in enumerate(candidates)
    )
    snap = capture_snapshot()
    high, high_model = high_from_snapshot(snap)
    result = await high.extract_structured(
        system=JUDGE_CANDIDATES_SYSTEM,
        user=f"기술/개념: {skill}\n\n후보 공고 목록:\n{listing}",
        tool_name=JUDGE_CANDIDATES_TOOL_NAME,
        tool_description=JUDGE_CANDIDATES_TOOL_DESCRIPTION,
        tool_schema=JUDGE_CANDIDATES_TOOL_SCHEMA,
        model=high_model,
        operation="시장 수요 후보 판정",
        reasoning_effort=snap.reasoning_effort,
    )
    valid_numbers = {n for n in result["relevant_numbers"] if 1 <= n <= len(candidates)}
    matched = len(valid_numbers)
    return {
        "matched": matched,
        "total": total,
        "ratio": matched / total if total else 0.0,
        "method": "estimated",
        "candidate_count": len(candidates),
    }


def _has_profile_embeddings(conn: psycopg.Connection, embed_provider: EmbeddingProvider) -> bool:
    column = _VECTOR_COLUMN[embed_provider.dimensions]
    (count,) = conn.execute(
        f"SELECT count(*) FROM chunk_embedding ce JOIN document_chunk dc ON dc.id = ce.chunk_id"
        f" WHERE ce.provider = %s AND ce.model = %s AND ce.{column} IS NOT NULL"
        " AND dc.source_type = 'candidate_profile'",
        (embed_provider.provider_name, embed_provider.model),
    ).fetchone()
    return count > 0


async def assess_gap(conn: psycopg.Connection, skill: str, embed_provider: EmbeddingProvider) -> dict:
    if not _has_profile_embeddings(conn, embed_provider):
        raise RuntimeError(
            f"이 provider({embed_provider.provider_name}/{embed_provider.model})로 후보자 프로필이"
            " 아직 임베딩되지 않았습니다. `rag.postgres.reindex --include-profile`로 먼저 임베딩하세요."
        )

    demand = await market_demand_hybrid(conn, skill, embed_provider)

    evidence_chunks = search_chunks(
        conn, embed_provider, f"{skill} 관련 실무 경험", source_type="candidate_profile", top_k=PROFILE_TOP_K
    )
    excerpts = [text for _score, _chunk_id, text in evidence_chunks]

    snap = capture_snapshot()
    high, high_model = high_from_snapshot(snap)

    excerpt_block = "\n\n".join(f"[발췌 {i+1}] {t}" for i, t in enumerate(excerpts)) or "(검색된 발췌문 없음)"
    scope = _recognized_scope(skill)
    scope_line = (
        f"이 개념으로 인정하는 구체적 표현(공고 검색 기준, 참고용): {scope}."
        " 이 목록에 없는 표현이라도 명백히 동일한 기능이면 인정할 수 있지만, 이 목록이 이 개념의"
        " 범위를 얼마나 넓게/좁게 볼지의 기준선이다.\n"
        if scope else ""
    )
    user = (
        f"기술/개념: {skill}\n"
        f"{scope_line}"
        f"시장 수요(참고용, 판정에 직접 쓰지 않음): 전체 공고 {demand['total']}건 중 {demand['matched']}건 요구"
        f"({demand['ratio']*100:.1f}%)\n\n"
        f"후보자 프로필에서 검색된 발췌문(유사도 상위 {len(excerpts)}건):\n{excerpt_block}"
    )

    result = await high.extract_structured(
        system=GAP_ASSESS_SYSTEM,
        user=user,
        tool_name=GAP_ASSESS_TOOL_NAME,
        tool_description=GAP_ASSESS_TOOL_DESCRIPTION,
        tool_schema=GAP_ASSESS_TOOL_SCHEMA,
        model=high_model,
        operation="Gap 판정",
        reasoning_effort=snap.reasoning_effort,
    )
    result["skill"] = skill
    result["market_demand"] = demand
    result["excerpts"] = excerpts
    return result


async def assess_all_gaps(conn: psycopg.Connection, embed_provider: EmbeddingProvider) -> list[dict]:
    return [await assess_gap(conn, skill, embed_provider) for skill in TRACKED_SKILLS]


async def validate() -> None:
    """CANDIDATE_EVIDENCE(정적 정답지)의 10개 기술을 실시간 파이프라인으로 재판정해 비교한다."""
    conn = get_connection()
    provider = LocalEmbeddingProvider()
    try:
        agree = 0
        for skill, truth in CANDIDATE_EVIDENCE.items():
            result = await assess_gap(conn, skill, provider)
            truth_level = truth["level"]
            predicted = result["evidence_level"]
            if truth_level == "직접 근거":
                ok = predicted in ("직접 근거", "부분 근거")
            else:
                ok = predicted in ("근거 없음", "인접 경험")
            agree += ok
            mark = "OK" if ok else "MISMATCH"
            print(f"[{mark}] {skill:14} 정답={truth_level:8} 예측={predicted:8} | {result['reasoning'][:80]}")
        print(f"\n일치: {agree}/{len(CANDIDATE_EVIDENCE)}")
    finally:
        provider.close()


if __name__ == "__main__":
    asyncio.run(validate())
