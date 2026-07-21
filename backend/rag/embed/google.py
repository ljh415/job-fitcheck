"""Google `gemini-embedding-2` 임베딩 provider 구현.

`gemini-embedding-2`는 `gemini-embedding-001`과 달리 `task_type` config 파라미터를
지원하지 않는다(공식 문서: "You cannot use the task_type field for the
gemini-embedding-2 model. Instead, include the task as an instruction in your
prompt"). API가 이 파라미터를 조용히 무시해서 에러 없이 성공했었지만 실제로는
문서/질의 구분 없이 임베딩되고 있었다 — Codex 리뷰로 발견(2026-07-22).
대신 텍스트 앞에 prefix를 붙이는 방식을 쓴다:
  - 문서(저장용): "title: {title} | text: {content}" (제목 없으면 title: none)
  - 질의(검색용): "task: search result | query: {content}"

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
        # skipped: 청크별 실제 제목(회사명·직무 등) 부여 — 지금은 "title: none"으로 시작,
        # 검색 품질이 부족해지면 posting 메타데이터를 title로 채우는 걸 고려한다.
        prefixed = [f"title: none | text: {t}" for t in texts]
        results: list[list[float]] = []
        for i in range(0, len(prefixed), self.BATCH_SIZE):
            batch = prefixed[i : i + self.BATCH_SIZE]
            embeddings = self._embed_with_retry(batch)
            results.extend(e.values for e in embeddings)
            if i + self.BATCH_SIZE < len(prefixed):
                time.sleep(self.BATCH_DELAY_SECONDS)
        return results

    def embed_query(self, text: str) -> list[float]:
        prefixed = f"task: search result | query: {text}"
        return self._embed_with_retry([prefixed])[0].values

    def _embed_with_retry(self, texts: list[str]):
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                res = self._client.models.embed_content(
                    model=self.model,
                    contents=texts,
                    config=types.EmbedContentConfig(output_dimensionality=self.dimensions),
                )
                return res.embeddings
            except errors.ClientError as e:
                if getattr(e, "code", None) == 429 and attempt < self.MAX_RETRIES:
                    print(f"  429 재시도 {attempt}/{self.MAX_RETRIES} — {self.RETRY_WAIT_SECONDS}초 대기")
                    time.sleep(self.RETRY_WAIT_SECONDS)
                    continue
                raise
