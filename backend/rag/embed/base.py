"""Plan A 임베딩 provider 공통 인터페이스.

`backend/llm/base.py`(`LLMProvider`)와 같은 패턴이지만, RAG 임베딩은 "런타임에 하나만
활성화"가 아니라 **여러 provider를 나란히 비교**하는 게 목적(Plan A 최종 목표)이라
`llm/router.py` 같은 "활성 provider 선택" 로직은 두지 않는다 — 비교하고 싶은 provider를
그때그때 명시적으로 만들어서 파이프라인에 넘긴다.
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
