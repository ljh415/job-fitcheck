"""Plan A 임베딩 실행 진입점 — provider 이름 하나를 받아 그 provider로 공고를 임베딩한다.

실제 로직은 `rag/chunks.py`(청킹, provider 무관)와 `rag/embed/<provider>.py`(구현체)
+ `rag/embed/pipeline.py`(공통 파이프라인)에 있다. 새 provider를 추가할 때는
`rag/embed/`에 구현체를 하나 만들고 아래 PROVIDERS에 한 줄만 추가하면 된다.

실행: backend/ 에서 `python3 -m rag.run_embedding --provider google`
"""
import argparse
import sqlite3

from rag.chunks import populate_candidate_profile_chunks, populate_posting_chunks
from rag.embed.base import EmbeddingProvider
from rag.embed.google import GoogleEmbeddingProvider
from rag.embed.local import LocalEmbeddingProvider
from rag.embed.pipeline import run_embedding_pipeline
from rag.ingest import DB_PATH

PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    "google": GoogleEmbeddingProvider,
    "local": LocalEmbeddingProvider,
    # "openai": OpenAIEmbeddingProvider, # 선택 실험
}


def run(provider_name: str, include_profile: bool) -> None:
    if provider_name not in PROVIDERS:
        raise ValueError(f"알 수 없는 provider: {provider_name} (선택 가능: {list(PROVIDERS)})")
    conn = sqlite3.connect(DB_PATH)
    n_touched, n_chunks = populate_posting_chunks(conn)
    provider = PROVIDERS[provider_name]()
    try:
        n_embedded = run_embedding_pipeline(conn, provider)
        print(
            f"공고 청크 총 {n_chunks}개 (내용 변경된 공고 {n_touched}건만 재생성),"
            f" 임베딩 완료: {n_embedded}개 (model={provider.model}, dim={provider.dimensions})"
        )
        if include_profile:
            # --include-profile은 기본값 False — 개인 프로필을 어떤 provider로든 보내는 건
            # 실행마다 명시적으로 선택해야 한다(무료 티어 Google에는 절대 포함하지 말 것,
            # 01a_embedding_model_candidates.md "개인 데이터 사용 제한" 참고).
            _, n_profile_chunks = populate_candidate_profile_chunks(conn)
            n_profile_embedded = run_embedding_pipeline(conn, provider, source_type="candidate_profile")
            print(f"프로필 청크 {n_profile_chunks}개, 임베딩 완료: {n_profile_embedded}개")
    finally:
        close = getattr(provider, "close", None)
        if close:
            close()  # LocalEmbeddingProvider의 SSH 터널 종료 — 다른 provider는 close() 없음


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="google", choices=list(PROVIDERS))
    parser.add_argument(
        "--include-profile", action="store_true",
        help="후보자 프로필도 같이 임베딩(기본 꺼짐 — 매 실행마다 명시적으로 선택 필요, Google 무료 티어에는 쓰지 말 것)",
    )
    args = parser.parse_args()
    run(args.provider, args.include_profile)
