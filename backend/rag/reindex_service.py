"""RAG 재색인 실행 상태·잠금·트리거 — FastAPI에 의존하지 않는다.
HTTP 상태 코드 변환(409/503)은 호출부(routers/rag.py)의 책임이다."""
import asyncio
import threading

from config import resolve_rag_embedding_provider, settings
from rag.postgres import reindex as rag_reindex

_reindex_in_progress = False  # main.py가 uvicorn 단일 프로세스(workers 미지정)라 in-process
# 플래그로 충분하다 — chunk_embedding에 UNIQUE(chunk_id, provider, model, dimensions) 제약이
# 있어서 재색인 두 개가 겹치면 두 번째가 UniqueViolation으로 500이 난다. 여러 worker/
# 인스턴스로 확장하면 advisory lock으로 바꿔야 한다.
_reindex_pending = False  # 재색인 도중 새 CRUD 이벤트가 들어오면 이 플래그만 세우고, 현재
# 실행이 끝난 뒤 한 번 더 돈다 — 그냥 무시하면 그 이벤트가 스캔에 안 잡힌 채 다음 트리거가
# 올 때까지 색인이 안 될 수 있다. 지금은 항상 호출부가 한 번 결정한 provider 하나로만
# 재실행된다(아래 참고) — provider가 여러 개일 수 있으면 재실행 때 범위를 잃을 수 있는데,
# provider를 하나로 통일하면서 이 문제 자체가 해소됨.
_reindex_lock = threading.Lock()  # _reindex_in_progress/_reindex_pending은 이벤트 루프
# 스레드(API 요청)와 워커 스레드(asyncio.to_thread로 도는 _run_sync) 양쪽에서 건드린다 —
# "확인 후 결정"이 두 단계짜리라 잠금 없인 원자적이지 않다. 워커가 pending을 False로
# 확인하고 루프를 빠져나가는 그 순간과, CRUD 훅이 in_progress를 확인해 pending을 True로
# 세우는 순간이 겹치면 방금 세운 pending을 워커가 바로 덮어써서 CRUD의 "재색인 필요" 신호가
# 조용히 사라질 수 있다 — 두 플래그를 건드리는 모든 지점을 이 잠금으로 감싼다.


def try_begin_manual() -> bool:
    """수동 트리거(웹 /reindex, provider 전환 시 선행 재색인) 전용.
    이미 진행 중이면 아무 것도 바꾸지 않고 False — 호출부가 409로 변환한다.
    pending은 자동 트리거 전용 개념이라 여기선 건드리지 않는다."""
    global _reindex_in_progress
    with _reindex_lock:
        if _reindex_in_progress:
            return False
        _reindex_in_progress = True
        return True


def _run_sync(provider: str) -> None:
    # 실제 작업+플래그 해제를 전부 워커 스레드 안에서 끝낸다 — 호출부 coroutine의 finally에서
    # 플래그를 풀면, 클라이언트 연결 끊김 등으로 그 coroutine이 cancel될 때 asyncio.to_thread()로
    # 넘어간 스레드는 안 멈추는데 플래그만 먼저 풀려서 새 요청이 겹쳐 실행될 수 있다. 플래그
    # 수명을 실제 동기 작업 전체와 묶어야 cancel에도 안전하다.
    #
    # provider는 호출부가 시작 시점에 한 번 결정해서 넘긴다(재실행 때마다 다시 resolve하지
    # 않음) — 재색인 도중 설정이 바뀌는 경우는 이제 이 함수가 신경 쓸 일이 아니다. 설정
    # 변경(update_rag_settings()) 자체가 "새 provider로 먼저 재색인 → 성공해야 override
    # 커밋"이라는 자기 완결적 흐름이라, 이 함수가 도는 도중에 활성 provider가 바뀌는 일 자체가
    # 없다 — 응답 시점에 다시 resolve하면 실제로 처리 안 한 provider를 성공값으로 반환할 수
    # 있는 race가 생긴다.
    global _reindex_in_progress, _reindex_pending
    try:
        while True:
            rag_reindex.run(provider, settings.rag_include_profile)
            with _reindex_lock:
                if _reindex_pending:
                    # pending 상태에서 재실행 — diff 기반이라 이미 반영된 변경은 다시
                    # 스캔해도 비용이 거의 없다.
                    _reindex_pending = False
                    continue
                # "pending 없음" 확인과 in_progress 해제를 같은 임계 구역 안에서 끝낸다 —
                # 둘을 분리하면 그 사이 틈에 CRUD 훅이 pending=True를 세워도 곧장 덮어써
                # 사라질 수 있다.
                _reindex_in_progress = False
                return
    except Exception:
        with _reindex_lock:
            _reindex_in_progress = False
            _reindex_pending = False
        raise


