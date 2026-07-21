"""Plan A 3단계 실행 스크립트 — Google(`gemini-embedding-2`) 임베딩 기준선.

실제 로직은 `rag/chunks.py`(청킹, provider 무관)와 `rag/embed/google.py`(Google 구현체)
+ `rag/embed/pipeline.py`(공통 파이프라인)에 있다. 이 파일은 그 조합을 실행하는
얇은 진입점이다 — 로컬/OpenAI도 같은 모양의 `embed_local.py`/`embed_openai.py`로 추가한다.

실행: backend/ 에서 `python3 -m rag.embed_google`
"""
import sqlite3

from rag.chunks import populate_posting_chunks
from rag.embed.google import GoogleEmbeddingProvider
from rag.embed.pipeline import run_embedding_pipeline
from rag.ingest import DB_PATH


def run() -> None:
    conn = sqlite3.connect(DB_PATH)
    n_chunks = populate_posting_chunks(conn)
    provider = GoogleEmbeddingProvider()
    n_embedded = run_embedding_pipeline(conn, provider)
    print(f"청크 생성: {n_chunks}개, 임베딩 완료: {n_embedded}개 (model={provider.model}, dim={provider.dimensions})")


if __name__ == "__main__":
    run()
