"""Google `gemini-embedding-2` 임베딩 provider 구현.

배치 크기 20은 429(RESOURCE_EXHAUSTED)가 실측됐다 — 10개+배치 간 딜레이,
429 발생 시 20초 대기 후 최대 3회 재시도로 안전하게 처리한다.
"""
import time

from google import genai
from google.genai import errors, types

from config import settings
from rag.embed.base import EmbeddingProvider


class GoogleEmbeddingProvider(EmbeddingProvider):
    provider_name = "google"
    model = "gemini-embedding-2"
    dimensions = 1536

    BATCH_SIZE = 10
    BATCH_DELAY_SECONDS = 2
    MAX_RETRIES = 3
    RETRY_WAIT_SECONDS = 20

    def __init__(self) -> None:
        self._client = genai.Client(api_key=settings.google_api_key)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i : i + self.BATCH_SIZE]
            embeddings = self._embed_with_retry(batch, task_type="RETRIEVAL_DOCUMENT")
            results.extend(e.values for e in embeddings)
            if i + self.BATCH_SIZE < len(texts):
                time.sleep(self.BATCH_DELAY_SECONDS)
        return results

    def embed_query(self, text: str) -> list[float]:
        return self._embed_with_retry([text], task_type="RETRIEVAL_QUERY")[0].values

    def _embed_with_retry(self, texts: list[str], task_type: str):
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                res = self._client.models.embed_content(
                    model=self.model,
                    contents=texts,
                    config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dimensions),
                )
                return res.embeddings
            except errors.ClientError as e:
                if getattr(e, "code", None) == 429 and attempt < self.MAX_RETRIES:
                    print(f"  429 재시도 {attempt}/{self.MAX_RETRIES} — {self.RETRY_WAIT_SECONDS}초 대기")
                    time.sleep(self.RETRY_WAIT_SECONDS)
                    continue
                raise
