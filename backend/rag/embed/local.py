"""3050Ti GPU 추론 서버를 SSH 터널로 호출하는 임베딩 provider.

실제 모델 추론은 3050Ti에서 도는 `inference_server.py`(FastAPI)가 담당하고, 여기서는
SSH 로컬 포트 포워딩 터널을 열고 그 서버를 HTTP로 호출만 한다. 서버가 127.0.0.1에만
바인딩돼 있어 SSH 터널 없이는 접근 자체가 안 된다.

`model`/`dimensions`는 다른 provider와 달리 클래스 상수로 고정하지 않고 매번 서버의
`/health` 응답에서 읽어와 EXPECTED_* 값과 대조한다 — 3050Ti 쪽에서 모델을 바꿔
재시작했는데 dev 서버가 그걸 모른 채 예전 차원으로 계속 저장하는 사고를 막기 위함
(Codex 리뷰 권고).
"""
import subprocess
import time

import httpx

from config import settings
from rag.embed.base import EmbeddingProvider


class LocalEmbeddingProvider(EmbeddingProvider):
    provider_name = "local"

    # 01c 실험 1~4 참고: gte-multilingual-base(토크나이저 버그) → e5-base(채택) →
    # BGE-M3(e5-base보다 낮은 점수) → Jina v5-text-small(e5-base·Google보다 높은 점수로 최종 채택,
    # 2026-07-23). Qwen3 기반 decoder+LoRA라 4GB VRAM에서 배치 크기를 작게(4) 잡아야 함
    # — inference_server.py를 --batch-size 4로 띄워야 한다.
    EXPECTED_MODEL = "jinaai/jina-embeddings-v5-text-small"
    EXPECTED_DIMENSIONS = 1024

    TUNNEL_LOCAL_PORT = 8500
    HEALTH_TIMEOUT_SECONDS = 30

    def __init__(self) -> None:
        self._tunnel = self._open_tunnel()
        try:
            self._wait_for_health()
            info = self._get("/health")
            self.model = info["model"]
            self.dimensions = info["dimensions"]
            if self.model != self.EXPECTED_MODEL or self.dimensions != self.EXPECTED_DIMENSIONS:
                raise RuntimeError(
                    f"3050Ti 추론 서버가 기대한 모델과 다릅니다"
                    f"(기대: {self.EXPECTED_MODEL}/{self.EXPECTED_DIMENSIONS}차원,"
                    f" 실제: {self.model}/{self.dimensions}차원)."
                    " 모델을 바꿨다면 이 클래스의 EXPECTED_* 값도 같이 갱신하세요."
                )
        except httpx.HTTPError as e:
            # _wait_for_health() 통과 후 이 health 재조회가 실패하는 경우(드문 타이밍 이슈) —
            # 여기서 안 잡으면 터널을 닫지 못한 채(self.close() 미호출) 예외가 새어나가 프로세스가
            # 고아로 남고, routers/rag.py도 RuntimeError만 잡아서 503 대신 500이 됐다(Codex
            # 재리뷰로 발견, 2026-07-23).
            self.close()
            raise RuntimeError(f"3050Ti 추론 서버 통신 오류: {e}") from e
        except Exception:
            self.close()
            raise

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._post("/embed_documents", {"texts": texts})["vectors"]

    def embed_query(self, text: str) -> list[float]:
        return self._post("/embed_query", {"text": text})["vector"]

    def close(self) -> None:
        self._tunnel.terminate()
        self._tunnel.wait(timeout=5)

    def _open_tunnel(self) -> subprocess.Popen:
        cmd = [
            "ssh", "-N",
            "-L", f"{self.TUNNEL_LOCAL_PORT}:127.0.0.1:{settings.rag_local_embed_port}",
            "-i", settings.rag_local_ssh_key_path,
            "-p", str(settings.rag_local_ssh_port),
            "-o", "ExitOnForwardFailure=yes",
            # known_hosts가 비어있는 환경(컨테이너 등)에서는 이 옵션 없이는 호스트 키 확인
            # 대화형 프롬프트가 떠서 비대화형 실행 시 터널이 바로 종료됨(실제로 겪은 버그).
            "-o", "StrictHostKeyChecking=accept-new",
            f"{settings.rag_local_ssh_user}@{settings.rag_local_ssh_host}",
        ]
        return subprocess.Popen(cmd)

    def _wait_for_health(self) -> None:
        deadline = time.monotonic() + self.HEALTH_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._tunnel.poll() is not None:
                raise RuntimeError("SSH 터널이 시작 직후 종료됨 — 접속 정보/키/원격 서버 실행 여부 확인 필요")
            try:
                self._get("/health")
                return
            except httpx.HTTPError:
                time.sleep(1)
        self.close()
        raise RuntimeError("3050Ti 추론 서버에 연결 실패(터널은 열렸으나 서버가 응답 없음)")

    def _get(self, path: str) -> dict:
        r = httpx.get(f"http://127.0.0.1:{self.TUNNEL_LOCAL_PORT}{path}", timeout=10)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        # Jina는 e5-base보다 느림(Qwen3 기반+작은 배치 크기 강제) — 대량 문서 임베딩 시
        # 120초로는 부족해서 넉넉히 잡음(실측: 183개 청크 배치 크기 4 기준 수 분 소요).
        r = httpx.post(f"http://127.0.0.1:{self.TUNNEL_LOCAL_PORT}{path}", json=body, timeout=600)
        r.raise_for_status()
        return r.json()
