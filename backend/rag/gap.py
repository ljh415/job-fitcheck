"""Plan A 6단계 — Gap 및 행동 엔진.

`01_lean_evidence_first_rag.md`의 원칙을 그대로 구현한다:
  - 기술 빈도·비율은 SQL/Python이 계산한다(LLM이 숫자를 만들지 않음).
  - 검색기(임베딩)가 후보자 프로필에서 근거 청크를 찾는다.
  - LLM은 검색된 근거를 해석해서 evidence_level을 판정하고, 필요하면 행동 계획을 제시한다.
"""
import itertools
import re
import sqlite3

from llm.router import capture_snapshot, high_from_snapshot
from prompts import TRUST_BOUNDARY_NOTICE
from rag.embed.base import EmbeddingProvider
from rag.retrieval import ensure_fts5, fts5_literal, search_chunks
from rag.skills import TRACKED_SKILLS

PROFILE_TOP_K = 3  # 5는 프로필이 짧으면 사실상 전체가 다 나올 만큼 과다 노출됨(2026-08-18 발견)
DEMAND_CANDIDATE_MAX = 25  # LLM 판정에 넘길 후보 공고 상한(비용·프롬프트 크기 제어)
DEMAND_EMBED_TOP_K = 40    # 임베딩 검색 청크 수(공고 단위로 접기 전)
DEMAND_FTS5_TOP_K = 20     # FTS5 검색 청크 수

GAP_ASSESS_SYSTEM = f"""당신은 채용 시장 데이터를 근거로 후보자의 기술 gap을 판정하는 분석가입니다.

주어진 기술/개념에 대해 후보자 프로필에서 검색된 발췌문만 근거로 아래 4단계 중 하나로 판정하세요:
- 직접 근거: 발췌문에서 확인되는 경험이 대상 기술/개념과 **동일한 기능적 역할**을 수행하며, 실무 맥락(프로젝트명·역할·기간 등)까지 함께 확인됨. 표현이 대상 개념의 정확한 이름이든, 그 개념을 실제로 구현하는 구체적인 방법·산출물이든 상관없다 — 핵심은 "기능이 같은가"이다.
- 부분 근거: 대상 기술/개념과 동일한 기능적 역할을 수행하는 경험은 확인되나 실무 맥락이 제한적임(기간이 매우 짧음, 역할이 불분명함, 단순 나열뿐임 등)
- 인접 경험: 대상 기술/개념 자체와 동일한 기능은 아니지만, 상위 카테고리는 같고 전이 가능성이 있는 경험이 있음(예: 다른 벤더의 동일 유형 서비스, 같은 영역이지만 다른 단계/목적을 담당하는 도구)
- 근거 없음: 검색된 발췌문에 관련 근거가 전혀 없음

반드시 지켜야 할 규칙:
- 검색된 발췌문에 없는 내용을 추측하거나 만들어내지 마세요. 근거가 없으면 "근거 없음"으로 판정하세요.
- **핵심 판별 기준은 "정확히 그 단어가 등장하는가"가 아니라 "발췌문의 경험이 대상 개념과 실제로 같은 기능을 수행하는가"입니다.** 같은 상위 카테고리에 속하더라도 기능적 역할이 다르면(예: 다른 벤더의 서비스, 같은 영역의 다른 단계를 담당하는 도구) "직접 근거"나 "부분 근거"를 주지 말고 "인접 경험"으로 판정하세요. 이 원칙은 모든 기술/개념 쌍에 동일하게 적용하며, 특정 기술 이름을 기준으로 한 예외를 두지 마세요.
- "인접 경험"으로 판정할 때는 어떤 보유 경험이 왜 전이 가능하다고 보는지 근거를 명시하세요.
- reasoning에는 판정 근거가 된 발췌문의 핵심 문구를 인용하세요.
{TRUST_BOUNDARY_NOTICE}"""

GAP_ASSESS_TOOL_NAME = "assess_evidence"
GAP_ASSESS_TOOL_DESCRIPTION = "기술/개념에 대한 후보자 프로필 근거 판정 결과를 제출합니다."
GAP_ASSESS_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "evidence_level": {
            "type": "string",
            "enum": ["직접 근거", "부분 근거", "인접 경험", "근거 없음"],
        },
        "reasoning": {
            "type": "string",
            "description": "판정 근거 — 검색된 발췌문의 핵심 문구를 인용해 설명",
        },
    },
    "required": ["evidence_level", "reasoning"],
}


def market_demand(conn: sqlite3.Connection, skill: str) -> dict:
    """SQL로 계산 — LLM이 이 숫자를 만들지 않는다."""
    (total,) = conn.execute("SELECT count(*) FROM posting").fetchone()
    (matched,) = conn.execute(
        "SELECT count(DISTINCT posting_id) FROM posting_skill WHERE skill = ?", (skill,)
    ).fetchone()
    ratio = matched / total if total else 0.0
    return {"matched": matched, "total": total, "ratio": ratio, "method": "exact"}