async def run(provider: str) -> None:
    """try_begin_manual()로 in_progress를 이미 세운 뒤 호출한다. 실패 시 원본 예외를 그대로
    던진다 — HTTP 변환은 호출부 책임."""
    await asyncio.to_thread(_run_sync, provider)


def trigger_background() -> bool:
    """회사/프로필 CRUD 훅에서 호출하는 자동 재색인 트리거. 수동 트리거(reindex())와 달리
    사용자에게 보여줄 응답이 없으므로, RAG 꺼짐이면 즉시 False, 진행 중이면 예외 대신
    pending만 세워 조용히 뒤로 미룬다. 실패해도(임베딩 API 오류 등) CRUD 요청 자체는 이미
    끝난 뒤라 사용자에게 영향 없음 — 다음 트리거(수동/자동 무관) 때 diff 기반으로 자연히
    재시도된다.

    provider 해석은 _reindex_in_progress를 세우기 전에 끝낸다 — 여기서 예외가 나면 아예
    상태를 안 건드리니 다음 트리거가 정상적으로 재시도할 수 있다. 상태를 세운 뒤
    asyncio.create_task() 예약 자체가 실패하는 경우(동기 예외)엔 _run_sync()가 아예
    시작되지 않아 자체 복구 코드도 못 도니, 여기서 직접 in_progress/pending을 원복하고
    예외를 그대로 다시 던진다 — 그러지 않으면 재색인이 프로세스 재시작 전까지 영구
    멈춘다(2026-09-04 Codex 리뷰)."""
    global _reindex_in_progress, _reindex_pending
    if not settings.rag_postgres_host:
        return False
    provider = resolve_rag_embedding_provider()
    with _reindex_lock:
        if _reindex_in_progress:
            _reindex_pending = True
            return False
        _reindex_in_progress = True
    try:
        asyncio.create_task(asyncio.to_thread(_run_sync, provider))
    except Exception:
        with _reindex_lock:
            _reindex_in_progress = False
            _reindex_pending = False
        raise
    return True


if __name__ == "__main__":
    from unittest.mock import patch

    def _reset():
        global _reindex_in_progress, _reindex_pending
        _reindex_in_progress = False
        _reindex_pending = False

    # 1. try_begin_manual() 연속 호출 — 두 번째는 잠겨 있어야 함
    _reset()
    assert try_begin_manual() is True
    assert try_begin_manual() is False
    _reset()

    # 2. 진행 중일 때 trigger_background()는 예외 없이 pending만 세움
    with patch.object(settings, "rag_postgres_host", "dummy"):
        _reindex_in_progress = True
        assert trigger_background() is False
        assert _reindex_pending is True
    _reset()

    # 3. run() 도중 예외 → in_progress/pending 둘 다 복구
    with patch.object(rag_reindex, "run", side_effect=RuntimeError("boom")):
        _reindex_in_progress = True
        try:
            asyncio.run(run("google"))
            assert False, "예외가 발생했어야 함"
        except RuntimeError:
            pass
        assert _reindex_in_progress is False
        assert _reindex_pending is False
    _reset()

    # 4. pending 재실행 — _run_sync() 한 번 호출로 fake run이 2회 불려야 함
    calls = []
    def _fake_run(provider, include_profile):
        calls.append(provider)
        if len(calls) == 1:
            with _reindex_lock:
                global _reindex_pending
                _reindex_pending = True
    with patch.object(rag_reindex, "run", side_effect=_fake_run):
        _reindex_in_progress = True
        _run_sync("google")
        assert calls == ["google", "google"], calls
        assert _reindex_in_progress is False
        assert _reindex_pending is False
    _reset()

    # 5. provider 해석 실패 → in_progress를 아예 세우지 않아 다음 트리거가 정상 동작
    with patch.object(settings, "rag_postgres_host", "dummy"), \
         patch("rag.reindex_service.resolve_rag_embedding_provider", side_effect=RuntimeError("provider boom")):
        try:
            trigger_background()
            assert False, "예외가 발생했어야 함"
        except RuntimeError:
            pass
        assert _reindex_in_progress is False, "provider 해석 실패는 in_progress를 건드리면 안 됨"
    _reset()

    # 6. asyncio.create_task() 예약 자체가 실패해도 상태가 원복돼야 함(그러지 않으면
    # 재색인이 프로세스 재시작 전까지 영구 멈춤)
    with patch.object(settings, "rag_postgres_host", "dummy"), \
         patch("rag.reindex_service.resolve_rag_embedding_provider", return_value="google"), \
         patch("asyncio.create_task", side_effect=RuntimeError("no event loop")):
        try:
            trigger_background()
            assert False, "예외가 발생했어야 함"
        except RuntimeError:
            pass
        assert _reindex_in_progress is False, "task 예약 실패 후 in_progress가 원복돼야 함"
        assert _reindex_pending is False, "task 예약 실패 후 pending도 원복돼야 함"
    _reset()

    print("reindex_service self-check 통과")
