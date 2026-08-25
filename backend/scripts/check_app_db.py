"""services/app_db.py 자체검증 — 임시 SQLite DB에서 schema/백필/동시성/FK cascade/
좀비 pending을 확인한다. 원래 app_db.py의 `if __name__ == "__main__"` 블록이었으나
운영 코드와 분리하기 위해 이 스크립트로 이동했다(로직 변경 없음).

실행: backend/에서 `python3 -m scripts.check_app_db`
"""
import os
import pathlib
import tempfile
import threading

from config import settings
from services import app_db
from services.app_db import (
    create_fit_history_entry,
    create_profile_version,
    create_rag_chat,
    delete_fit_history_for_slug,
    delete_profile_version,
    delete_qa_history_for_slug,
    delete_rag_chat,
    get_connection,
    get_fit_history_entry,
    get_profile_version,
    get_rag_chat,
    init_db,
    insert_pending_qa,
    insert_pending_rag_message,
    is_healthy,
    latest_profile_version_id,
    list_fit_history,
    list_profile_versions,
    list_qa_context,
    list_qa_history,
    list_rag_chats,
    list_rag_context,
    list_rag_messages,
    mark_qa_done,
    mark_qa_failed,
    mark_rag_message_done,
    mark_rag_message_failed,
    migrate_qa_slug_history,
    migrate_rag_chat,
    set_rag_chat_title_if_empty,
    update_profile_version_note,
    _backfill_fit_history,
)

