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
from rag.postgres.chunks import populate_candidate_profile_chunks, populate_posting_chunks
from rag.postgres.ingest import run as ingest_run
from rag.postgres.pipeline import run_embedding_pipeline

PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    "google": GoogleEmbeddingProvider,
    "local": LocalEmbeddingProvider,
}


def run(provider_name: str, include_profile: bool) -> None:
    if provider_name not in PROVIDERS:
        raise ValueError(f"알 수 없는 provider: {provider_name} (선택 가능: {list(PROVIDERS)})")

    conn = ingest_run()  # 스키마 생성 + posting/posting_skill/skill_alias/candidate_evidence 적재
    n_touched, n_chunks = populate_posting_chunks(conn)
    provider = PROVIDERS[provider_name]()
    try:
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
        close = getattr(provider, "close", None)
        if close:
            close()  # LocalEmbeddingProvider의 SSH 터널 종료 — 다른 provider는 close() 없음
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="google", choices=list(PROVIDERS))
    parser.add_argument(
        "--include-profile", action="store_true",
        help="후보자 프로필도 같이 임베딩(기본 꺼짐 — Google 무료 티어에는 쓰지 말 것)",
    )
    args = parser.parse_args()
    run(args.provider, args.include_profile)
