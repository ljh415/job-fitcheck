"""MCP 회사 등록 워크플로(raw_text → company_data → judge_result)를 짧은 TTL로
메모리에 보관 — create_company까지 클라이언트가 이 값들을 다시 통째로
재전송하지 않도록 하기 위함(docs/mcp-structured-contract/PLAN.md §8 참고).

uvicorn 단일 워커 전제(backend/main.py, workers 인자 없음) — 다중 워커로
확장하면 프로세스마다 메모리가 분리돼 이 캐시가 깨진다. 그때는 Redis 등
프로세스 공유 저장소로 교체해야 한다.
"""

import secrets
import threading
import time

_TTL_SECONDS = 40 * 60  # 30~60분 권장 범위의 중간값, 진행 시 갱신됨
_MAX_ENTRIES = 50  # raw_text가 이미 100,000자로 상한이 있어 건수 제한만으로 충분


class WorkflowNotFoundError(Exception):
    """workflow_id가 없거나 만료됨 — 재시작 안내 메시지 포함."""


def _not_found(workflow_id: str) -> WorkflowNotFoundError:
    return WorkflowNotFoundError(
        f"workflow_id({workflow_id})를 찾을 수 없습니다 — 만료됐거나 이미 저장 완료된 "
        "작업이거나 잘못된 id입니다. prepare_company_import부터 다시 시작하세요."
    )


class _Entry:
    __slots__ = ("data", "expires_at", "saving")

    def __init__(self, data: dict):
        self.data = data
        self.expires_at = time.monotonic() + _TTL_SECONDS
        self.saving = False


_store: dict[str, _Entry] = {}
_lock = threading.Lock()


def _purge_expired_locked() -> None:
    now = time.monotonic()
    for k in [k for k, e in _store.items() if e.expires_at < now]:
        _store.pop(k, None)


def _get_live_locked(workflow_id: str) -> "_Entry | None":
    """만료된 항목은 조회 시점에 바로 제거 — 일괄 purge(create/get)가 아직
    안 돌았어도 만료된 항목을 살아있는 것처럼 되살리지 않기 위함."""
    entry = _store.get(workflow_id)
    if entry is None:
        return None
    if entry.expires_at < time.monotonic():
        _store.pop(workflow_id, None)
        return None
    return entry


def create(data: dict) -> str:
    """새 workflow_id를 발급하고 초기 데이터(예: raw_text)를 저장한다."""
    with _lock:
        _purge_expired_locked()
        if len(_store) >= _MAX_ENTRIES:
            raise RuntimeError(
                "동시 진행 중인 MCP 등록 작업이 너무 많습니다 — 잠시 후 다시 시도하세요."
            )
        workflow_id = secrets.token_urlsafe(24)
        _store[workflow_id] = _Entry(dict(data))
        return workflow_id


def update(workflow_id: str, **fields) -> None:
    """기존 workflow에 필드를 추가/갱신하고 TTL을 갱신한다."""
    with _lock:
        entry = _get_live_locked(workflow_id)
        if entry is None:
            raise _not_found(workflow_id)
        entry.data.update(fields)
        entry.expires_at = time.monotonic() + _TTL_SECONDS


def get(workflow_id: str) -> dict:
    """저장된 데이터를 조회한다(읽기 전용 — TTL 갱신 없음)."""
    with _lock:
        _purge_expired_locked()
        entry = _get_live_locked(workflow_id)
        if entry is None:
            raise _not_found(workflow_id)
        return entry.data


def try_begin_save(workflow_id: str) -> bool:
    """저장 시작을 시도한다 — 이미 저장 진행 중이면 False(동시 요청 멱등 처리)."""
    with _lock:
        entry = _get_live_locked(workflow_id)
        if entry is None:
            raise _not_found(workflow_id)
        if entry.saving:
            return False
        entry.saving = True
        return True


def release_save(workflow_id: str) -> None:
    """저장 실패 시 saving 플래그를 해제해 재시도를 허용한다."""
    with _lock:
        entry = _get_live_locked(workflow_id)
        if entry is not None:
            entry.saving = False


def complete(workflow_id: str) -> None:
    """저장 성공 후 캐시에서 제거한다."""
    with _lock:
        _store.pop(workflow_id, None)


if __name__ == "__main__":
    wid = create({"raw_text": "원문"})
    assert get(wid)["raw_text"] == "원문"

    update(wid, company_data={"company_name": "테스트"})
    assert get(wid)["company_data"]["company_name"] == "테스트"

    assert try_begin_save(wid) is True
    assert try_begin_save(wid) is False  # 동시 저장 시도는 거부(멱등)
    release_save(wid)
    assert try_begin_save(wid) is True  # 실패 후 재시도는 허용

    complete(wid)
    try:
        get(wid)
        raise AssertionError("삭제된 workflow가 조회돼서는 안 됨")
    except WorkflowNotFoundError:
        pass

    try:
        get("존재하지-않는-id")
        raise AssertionError("없는 id는 예외를 던져야 함")
    except WorkflowNotFoundError:
        pass

    # 일괄 purge가 아직 안 돈 상태에서도 만료된 항목은 되살아나면 안 됨
    wid2 = create({"raw_text": "곧 만료"})
    _store[wid2].expires_at = time.monotonic() - 1  # 강제로 만료시킴(purge는 아직 안 됨)
    try:
        update(wid2, company_data={})
        raise AssertionError("만료된 workflow를 update가 되살려서는 안 됨")
    except WorkflowNotFoundError:
        pass

    _store.clear()
    for _ in range(_MAX_ENTRIES):
        create({})
    try:
        create({})
        raise AssertionError("최대 건수 초과 시 예외를 던져야 함")
    except RuntimeError:
        pass

    print("OK")
