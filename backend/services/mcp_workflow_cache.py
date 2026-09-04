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
    __slots__ = ("data", "expires_at", "saving", "completed", "result")

    def __init__(self, data: dict):
        self.data = data
        self.expires_at = time.monotonic() + _TTL_SECONDS
        self.saving = False
        self.completed = False
        self.result: dict | None = None


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
    """기존 workflow에 필드를 추가/갱신하고 TTL을 갱신한다. 저장이 진행 중이거나
    이미 완료된 workflow는 거부한다 — 완료된 entry에 데이터가 다시 채워지면
    completed 상태와 실제 데이터가 어긋난다."""
    with _lock:
        entry = _get_live_locked(workflow_id)
        if entry is None:
            raise _not_found(workflow_id)
        if entry.saving or entry.completed:
            raise WorkflowNotFoundError(
                f"workflow_id({workflow_id})는 이미 저장이 진행 중이거나 완료돼 더 이상 갱신할 수 없습니다."
            )
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


def get_completed_result(workflow_id: str) -> "dict | None":
    """이미 저장 완료된 workflow면 그 결과를 반환, 아니면(진행 중/미완료) None.
    존재하지 않는 workflow_id도 None — 완료 여부만 보는 조용한 조회라 예외를
    던지지 않는다(멱등 재호출 판단용, 없는 id 자체를 알리는 건 다른 함수의 몫)."""
    with _lock:
        entry = _get_live_locked(workflow_id)
        if entry is None or not entry.completed:
            return None
        return entry.result


def try_begin_save(workflow_id: str) -> bool:
    """저장 시작을 시도한다 — 이미 저장 진행 중이거나 완료됐으면 False(동시 요청 멱등 처리).
    시작 시점에 TTL을 갱신해, 저장이 오래 걸려도 그 사이 TTL 만료로 entry가
    사라지지 않게 한다."""
    with _lock:
        entry = _get_live_locked(workflow_id)
        if entry is None:
            raise _not_found(workflow_id)
        if entry.saving or entry.completed:
            return False
        entry.saving = True
        entry.expires_at = time.monotonic() + _TTL_SECONDS
        return True


def release_save(workflow_id: str) -> None:
    """저장 실패 시 saving 플래그를 해제해 재시도를 허용한다(데이터는 그대로 유지)."""
    with _lock:
        entry = _get_live_locked(workflow_id)
        if entry is not None:
            entry.saving = False


def complete(workflow_id: str, result: dict) -> None:
    """저장 성공 처리 — raw_text/company_data/judge_result 등 대용량 데이터는
    비우고, 완료 상태와 결과만 TTL까지 유지한다(동일 workflow_id 재호출 시
    저장을 반복하지 않고 같은 결과를 그대로 돌려주기 위함 — 멱등 재호출 지원).
    만료됐거나 try_begin_save로 저장을 시작한 적 없는 entry는 조용히 무시한다
    (만료된 entry를 되살리지 않기 위해 `_store.get()`이 아닌 `_get_live_locked()`를
    쓴다 — 그 사이 TTL이 지났다면 client는 get_completed_result()에서 None을
    받고 처음부터 다시 시작해야 한다)."""
    with _lock:
        entry = _get_live_locked(workflow_id)
        if entry is None or not entry.saving:
            return
        entry.data = {}
        entry.saving = False
        entry.completed = True
        entry.result = result
        entry.expires_at = time.monotonic() + _TTL_SECONDS


if __name__ == "__main__":
    wid = create({"raw_text": "원문"})
    assert get(wid)["raw_text"] == "원문"

    update(wid, company_data={"company_name": "테스트"})
    assert get(wid)["company_data"]["company_name"] == "테스트"

    assert try_begin_save(wid) is True
    assert try_begin_save(wid) is False  # 동시 저장 시도는 거부(멱등)
    assert get_completed_result(wid) is None  # 아직 완료 아님
    release_save(wid)
    assert try_begin_save(wid) is True  # 실패 후 재시도는 허용

    complete(wid, {"slug": "test-slug"})
    assert try_begin_save(wid) is False  # 완료된 workflow는 다시 저장 시작 못 함
    assert get_completed_result(wid) == {"slug": "test-slug"}  # 멱등 재호출용 결과 유지
    assert get(wid) == {}  # 대용량 데이터는 비워짐

    # 완료된 workflow에 update()를 걸면 거부돼야 함(완료 후 데이터가 다시 채워지면 안 됨)
    try:
        update(wid, company_data={"company_name": "다시채움시도"})
        raise AssertionError("완료된 workflow를 update가 받아들이면 안 됨")
    except WorkflowNotFoundError:
        pass

    try:
        get("존재하지-않는-id")
        raise AssertionError("없는 id는 예외를 던져야 함")
    except WorkflowNotFoundError:
        pass
    assert get_completed_result("존재하지-않는-id") is None  # 조용한 조회는 예외 없음

    # 일괄 purge가 아직 안 돈 상태에서도 만료된 항목은 되살아나면 안 됨
    wid2 = create({"raw_text": "곧 만료"})
    _store[wid2].expires_at = time.monotonic() - 1  # 강제로 만료시킴(purge는 아직 안 됨)
    try:
        update(wid2, company_data={})
        raise AssertionError("만료된 workflow를 update가 되살려서는 안 됨")
    except WorkflowNotFoundError:
        pass

    # 저장 도중(TTL 만료 후) complete()가 오면 되살리지 말고 조용히 무시해야 함
    wid3 = create({"raw_text": "만료 후 complete 테스트"})
    assert try_begin_save(wid3) is True
    _store[wid3].expires_at = time.monotonic() - 1  # 저장 도중 만료된 상황을 시뮬레이션
    complete(wid3, {"slug": "should-not-exist"})
    assert get_completed_result(wid3) is None, "만료된 workflow가 complete로 되살아나면 안 됨"

    # try_begin_save는 저장 시작 시점에 TTL을 갱신해야 함(오래 걸리는 저장 도중 만료 방지)
    wid4 = create({"raw_text": "TTL 갱신 테스트"})
    _store[wid4].expires_at = time.monotonic() + 1  # 곧 만료될 것처럼 설정(아직 안 지남)
    assert try_begin_save(wid4) is True
    assert _store[wid4].expires_at > time.monotonic() + 60, "저장 시작 시 TTL이 갱신돼야 함"

    _store.clear()
    for _ in range(_MAX_ENTRIES):
        create({})
    try:
        create({})
        raise AssertionError("최대 건수 초과 시 예외를 던져야 함")
    except RuntimeError:
        pass

    print("OK")
