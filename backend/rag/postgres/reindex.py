"""Plan B 2단계 — PostgreSQL 전체 재색인 단일 진입점.

스키마 생성 → 공고/기술사전/정답지 적재 → 청킹 → 임베딩까지 한 번에 실행한다.
원본(`data/companies/*.raw.txt`, `data/candidate_profile.md`)이 사실 원본이고, 이 명령은
언제든 처음부터 다시 실행 가능하다(SQLite `data/rag.db`는 건드리지 않음).

실행: backend/ 에서 `python3 -m rag.postgres.reindex --provider google [--include-profile]`
"""
import argparse

from rag.embed.base import EmbeddingProvider
from rag.embed.google import GoogleEmbeddingProvider
from rag.embed.local import LocalEmbeddingProvider
from rag.postgres.chunks import populate_candidate_profile_chunks, populate_posting_chunks, prune_deleted_postings
from rag.postgres.db import get_connection
from rag.postgres.ingest import run as ingest_run
from rag.postgres.pipeline import run_embedding_pipeline
from rag.postgres.schema import rebuild_schema

PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    "google": GoogleEmbeddingProvider,
    "local": LocalEmbeddingProvider,
}


def run(provider_name: str, include_profile: bool, rebuild_schema_flag: bool = False) -> None:
    if provider_name not in PROVIDERS:
        raise ValueError(f"알 수 없는 provider: {provider_name} (선택 가능: {list(PROVIDERS)})")

    if rebuild_schema_flag:
        # `CREATE TABLE IF NOT EXISTS`는 기존 테이블에 새 컬럼을 안 추가해준다 — 스키마가
        # 바뀌었을 때(Stage 2→4→6처럼)는 드롭 후 재생성이 필요하다(Codex 리뷰로 발견, 2026-07-23).
        schema_conn = get_connection()
        try:
            rebuild_schema(schema_conn)
        finally:
            schema_conn.close()  # rebuild_schema() 실패해도 닫아야 함(Codex 4차 재리뷰로 발견, 2026-07-24)
        print("스키마 재생성 완료(기존 데이터 전부 삭제됨)")

    conn = ingest_run()  # 스키마 생성 + posting/posting_skill/skill_alias 적재
    try:
        _run_with_conn(conn, provider_name, include_profile)
    finally:
        conn.close()  # prune_deleted_postings()/populate_posting_chunks() 실패 경로도 여기서 닫힌다
        # (원래는 try 밖에 있어서 이 둘이 실패하면 conn이 안 닫혔다 — Codex 4차 재리뷰로 발견, 2026-07-24)


def _run_with_conn(conn, provider_name: str, include_profile: bool) -> None:
    # 예전엔(2026-07-23~) google/local을 동시에 유지하는 설계라 "지금 안 고른 provider도
    # 챙겨야 한다"는 전제로 경고·자동 재임베딩 로직이 있었다. 활성 provider를 하나로 통일한
    # 뒤(2026-07-31, 코드리뷰 4번)에도 이 로직이 안 지워져서, 사용자가 의도적으로 하나만
    # 쓰기로 정했는데도 "다른 provider가 안 됐다"는 경고가 계속 뜨고, 프로필이 바뀌면 꺼둔
    # provider로 API 호출까지 조용히 나가고 있었다(사용자 질문으로 발견, 2026-08-02) —
    # 지금은 활성 provider 하나만 다루는 게 의도된 동작이라 이 로직 자체를 없앴다.
    n_pruned = prune_deleted_postings(conn)  # 원문이 삭제된 posting의 고아 청크/임베딩 정리
    if n_pruned:
        print(f"삭제된 공고 {n_pruned}건의 청크/임베딩 정리 완료")
    n_touched, n_chunks = populate_posting_chunks(conn)
    provider = None
    try:
        # provider 생성도 try 안에서 해야 LocalEmbeddingProvider의 SSH 터널 실패 시에도
        # finally의 conn.close()가 실행된다(Codex 3차 재리뷰로 발견, 2026-07-24 — CLI라
        # 프로세스 종료로 대부분 회수되긴 하지만, routers/rag.py에서 이미 쓰던 패턴과
        # 일관되게 맞춘다).
        provider = PROVIDERS[provider_name]()
        n_embedded = run_embedding_pipeline(conn, provider)
        print(
            f"공고 청크 총 {n_chunks}개 (내용 변경된 공고 {n_touched}건만 재생성),"
            f" 임베딩 완료: {n_embedded}개 (model={provider.model}, dim={provider.dimensions})"
        )
        if include_profile:
            _, n_profile_chunks = populate_candidate_profile_chunks(conn)
            n_profile_embedded = run_embedding_pipeline(conn, provider, source_type="candidate_profile")
            print(f"프로필 청크 {n_profile_chunks}개, 임베딩 완료: {n_profile_embedded}개")
    finally:
        # conn은 여기서 안 닫는다 — 호출부인 run()의 finally가 담당(prune_deleted_postings()/
        # populate_posting_chunks() 실패 경로까지 한곳에서 책임지기 위해, 중복 close 방지).
        close = getattr(provider, "close", None)
        if close:
            try:
                close()  # LocalEmbeddingProvider의 SSH 터널 종료 — 다른 provider는 close() 없음
            except Exception:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="google", choices=list(PROVIDERS))
    parser.add_argument(
        "--include-profile", action="store_true",
        help="후보자 프로필도 같이 임베딩(기본 꺼짐 — Google 무료 티어에는 쓰지 말 것)",
    )
    parser.add_argument(
        "--rebuild-schema", action="store_true",
        help="RAG 테이블을 전부 지우고 스키마를 새로 만든 뒤 재색인(schema.py 변경 후 사용, 기존 데이터 전부 삭제됨)",
    )
    args = parser.parse_args()
    run(args.provider, args.include_profile, args.rebuild_schema)
