"""임베딩 provider 공통 인터페이스.

`backend/llm/base.py`(`LLMProvider`)와 같은 패턴이다. 활성 provider를 고르는 로직은 이 모듈이
아니라 `config.resolve_rag_embedding_provider()`가 맡고, 파이프라인에는 provider 구현체를
명시적으로 만들어서 넘긴다.
"""
from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    provider_name: str
    model: str
    dimensions: int

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """저장용 문서 텍스트 목록을 벡터 목록으로 변환한다(입력 순서 보존).
        배치 크기·재시도 등 provider별 API 제약은 구현체 내부에서 처리한다."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """검색 질의 텍스트 하나를 벡터로 변환한다.
        Google처럼 문서용/질의용을 다르게 인코딩하는 provider는 여기서 구분 처리한다."""
