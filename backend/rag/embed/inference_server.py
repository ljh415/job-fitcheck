"""3050Ti GPU에서 실행하는 로컬 임베딩 추론 서버.

dev 서버(GPU 없음)의 `rag/embed/local.py`(`LocalEmbeddingProvider`)가 SSH 로컬 포트
포워딩 터널로 이 서버를 호출한다. 127.0.0.1에만 바인딩해서 외부에 직접 노출하지
않는다 — SSH 인증을 거친 터널을 통해서만 접근 가능(Codex 리뷰 권고 반영).

이 파일은 dev 서버 repo에서 관리되고 rsync로 3050Ti에 복사해서 실행하는 독립
스크립트다(dev 서버 쪽 backend/rag/* 코드에서 직접 import하지 않음).

기본 모델은 `intfloat/multilingual-e5-base`(768차원). 처음 시도한
`Alibaba-NLP/gte-multilingual-base`는 실제 공고 청크(한글+영어+특수문자 혼합)에서
토크나이저가 vocab 범위를 벗어난 토큰 ID를 만들어 CUDA `device-side assert`로
죽는 버그가 있어 교체함 — 상세 원인·진단 과정은
`docs/rag-project-plans/01c_local_embedding_experiment_log.md` 참고.

모델 계열별 인코딩 방식이 전부 달라서(공식 모델 카드 기준), 모델명으로 계열을 판별해 분기한다
(`_family_for()`) — 새 모델 계열을 추가하면 이 분기도 같이 확인해야 한다:
  - E5 계열(`intfloat/...`): 문서 "passage: ", 질의 "query: " 텍스트 prefix 필수
  - BGE-M3(`BAAI/bge-m3`): prefix 불필요(공식 문서: "no longer requires adding
    instructions to the queries")
  - Jina 계열(`jinaai/...`): 텍스트 prefix가 아니라 `encode(..., task="retrieval",
    prompt_name="document"/"query")` 파라미터로 구분(모델 자체에 등록된 named prompt)
"""
import argparse

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

app = FastAPI()
_model: SentenceTransformer | None = None
_model_name = ""
_family = "none"  # "e5" | "bge" | "jina" | "none"
_dimensions = 0  # get_embedding_dimension()이 모델별로 안 맞을 때가 있어(예: Jina v5는 None
                 # 반환) 실제 인코딩한 벡터 길이로 시작할 때 한 번 직접 측정해서 고정한다.
_batch_size = 32  # sentence-transformers 기본값. Qwen3 기반(Jina v5)처럼 무거운 decoder
                  # 아키텍처는 4GB VRAM에서 OOM 나므로 --batch-size로 낮춰서 실행한다.


def _family_for(model_name: str) -> str:
    name = model_name.lower()
    if "e5" in name:
        return "e5"
    if "jina" in name:
        return "jina"
    return "none"  # BGE-M3 등 prefix 불필요 계열


class DocumentsRequest(BaseModel):
    texts: list[str]


class QueryRequest(BaseModel):
    text: str


def _encode_documents(texts: list[str]) -> list[list[float]]:
    if _family == "e5":
        texts = [f"passage: {t}" for t in texts]
        return _model.encode(
            texts, convert_to_numpy=True, normalize_embeddings=True, batch_size=_batch_size
        ).tolist()
    if _family == "jina":
        return _model.encode(
            texts, task="retrieval", prompt_name="document",
            convert_to_numpy=True, normalize_embeddings=True, batch_size=_batch_size,
        ).tolist()
    return _model.encode(
        texts, convert_to_numpy=True, normalize_embeddings=True, batch_size=_batch_size
    ).tolist()


def _encode_query(text: str) -> list[float]:
    if _family == "e5":
        return _model.encode([f"query: {text}"], convert_to_numpy=True, normalize_embeddings=True)[0].tolist()
    if _family == "jina":
        return _model.encode(
            [text], task="retrieval", prompt_name="query",
            convert_to_numpy=True, normalize_embeddings=True,
        )[0].tolist()
    return _model.encode([text], convert_to_numpy=True, normalize_embeddings=True)[0].tolist()


@app.get("/health")
def health():
    return {"model": _model_name, "dimensions": _dimensions, "device": str(_model.device)}


@app.post("/embed_documents")
def embed_documents(req: DocumentsRequest):
    vectors = _encode_documents(req.texts)
    return {"vectors": vectors, "model": _model_name, "dimensions": _dimensions}


@app.post("/embed_query")
def embed_query(req: QueryRequest):
    vector = _encode_query(req.text)
    return {"vector": vector, "model": _model_name, "dimensions": _dimensions}


def main() -> None:
    global _model, _model_name, _family, _dimensions, _batch_size
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="intfloat/multilingual-e5-base")
    parser.add_argument("--port", type=int, default=8500)
    parser.add_argument("--batch-size", type=int, default=32, help="4GB VRAM에서 무거운 모델은 낮춰야 함(예: 4~8)")
    args = parser.parse_args()

    _model_name = args.model
    _family = _family_for(args.model)
    _batch_size = args.batch_size
    _model = SentenceTransformer(args.model, device="cuda", trust_remote_code=True)
    _dimensions = len(_encode_query("dimension probe"))
    print(f"모델 로드 완료: {args.model} (dim={_dimensions}, device={_model.device})")

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