JUDGE_CANDIDATES_SYSTEM = f"""당신은 채용공고가 특정 기술/개념과 실제로 관련 있는지 판정하는 분석가입니다.

주어진 후보 공고 목록(회사·직무·발췌문)을 보고, 그 발췌문에 대상 기술/개념과 관련된 내용이
실제로 있는 공고의 번호만 골라내세요.

반드시 지켜야 할 규칙:
- 발췌문에 없는 내용을 추측하지 마세요. 애매하면 포함하지 마세요(과대 집계보다 누락이 낫습니다).
- 후보 목록에 없는 공고를 새로 만들어내지 마세요.
- 공고 텍스트의 "주요업무"에서 실제로 수행하는 일인지를 우선 보세요. "우대사항"이나 자격요건의
  "있으면 좋음" 수준 목록에만 키워드가 등장하고 주요업무에는 없다면, 그 공고의 핵심 업무가 아니므로
  관련 있다고 판단하지 마세요(예: 이커머스 우대사항에 "주문/결제/배송"이 나열됐다고 해서 그 회사가
  결제 시스템을 개발한다는 뜻은 아닙니다).
{TRUST_BOUNDARY_NOTICE}"""

JUDGE_CANDIDATES_TOOL_NAME = "judge_relevant_postings"
JUDGE_CANDIDATES_TOOL_DESCRIPTION = "실제로 관련 있는 후보 공고 번호와 판정 근거를 제출합니다."
JUDGE_CANDIDATES_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "relevant": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "integer", "description": "후보 목록의 번호(1부터)"},
                    "reason": {
                        "type": "string",
                        "description": "발췌문 중 이 공고를 관련 있다고 판단한 근거(짧은 인용 또는 요약, 1문장 이내)",
                    },
                },
                "required": ["number", "reason"],
            },
            "description": "실제로 관련 있는 후보만, 번호와 판정 근거를 함께",
        },
    },
    "required": ["relevant"],
}


def _candidate_postings(conn: sqlite3.Connection, skill: str, embed_provider: EmbeddingProvider) -> list[dict]:
    """임베딩 검색 + FTS5 검색으로 후보 공고를 모은다(중복 제거, 최대 DEMAND_CANDIDATE_MAX개).
    두 채널을 같이 쓰는 이유: 임베딩은 이 corpus(비슷한 직군 공고들)에서 유사도 크기로
    "관련 있음/없음"을 가르지 못한다는 게 실측으로 확인됐고(2026-07-23, Redis/Python 캘리브레이션),
    FTS5는 반대로 정확한 표현이 아니면 아예 못 찾는다 — 후보를 넉넉히 모으는 역할만 시키고,
    실제 판정은 LLM이 발췌문을 직접 읽고 한다."""
    ensure_fts5(conn)

    embed_queue = [
        (chunk_id, text)
        for _score, chunk_id, text in search_chunks(
            conn, embed_provider, skill, source_type="posting_raw", top_k=DEMAND_EMBED_TOP_K
        )
    ]

    # source_type='posting_raw' 필터 없이 LIMIT부터 걸면 프로필 청크가 상위권을 차지해 진짜
    # 공고 후보가 밀려날 수 있었다(Codex 재리뷰로 발견, 2026-07-23).
    fts_rows = conn.execute(
        "SELECT rowid FROM document_chunk_fts WHERE document_chunk_fts MATCH ? AND source_type = 'posting_raw'"
        " ORDER BY bm25(document_chunk_fts) LIMIT ?",
        (fts5_literal(skill), DEMAND_FTS5_TOP_K),
    ).fetchall()
    fts_queue = []
    for (chunk_id,) in fts_rows:
        row = conn.execute("SELECT text FROM document_chunk WHERE id = ?", (chunk_id,)).fetchone()
        if row:  # 청크가 실제로 바뀌면 chunks.py가 FTS를 재생성하므로 정상 경로에선 항상 있어야
                 # 하지만, 타이밍 이슈에 대비해 방어적으로 확인
            fts_queue.append((chunk_id, row[0]))

    candidates: dict[int, dict] = {}

    def _add(chunk_id: int, text: str):
        if len(candidates) >= DEMAND_CANDIDATE_MAX:
            return
        row = conn.execute(
            "SELECT po.id, po.company_name, po.job_title FROM document_chunk dc"
            " JOIN posting po ON po.slug = dc.source_id WHERE dc.id = ?",
            (chunk_id,),
        ).fetchone()
        if not row or row[0] in candidates:
            return
        candidates[row[0]] = {"posting_id": row[0], "company_name": row[1], "job_title": row[2], "excerpt": text}

    # 임베딩 채널이 먼저 상한을 다 채워버리면 FTS5 결과가 하나도 안 섞이던 문제(Codex 리뷰 발견,
    # 2026-07-23) — 두 채널을 번갈아 채워서 한쪽이 상한을 독점하지 않게 한다.
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