with tempfile.TemporaryDirectory() as tmp:
    settings.data_dir = pathlib.Path(tmp)
    # DB_PATH는 함수가 아니라 재할당되는 모듈 전역이라, 이름으로 import하면 이 시점 값을
    # 그대로 복사해버려 이후 재할당이 반영 안 된다 — app_db.DB_PATH로 매번 한정 접근한다.
    app_db.DB_PATH = settings.data_dir / "app.db"

    init_db()
    assert os.path.exists(app_db.DB_PATH)
    assert list_profile_versions() == []  # 프로필 파일 없으면 백필 안 함

    # 프로필 소급 백필 — 이 기능 생기기 전부터 있던 candidate_profile.md가
    # 생긴 뒤 init_db()를 다시 부르면(=앱 재시작 흉내) 1건만 생기고, 반복
    # 호출해도 중복 안 생겨야 한다.
    settings.candidate_profile_path.write_text("---\nname: 테스트\n---\n소급 프로필", encoding="utf-8")
    init_db()
    profile_backfilled = list_profile_versions()
    assert len(profile_backfilled) == 1
    assert get_profile_version(profile_backfilled[0]["id"])["content"] == "---\nname: 테스트\n---\n소급 프로필"
    init_db()  # 재시작 흉내 — 중복 생성 안 됨
    assert len(list_profile_versions()) == 1
    assert delete_profile_version(profile_backfilled[0]["id"]) is True
    assert list_profile_versions() == []
    # 사용자가 마지막 스냅샷을 명시적으로 지운 것 — 프로필 파일이 그대로
    # 있어도 재시작 때 되살아나면 안 된다("한 번도 없었음"과 구분)
    init_db()
    assert list_profile_versions() == [], "삭제한 스냅샷이 재시작으로 되살아나면 안 됨"

    # 이후 테스트들이 빈 상태를 가정하므로 백필 테스트용 파일 정리
    # (파일을 안 지우면 이후에도 계속 존재하지만, 위 재검증으로 더 이상
    # 백필 대상이 아님을 확인했으므로 상태 정리 차원)
    settings.candidate_profile_path.unlink()

    # 소급 이력 백필 — fit_score 있는 기존 회사 파일이 생긴 뒤 init_db()를 다시
    # 부르면(=앱 재시작 상황을 흉내) 1건만 생기고, 반복 호출해도 중복 안 생겨야 한다.
    settings.companies_dir.mkdir(parents=True, exist_ok=True)
    (settings.companies_dir / "백필테스트__직무.md").write_text(
        "---\nfit_score: 55\nfit_label: 조건부추천\n---\n본문", encoding="utf-8"
    )
    (settings.companies_dir / "미평가회사__직무.md").write_text(
        "---\ncompany_name: 미평가회사\n---\n본문(fit_score 없음)", encoding="utf-8"
    )
    init_db()
    backfilled = list_fit_history("백필테스트__직무")
    assert len(backfilled) == 1
    assert backfilled[0]["fit_score"] == 55
    assert backfilled[0]["profile_version_id"] is None  # "이전 버전 불명"
    assert list_fit_history("미평가회사__직무") == []  # fit_score 없으면 백필 안 함
    init_db()  # 재시작 흉내 — 중복 생성 안 됨
    assert len(list_fit_history("백필테스트__직무")) == 1

    # 손상된 회사 파일 하나가 섞여도 나머지 정상 회사는 백필돼야 한다(트랜잭션
    # 전체 롤백 금지 — 한 파일이 깨졌다고 이미 처리된 정상 이력까지 사라지면 안 됨)
    (settings.companies_dir / "깨진회사__직무.md").write_text(
        "---\nfit_score: [닫히지 않은 리스트\n---\n본문", encoding="utf-8"
    )
    (settings.companies_dir / "새회사__직무.md").write_text(
        "---\nfit_score: 88\nfit_label: 추천\n---\n본문", encoding="utf-8"
    )
    init_db()
    assert len(list_fit_history("새회사__직무")) == 1
    assert list_fit_history("새회사__직무")[0]["fit_score"] == 88
    assert list_fit_history("깨진회사__직무") == []  # 파싱 실패 → 건너뜀, 크래시 안 함
    assert len(list_fit_history("백필테스트__직무")) == 1  # 기존 이력도 롤백 안 됨

    # DB 자체가 고장난 경우는 파일 파싱 실패와 달리 삼키면 안 되고 그대로 예외가 올라가야
    # 한다 — 안 그러면 init_db()가 DB 장애를 감지 못 해서 "정상 0건"처럼 조용히 넘어간다.
    # chmod 0o444(읽기전용)로 시뮬레이션하면 Docker 컨테이너는 root로 도는데 root는 파일
    # 권한 비트를 무시하고 그냥 써버려서 이 검증 자체가 무력화된다(실측 확인) — 대신 DB_PATH를
    # 디렉토리 경로로 돌리면 sqlite3.connect()가 애초에 "파일 열기" 자체를 못 해서
    # root든 아니든 항상 실패한다.
    (settings.companies_dir / "읽기전용테스트__직무.md").write_text(
        "---\nfit_score: 70\n---\n본문", encoding="utf-8"
    )
    broken_db_path, app_db.DB_PATH = app_db.DB_PATH, settings.data_dir
    try:
        raised = False
        try:
            _backfill_fit_history()
        except Exception:
            raised = True
        assert raised, "DB 접근 불가 상태에서도 예외 없이 조용히 끝나면 안 됨"

        # 공개 진입점인 init_db()는 반대로 예외를 밖으로 내지 않고 흡수해야
        # 한다(main.py가 앱 전체를 죽이지 않도록) — 대신 is_healthy()가 False가
        # 되고, 그 상태에서 회복(정상 경로 복원 후 재호출)하면 다시 True가 되는지도 확인
        assert is_healthy() is True  # 지금까지는 전부 정상 케이스였음
        init_db()
        assert is_healthy() is False
    finally:
        app_db.DB_PATH = broken_db_path  # 이후 테스트가 계속 쓸 수 있도록 원복
    init_db()
    assert is_healthy() is True  # 정상 경로 복원 후 재시작하면 다시 정상으로 돌아옴

    version_id = create_profile_version("테스트 프로필 내용")
    version_id2 = create_profile_version("두번째 프로필 내용", note="사이드 프로젝트 추가")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM profile_versions WHERE id = ?", (version_id,)
        ).fetchone()
        assert row["content"] == "테스트 프로필 내용"
        assert row["note"] is None  # note 안 남기면 NULL

        row2 = conn.execute(
            "SELECT * FROM profile_versions WHERE id = ?", (version_id2,)
        ).fetchone()
        assert row2["note"] == "사이드 프로젝트 추가"

    versions = list_profile_versions()
    assert [v["id"] for v in versions] == [version_id2, version_id]  # 최신순

    fetched = get_profile_version(version_id)
    assert fetched["content"] == "테스트 프로필 내용"
    assert get_profile_version(999999) is None

    assert update_profile_version_note(version_id, "나중에 붙인 메모") is True
    assert get_profile_version(version_id)["note"] == "나중에 붙인 메모"
    assert update_profile_version_note(999999, "없는 버전") is False

    assert delete_profile_version(version_id) is True
    assert get_profile_version(version_id) is None
    assert delete_profile_version(version_id) is False  # 이미 삭제됨

    # fit_history는 FK를 강제하지 않으므로, 삭제된 스냅샷을 참조해도 insert는 성공해야
    # 한다("삭제됨" 표시는 조회 시점에 판단, 위 docs/profile-history/PLAN.md 참고)
    assert latest_profile_version_id() == version_id2
    create_fit_history_entry("테스트회사__직무", version_id, 72, "추천", "리포트 원문(삭제된 버전 참조)")
    create_fit_history_entry("테스트회사__직무", version_id2, 62, "조건부추천", "리포트 원문(정상 참조)")

    hist_list = list_fit_history("테스트회사__직무")
    assert len(hist_list) == 2
    assert hist_list[0]["fit_score"] == 62  # 최신순(나중에 넣은 것)
    assert hist_list[0]["profile_version_created_at"] is not None  # 정상 참조
    assert hist_list[1]["fit_score"] == 72
    assert hist_list[1]["profile_version_created_at"] is None  # 삭제된 버전 참조 → "삭제됨" 판단용

    entry = get_fit_history_entry(hist_list[0]["id"])
    assert entry["content"] == "리포트 원문(정상 참조)"
    assert get_fit_history_entry(999999) is None

    # 회사 삭제 시 이력도 같이 지워야 slug 재사용 시 옛 이력이 다시 안 붙는다
    assert delete_fit_history_for_slug("테스트회사__직무") == 2
    assert list_fit_history("테스트회사__직무") == []
    assert delete_fit_history_for_slug("테스트회사__직무") == 0  # 이미 없음

    # 초기화 재호출(idempotent) 확인
    init_db()

    # qa_messages: pending → done/failed 전환, 조회 함수들
    mid1 = insert_pending_qa("테스트회사__직무", "연봉 협상 여지 있나요?")
    pending_rows = list_qa_history("테스트회사__직무")
    assert len(pending_rows) == 1
    assert pending_rows[0]["status"] == "pending"
    assert pending_rows[0]["answer"] is None

    mark_qa_done(mid1, "네, 협상 가능합니다.")
    after_done = list_qa_history("테스트회사__직무")
    assert after_done[0]["status"] == "done"
    assert after_done[0]["answer"] == "네, 협상 가능합니다."

    mid2 = insert_pending_qa("테스트회사__직무", "재택 가능한가요?")
    mark_qa_failed(mid2, "LLM 서비스 오류")
    failed_row = [r for r in list_qa_history("테스트회사__직무") if r["id"] == mid2][0]
    assert failed_row["status"] == "failed"
    assert failed_row["error"] == "LLM 서비스 오류"
    assert failed_row["answer"] is None

    # list_qa_context: status='done'만, 오래된 순 — failed/pending은 컨텍스트에서 제외
    context = list_qa_context("테스트회사__직무")
    assert len(context) == 1  # done인 mid1만
    assert context[0]["question"] == "연봉 협상 여지 있나요?"

    # cap 확인: limit보다 많으면 최근 것만, 오래된 순으로
    for i in range(3):
        mid = insert_pending_qa("cap테스트__직무", f"질문{i}")
        mark_qa_done(mid, f"답변{i}")
    capped = list_qa_context("cap테스트__직무", limit=2)
    assert [c["question"] for c in capped] == ["질문1", "질문2"]  # 오래된 것(질문0) 잘림, 순서 유지

    # 좀비 pending: 남은 pending 행이 있는 상태에서 init_db()를 다시 부르면(=재시작 흉내)
    # failed로 전환돼야 한다 — "N분 지났으면" 추측 없이, 서버가 막 켜진 시점 자체가 근거
    zombie_id = insert_pending_qa("좀비테스트__직무", "재시작 전에 물어본 질문")
    init_db()
    zombie_row = [r for r in list_qa_history("좀비테스트__직무") if r["id"] == zombie_id][0]
    assert zombie_row["status"] == "failed"
    assert zombie_row["error"] == "서버 재시작으로 응답을 받지 못했습니다"
    # done/failed로 이미 끝난 행은 재시작해도 안 건드려야 한다
    after_restart = list_qa_history("테스트회사__직무")
    assert after_restart[0]["status"] == "done"  # mid1

    # 회사 삭제 시 QnA 대화도 같이 지워야 slug 재사용 시 옛 대화가 다시 안 붙는다
    # (mid1=done, mid2=failed 2건 — status 상관없이 그 slug 전부 지워져야 함)
    assert delete_qa_history_for_slug("테스트회사__직무") == 2
    assert list_qa_history("테스트회사__직무") == []
    assert delete_qa_history_for_slug("테스트회사__직무") == 0  # 이미 없음

    # migrate_qa_slug_history: 기기별 멱등 처리 — "슬러그에 메시지 있음"이 아니라
    # "이 기기가 이 슬러그를 옮긴 적 있음" 기준이어야 한다(v1.5.1 회귀 수정)
    pairs_a = [("데스크탑 질문1", "데스크탑 답변1"), ("데스크탑 질문2", "데스크탑 답변2")]
    inserted_a = migrate_qa_slug_history("device-desktop", "마이그레이션테스트__직무", pairs_a)
    assert inserted_a == 2
    assert len(list_qa_history("마이그레이션테스트__직무")) == 2

    # 같은 기기가 같은 슬러그를 재호출하면(응답 유실 후 재시도 등) 건너뛰어야 함
    retry_a = migrate_qa_slug_history("device-desktop", "마이그레이션테스트__직무", pairs_a)
    assert retry_a == 0
    assert len(list_qa_history("마이그레이션테스트__직무")) == 2  # 중복 안 생김

    # 다른 기기가 같은 슬러그에 대해 다른 이력을 갖고 있으면, 먼저 옮겨진 게 있어도
    # 반드시 같이 옮겨져야 한다(v1.5.1은 여기서 스킵해버리던 회귀)
    pairs_b = [("모바일 질문1", "모바일 답변1")]
    inserted_b = migrate_qa_slug_history("device-mobile", "마이그레이션테스트__직무", pairs_b)
    assert inserted_b == 1
    all_migrated = list_qa_history("마이그레이션테스트__직무")
    assert len(all_migrated) == 3  # 데스크탑 2건 + 모바일 1건, 둘 다 살아있음
    assert any(m["question"] == "모바일 질문1" for m in all_migrated)

    # v1.5.1 복구 경로: qa_migrations 기록이 아예 없는(=기기 추적 테이블이 없던 시절
    # 이미 성공한) 새 기기가 같은 내용으로 재호출하면, "already" 단락(같은 device의
    # 재시도 체크)이 아니라 content 기반 스킵으로 걸러져야 한다 — device_id를 반드시
    # 처음 쓰는 값으로 해야 이 경로를 제대로 검증한다(Codex 2차 리뷰가 지적한 맹점:
    # 이미 qa_migrations 표식이 있는 기기를 재사용하면 content 검사 전에 반환돼버림)
    retry_legacy_same_content = migrate_qa_slug_history(
        "device-legacy-untracked", "마이그레이션테스트__직무", pairs_a
    )
    assert retry_legacy_same_content == 0
    assert len(list_qa_history("마이그레이션테스트__직무")) == 3  # 중복 안 생김

    # 반대로 새 기기가 일부는 서버에 이미 있는 내용(우연 일치), 일부는 진짜 새 내용을
    # 보내면 새 것만 들어가야 한다
    pairs_mixed = [("데스크탑 질문1", "데스크탑 답변1"), ("태블릿 질문1", "태블릿 답변1")]
    inserted_mixed = migrate_qa_slug_history(
        "device-tablet", "마이그레이션테스트__직무", pairs_mixed
    )
    assert inserted_mixed == 1  # 겹치는 것 스킵, 새 것만 삽입
    final_migrated = list_qa_history("마이그레이션테스트__직무")
    assert len(final_migrated) == 4
    assert any(m["question"] == "태블릿 질문1" for m in final_migrated)

    # occurrence 소비 회귀: 완전히 같은 (질문,답변) 쌍이 로컬 이력에 두 번 있는 정상
    # 케이스 — 서버에 기존 데이터가 없는 슬러그에 처음 옮길 때도 boolean 존재 체크였다면
    # 두 번째 턴이 "방금 넣은 첫 번째 턴"과 겹쳐 보여서 유실된다. 둘 다 들어가야 한다.
    pairs_dup = [("같은 질문", "같은 답변"), ("같은 질문", "같은 답변")]
    inserted_dup = migrate_qa_slug_history(
        "device-dup", "중복턴테스트__직무", pairs_dup
    )
    assert inserted_dup == 2  # 둘 다 삽입돼야 함 — occurrence 유실 금지
    assert len(list_qa_history("중복턴테스트__직무")) == 2

    # 기존 서버 데이터가 정확히 1건, 입력에 같은 내용이 2건이면 기존 1건만큼만
    # 스킵하고 초과분 1건은 새로 삽입돼야 한다(기존 개수만 한 번씩 소비)
    inserted_seed = migrate_qa_slug_history(
        "device-dup-seed", "중복턴테스트2__직무", [("같은 질문", "같은 답변")]
    )
    assert inserted_seed == 1
    inserted_dup_more = migrate_qa_slug_history(
        "device-dup-2", "중복턴테스트2__직무", pairs_dup
    )
    assert inserted_dup_more == 1  # 기존 1개만큼 스킵, 초과분 1개만 삽입
    assert len(list_qa_history("중복턴테스트2__직무")) == 2

    # 동시성 회귀: 서로 다른 두 기기가 정확히 동시에 같은 슬러그의 같은 내용을
    # 복구하면, BEGIN IMMEDIATE로 직렬화되지 않을 경우 둘 다 같은(비어있는) occurrence
    # 스냅샷을 읽어 중복 삽입된다. 순차 실행과 같은 결과가 나와야 한다.
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def _concurrent_worker(device_id: str) -> None:
        barrier.wait()
        results[device_id] = migrate_qa_slug_history(
            device_id, "동시성테스트__직무", [("동시 질문", "동시 답변")]
        )

    t1 = threading.Thread(target=_concurrent_worker, args=("device-concurrent-1",))
    t2 = threading.Thread(target=_concurrent_worker, args=("device-concurrent-2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert sorted(results.values()) == [0, 1]  # 하나만 실제 삽입, 다른 하나는 스킵
    assert len(list_qa_history("동시성테스트__직무")) == 1  # 중복 안 생김

    # rag_chats/rag_messages: 채팅방 생성 → pending → done/failed, 컨텍스트 조회
    create_rag_chat("chat-1", created_at="2026-08-22T00:00:00")
    assert get_rag_chat("chat-1")["title"] is None
    set_rag_chat_title_if_empty("chat-1", "첫 질문 요약")
    assert get_rag_chat("chat-1")["title"] == "첫 질문 요약"
    set_rag_chat_title_if_empty("chat-1", "덮어쓰면 안 됨")  # 이미 제목 있으면 무시
    assert get_rag_chat("chat-1")["title"] == "첫 질문 요약"
    assert get_rag_chat("없는챗") is None

    rmid1 = insert_pending_rag_message("chat-1", "이 회사 강점은?")
    assert [m["status"] for m in list_rag_messages("chat-1")] == ["pending"]
    mark_rag_message_done(rmid1, '{"answer": "강점입니다", "tool_calls": [], "provider": "google"}')
    assert list_rag_messages("chat-1")[0]["status"] == "done"

    rmid2 = insert_pending_rag_message("chat-1", "연봉은?")
    mark_rag_message_failed(rmid2, "LLM 서비스 오류")
    failed_rag = [m for m in list_rag_messages("chat-1") if m["id"] == rmid2][0]
    assert failed_rag["status"] == "failed"
    assert failed_rag["data"] is None

    rag_context = list_rag_context("chat-1")
    assert len(rag_context) == 1  # done인 rmid1만, pending/failed 제외
    assert rag_context[0]["question"] == "이 회사 강점은?"

    # FK CASCADE: 채팅방을 지우면 소속 메시지도 자동으로 같이 지워져야 한다
    assert delete_rag_chat("chat-1") is True
    with get_connection() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) AS c FROM rag_messages WHERE chat_id = 'chat-1'"
        ).fetchone()
        assert remaining["c"] == 0, "ON DELETE CASCADE가 동작 안 함 — 고아 메시지 남음"
    assert get_rag_chat("chat-1") is None
    assert delete_rag_chat("chat-1") is False  # 이미 삭제됨

    # migrate_rag_chat: 방+메시지를 한 트랜잭션으로, 재시도 시 중복 없이 멱등
    entries = [("마이그레이션 질문1", '{"answer": "답1"}'), ("마이그레이션 질문2", '{"answer": "답2"}')]
    inserted = migrate_rag_chat("chat-migrate-1", "옛 채팅", "2026-08-01T00:00:00", entries)
    assert inserted == 2
    assert len(list_rag_messages("chat-migrate-1")) == 2

    # 같은 chat_id로 재시도(응답 유실 후 재호출 등)하면 건너뛰어야 함 — 중복 방지
    retry = migrate_rag_chat("chat-migrate-1", "옛 채팅", "2026-08-01T00:00:00", entries)
    assert retry == 0
    assert len(list_rag_messages("chat-migrate-1")) == 2  # 중복 안 생김

    # 동시성 회귀: 같은 chat_id를 두 기기가 정확히 동시에 이관하면, SELECT-후-INSERT였다면
    # 둘 다 "없음"을 보고 진행해 한쪽이 기본키 충돌(IntegrityError)로 500이 난다.
    # INSERT ... ON CONFLICT DO NOTHING이면 오류 없이 한쪽만 성공(1)하고 다른 쪽은
    # 멱등하게 0을 반환해야 한다.
    rag_barrier = threading.Barrier(2)
    rag_results: dict[str, int] = {}

    def _concurrent_rag_worker(label: str) -> None:
        rag_barrier.wait()
        rag_results[label] = migrate_rag_chat(
            "chat-concurrent", "동시 채팅", "2026-08-22T00:00:00",
            [("동시 RAG 질문", '{"answer": "동시 답"}')],
        )

    rt1 = threading.Thread(target=_concurrent_rag_worker, args=("a",))
    rt2 = threading.Thread(target=_concurrent_rag_worker, args=("b",))
    rt1.start()
    rt2.start()
    rt1.join()
    rt2.join()
    assert sorted(rag_results.values()) == [0, 1]
    assert len(list_rag_messages("chat-concurrent")) == 1  # 중복 안 생김

    # 좀비 pending도 QnA와 동일하게 서버 재시작 시점에 failed로 전환돼야 한다
    create_rag_chat("chat-2")
    zombie_rag_id = insert_pending_rag_message("chat-2", "재시작 전 질문")
    init_db()
    zombie_rag = [m for m in list_rag_messages("chat-2") if m["id"] == zombie_rag_id][0]
    assert zombie_rag["status"] == "failed"
    assert zombie_rag["error"] == "서버 재시작으로 응답을 받지 못했습니다"

    chats = list_rag_chats()
    assert {c["id"] for c in chats} == {"chat-2", "chat-migrate-1", "chat-concurrent"}  # chat-1은 위에서 삭제됨

print("app_db self-check 통과")