async def market_demand_hybrid(conn: sqlite3.Connection, skill: str, embed_provider: EmbeddingProvider) -> dict:
    """`TRACKED_SKILLS`에 있는 13개 기술은 검증된 정확 매칭을 그대로 쓰고(`market_demand()`),
    그 외 자유 키워드는 후보를 모아 LLM이 실제로 관련 있는지 개별 판정한 결과를 센다.
    이 추정치는 (1) 후보 풀 안에서만 판단하므로 전수조사가 아니고, (2) LLM 판정이라 완전히
    결정적이지 않다 — `method` 필드로 두 경로를 구분해서 호출부가 신뢰도를 다르게 표시하게 한다."""
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
    # LLM이 후보 범위 밖 번호나 중복 번호를 반환해도 그대로 세면 matched가 candidate_count보다
    # 커지는 모순이 생길 수 있다(Codex 리뷰로 발견, 2026-07-23) — 유효 범위로 걸러내고 중복 제거.
    valid_numbers = {r["number"] for r in result["relevant"] if 1 <= r["number"] <= len(candidates)}
    matched = len(valid_numbers)
    return {
        "matched": matched,
        "total": total,
        "ratio": matched / total if total else 0.0,
        "method": "estimated",
        "candidate_count": len(candidates),
    }


def _recognized_scope(skill: str) -> str | None:
    """공고 검색용으로 이미 사람이 정의해둔 TRACKED_SKILLS 동의어 범위를 판정 LLM에게도
    참고 정보로 전달한다. 이렇게 하면 "이 개념을 얼마나 넓게/좁게 볼지"를 프롬프트에 사례를
    하드코딩하지 않고도, 이미 있는 데이터로 전달할 수 있다(2026-07-23, IaC/Ansible처럼 개념
    경계가 애매한 사례에서 판정이 매번 흔들리는 문제를 프롬프트 예시 대신 데이터로 해결)."""
    patterns = TRACKED_SKILLS.get(skill)
    if not patterns:
        return None
    readable = [re.sub(r"\\b", "", p) for p in patterns]
    return ", ".join(readable)


def _has_profile_embeddings(conn: sqlite3.Connection, embed_provider: EmbeddingProvider) -> bool:
    (count,) = conn.execute(
        "SELECT count(*) FROM chunk_embedding ce JOIN document_chunk dc ON dc.id = ce.chunk_id"
        " WHERE ce.provider = ? AND ce.model = ? AND ce.dimensions = ? AND dc.source_type = 'candidate_profile'",
        (embed_provider.provider_name, embed_provider.model, embed_provider.dimensions),
    ).fetchone()
    return count > 0


async def assess_gap(conn: sqlite3.Connection, skill: str, embed_provider: EmbeddingProvider) -> dict:
    # 검색 결과 0건이 "실제로 근거 없음"인지 "이 provider로 프로필을 아직 임베딩 안 함"인지
    # 구분 못 하고 둘 다 LLM에게 "발췌문 없음"으로 넘어가 "근거 없음"으로 오판되는 문제가 있었다
    # (Codex 리뷰로 발견, 2026-07-23) — 판정 전에 인덱스 존재 자체를 먼저 확인한다.
    if not _has_profile_embeddings(conn, embed_provider):
        raise RuntimeError(
            f"이 provider({embed_provider.provider_name}/{embed_provider.model})로 후보자 프로필이"
            " 아직 임베딩되지 않았습니다. 이 SQLite 경로는 Plan A 재현용으로 동결돼 있어 재임베딩"
            " 스크립트가 더 이상 없습니다 — 실제 서비스는 `rag.postgres.reindex --include-profile`을 씁니다."
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


async def assess_all_gaps(conn: sqlite3.Connection, embed_provider: EmbeddingProvider) -> list[dict]:
    """`TRACKED_SKILLS` 전체를 순회하며 판정한다. 01b의 GP-01(우선순위 gap)·GP-06(전체 강점 요약)·
    AC-06(여러 gap 순서 정하기)처럼 기술 하나가 아니라 전체를 종합해야 하는 질문에 필요하다 —
    `assess_gap()`은 기술 하나만 보므로 이 함수 없이는 그런 질문에 답할 수 없다."""
    return [await assess_gap(conn, skill, embed_provider) for skill in TRACKED_SKILLS]


