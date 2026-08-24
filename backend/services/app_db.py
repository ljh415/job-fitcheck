"""프로필 스냅샷/회사별 적합도 평가 이력을 저장하는 SQLite 연결.

단일 파일(data/app.db)로 관리한다. RAG의 Postgres(opt-in)와는 무관 — 프로필 히스토리는
RAG를 안 쓰는 사용자도 써야 하는 핵심 기능이라 선택 기능의 DB에 의존하지 않는다.
세부 설계는 docs/profile-history/PLAN.md 참고.
"""
import logging
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime

import frontmatter

from config import settings

logger = logging.getLogger(__name__)

DB_PATH = settings.data_dir / "app.db"

_healthy = True  # init_db()가 실패하면 False로 남는다 — 조회 API가 이 값을 보고
                  # "이력 0건"과 "DB 장애"를 구분한다(is_healthy() 참고).


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # rag_messages.chat_id가 rag_chats(id)를 FK(ON DELETE CASCADE)로 참조한다 — SQLite는
    # 연결마다 매번 다시 켜야 강제된다(DB 파일에 영속되는 설정이 아님). 기존 테이블
    # (profile_versions/fit_history)엔 FK가 전혀 없어서 켜도 기존 동작이 깨질 위험은 없다.
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def is_healthy() -> bool:
    """init_db()가 성공적으로 끝났는지. False면 조회 API가 빈 목록 대신 503을
    반환해야 한다 — 그렇지 않으면 SELECT는 계속 성공할 수 있어(read-only DB 등)
    "이력이 원래 없음"과 "DB 장애로 못 채워짐"이 화면에서 구분이 안 된다."""
    return _healthy


def init_db() -> None:
    """앱 시작 시 호출 — 테이블이 없으면 생성한다. 실패해도 예외를 밖으로 내지
    않는다 — 회사 CRUD 같은 핵심 기능까지 이 기능 하나 때문에 막히면 안 된다.
    대신 is_healthy()가 False가 되어 조회 API가 이를 반영한다."""
    global _healthy
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS profile_versions (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    content    TEXT NOT NULL,
                    note       TEXT
                )
            """)
            # note 컬럼 마이그레이션 — 이미 만들어진 DB(테이블은 있지만 컬럼 추가 전)에도 대응
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(profile_versions)").fetchall()}
            if "note" not in cols:
                conn.execute("ALTER TABLE profile_versions ADD COLUMN note TEXT")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fit_history (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_slug       TEXT NOT NULL,
                    created_at         TEXT NOT NULL,
                    profile_version_id INTEGER,
                    fit_score          INTEGER,
                    fit_label          TEXT,
                    content            TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_fit_history_slug
                ON fit_history(company_slug, created_at)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS qa_messages (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_slug TEXT NOT NULL,
                    question     TEXT NOT NULL,
                    answer       TEXT,
                    status       TEXT NOT NULL DEFAULT 'pending',
                    error        TEXT,
                    created_at   TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_qa_messages_slug
                ON qa_messages(company_slug, id)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS qa_migrations (
                    device_id    TEXT NOT NULL,
                    company_slug TEXT NOT NULL,
                    migrated_at  TEXT NOT NULL,
                    PRIMARY KEY (device_id, company_slug)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rag_chats (
                    id         TEXT PRIMARY KEY,
                    title      TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rag_messages (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id      TEXT NOT NULL REFERENCES rag_chats(id) ON DELETE CASCADE,
                    question     TEXT NOT NULL,
                    data         TEXT,
                    status       TEXT NOT NULL DEFAULT 'pending',
                    error        TEXT,
                    created_at   TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_rag_messages_chat
                ON rag_messages(chat_id, id)
            """)
            conn.commit()
        _backfill_profile_version()
        _backfill_fit_history()
        _fail_stale_qa_pending()
        _fail_stale_rag_pending()
        _healthy = True
    except Exception as e:
        logger.error("프로필 히스토리 DB 초기화 실패 - 이 기능만 비활성화됩니다: %s", e)
        _healthy = False


def _backfill_profile_version() -> None:
    """프로필 스냅샷이 하나도 없는데 candidate_profile.md는 있으면(이 기능이
    생기기 전부터 있던 기존 프로필), 그 파일을 첫 스냅샷으로 소급 적용한다.
    이건 "최초 설치 시 1회" 마이그레이션이지, 사용자가 스냅샷을 전부 삭제한
    걸 되살리는 게 아니다 — 그래서 단순히 "테이블이 비었는지"가 아니라
    sqlite_sequence(SQLite가 AUTOINCREMENT 최고값을 추적하는 내장 테이블,
    행을 다 지워도 기록은 남음)에 이 테이블 이력이 아예 없을 때만("지금까지
    단 한 번도 INSERT가 없었을 때만") 백필한다. 안 그러면 사용자가 마지막
    스냅샷을 명시적으로 삭제해도 다음 재시작에 새 id로 조용히 되살아난다."""
    with get_connection() as conn:
        ever_inserted = conn.execute(
            "SELECT 1 FROM sqlite_sequence WHERE name = 'profile_versions'"
        ).fetchone()
        if ever_inserted:
            return
        if not settings.candidate_profile_path.exists():
            return
        try:
            content = settings.candidate_profile_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("프로필 소급 스냅샷 생성 실패: %s", e)
            return
        conn.execute(
            "INSERT INTO profile_versions (created_at, content, note) VALUES (?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), content, None),
        )
        conn.commit()


def _backfill_fit_history() -> None:
    """기존 회사(.md에 fit_score 있음)의 이력이 하나도 없으면 현재 값을 첫 이력으로
    소급 적용한다(profile_version_id=NULL, "이전 버전 불명"). 회사별로 이력이 하나라도
    있으면 건너뛰므로 init_db() 호출마다(=앱 시작마다) 반복 실행해도 중복 생성되지 않는다.
    회사 파일 하나가 파싱 실패해도(손상된 frontmatter 등) 그 파일만 건너뛰고 나머지는
    계속 진행한다 — 한 파일 때문에 전체가 실패하면 트랜잭션이 커밋 전 롤백돼 정상
    회사들 이력까지 통째로 안 생긴다.
    단, try/except는 "파일 내용 파싱"만 감싼다 — DB 자체가 read-only/손상이면(SQLite
    호출 실패) 그건 파일 문제가 아니라 인프라 문제라 여기서 삼키지 않고 그대로
    올려보내서 init_db()가 잡아 health flag(is_healthy())로 바꾸게 한다. 안 그러면
    DB가 통째로 고장나도 "파일 파싱 실패"로 매번 조용히 넘어가 아무도 못 알아챈다."""
    with get_connection() as conn:
        for md_path in sorted(settings.companies_dir.glob("*.md")):
            slug = md_path.stem
            existing = conn.execute(
                "SELECT 1 FROM fit_history WHERE company_slug = ? LIMIT 1", (slug,)
            ).fetchone()
            if existing:
                continue
            try:
                post = frontmatter.load(str(md_path))
                fit_score = post.metadata.get("fit_score")
                fit_label = post.metadata.get("fit_label")
                content = md_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("소급 이력 생성 실패 - 파일 파싱 오류 (slug=%s): %s", slug, e)
                continue
            if fit_score is None:
                continue
            conn.execute(
                "INSERT INTO fit_history (company_slug, created_at, profile_version_id, "
                "fit_score, fit_label, content) VALUES (?, ?, NULL, ?, ?, ?)",
                (slug, datetime.now().isoformat(timespec="seconds"), fit_score, fit_label, content),
            )
        conn.commit()


def _fail_stale_qa_pending() -> None:
    """서버 시작 시점에 남아있는 status='pending' 행은 예외 없이 이전 프로세스가 죽으며
    (컨테이너 재시작 등) 못 끝낸 요청이다 — 지금 이 프로세스가 막 시작했는데 pending인
    행이 있다는 것 자체가 증거라서, "몇 분 지났으면"같은 시간 기반 추측이 필요 없다.
    좀비 pending을 다루는 확정적 방법(docs/chat-history-server-storage/PLAN.md 참고)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE qa_messages SET status = 'failed', "
            "error = '서버 재시작으로 응답을 받지 못했습니다' WHERE status = 'pending'"
        )
        conn.commit()


def _fail_stale_rag_pending() -> None:
    """_fail_stale_qa_pending()과 동일한 이유·동일한 방식 — RAG도 좀비 pending은 서버 시작
    시점 일괄 정리로 확정적으로 처리한다."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE rag_messages SET status = 'failed', "
            "error = '서버 재시작으로 응답을 받지 못했습니다' WHERE status = 'pending'"
        )
        conn.commit()


def create_profile_version(content: str, note: str | None = None) -> int:
    """프로필 스냅샷을 저장하고 새로 생성된 id를 반환한다. note는 사용자가 남긴 짧은 메모(선택)."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO profile_versions (created_at, content, note) VALUES (?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), content, note),
        )
        conn.commit()
        return cur.lastrowid


def list_profile_versions() -> list[dict]:
    """최신순 (id, created_at, note) 목록 — 표시용, 무거운 content는 제외."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, created_at, note FROM profile_versions ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_profile_version(version_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, created_at, content, note FROM profile_versions WHERE id = ?",
            (version_id,),
        ).fetchone()
        return dict(row) if row else None


def update_profile_version_note(version_id: int, note: str | None) -> bool:
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE profile_versions SET note = ? WHERE id = ?", (note, version_id)
        )
        conn.commit()
        return cur.rowcount > 0


def delete_profile_version(version_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM profile_versions WHERE id = ?", (version_id,))
        conn.commit()
        return cur.rowcount > 0


def latest_profile_version_id() -> int | None:
    """가장 최근 프로필 스냅샷 id. 평가 이력을 남길 때 "현재 프로필 버전"으로 참조한다."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM profile_versions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["id"] if row else None


def create_fit_history_entry(
    company_slug: str,
    profile_version_id: int | None,
    fit_score: int | None,
    fit_label: str | None,
    content: str,
) -> int:
    """평가 결과를 이력에 추가한다(덮어쓰기 아님) — 최초 평가도 포함해서 매번 호출."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO fit_history (company_slug, created_at, profile_version_id, "
            "fit_score, fit_label, content) VALUES (?, ?, ?, ?, ?, ?)",
            (
                company_slug,
                datetime.now().isoformat(timespec="seconds"),
                profile_version_id,
                fit_score,
                fit_label,
                content,
            ),
        )
        conn.commit()
        return cur.lastrowid


def get_fit_history_entry(entry_id: int) -> dict | None:
    """평가 이력 1건 전체(그 시점 회사 .md 원문 포함) — 상세보기용."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, company_slug, created_at, fit_score, fit_label, content "
            "FROM fit_history WHERE id = ?",
            (entry_id,),
        ).fetchone()
        return dict(row) if row else None


def list_fit_history(company_slug: str) -> list[dict]:
    """표시용 — content(무거운 원문)는 제외, 프로필 버전의 존재 여부까지 같이 반환."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT fh.id, fh.created_at, fh.fit_score, fh.fit_label,
                   fh.profile_version_id, pv.created_at AS profile_version_created_at
            FROM fit_history fh
            LEFT JOIN profile_versions pv ON pv.id = fh.profile_version_id
            WHERE fh.company_slug = ?
            ORDER BY fh.id DESC
            """,
            (company_slug,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_fit_history_for_slug(company_slug: str) -> int:
    """회사 삭제 시 그 slug의 평가 이력을 전부 지운다. slug는 회사명+직무명으로 결정적
    생성되므로, 이력을 안 지우면 같은 이름으로 재등록했을 때 예전(별개) 지원의 이력이
    새 회사에 다시 붙어버린다 — 반환값은 지워진 행 수."""
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM fit_history WHERE company_slug = ?", (company_slug,))
        conn.commit()
        return cur.rowcount


def migrate_qa_slug_history(device_id: str, company_slug: str, pairs: list[tuple[str, str]]) -> int:
    """이 기기(device_id)가 이 회사(company_slug)를 이미 마이그레이션했으면 0을 반환하고
    아무것도 안 한다. 아니면 pairs((질문,답변) 튜플 목록) 전부를 status='done'으로 삽입하고
    qa_migrations에 (device_id, company_slug) 기록까지 **한 트랜잭션**으로 커밋한 뒤 삽입
    건수를 반환한다.

    "슬러그에 메시지가 이미 있으면 스킵"(v1.5.1)이 아니라 "이 기기가 이 슬러그를 이미
    옮겼는지"로 판단해야 한다 — 안 그러면 기기 A가 먼저 마이그레이션한 회사는 기기 B의
    (서로 다른) 이력이 영영 안 옮겨진다. 메시지 삽입과 마이그레이션 기록을 분리된 커밋으로
    하면, 중간에 실패했을 때 "메시지는 없는데 기록은 남아 영구 스킵"되거나 반대로 "재시도
    때마다 중복 삽입"될 수 있어 하나의 트랜잭션으로 묶는다(docs/chat-history-server-storage/
    PLAN.md 참고).

    쌍(question, answer)의 occurrence 개수 기준으로 서버에 이미 있던 만큼만 건너뛴다 —
    v1.5.1 당시(기기 추적 테이블이 없던 시절) 이미 성공적으로 옮겨진 기기가 복구 경로로
    재호출해도 중복 삽입되지 않도록 하기 위함. 존재 여부를 boolean으로만 보면 완전히 같은
    질문을 두 번 물어본 정상 이력조차 마이그레이션 중 하나로 뭉개진다 — 이번 호출에서 새로
    넣은 행을 다음 쌍의 "이미 있음" 근거로 다시 세면 안 되므로, 시작 시점의 기존 개수만
    한 번씩 소비한다.

    함수 맨 앞에서 BEGIN IMMEDIATE로 쓰기 트랜잭션을 먼저 확보한다 — 안 그러면 서로 다른
    두 기기가 동시에 같은 슬러그를 복구할 때 둘 다 같은(비어있는) occurrence 스냅샷을
    읽어서 순차 실행이었다면 스킵됐을 턴을 양쪽 다 삽입해버린다."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        already = conn.execute(
            "SELECT 1 FROM qa_migrations WHERE device_id = ? AND company_slug = ?",
            (device_id, company_slug),
        ).fetchone()
        if already:
            conn.rollback()
            return 0
        now = datetime.now().isoformat(timespec="seconds")
        existing_rows = conn.execute(
            "SELECT question, answer FROM qa_messages WHERE company_slug = ?",
            (company_slug,),
        ).fetchall()
        remaining = Counter((row["question"], row["answer"]) for row in existing_rows)
        inserted = 0
        for question, answer in pairs:
            key = (question, answer)
            if remaining[key] > 0:
                remaining[key] -= 1
                continue
            conn.execute(
                "INSERT INTO qa_messages (company_slug, question, answer, status, created_at) "
                "VALUES (?, ?, ?, 'done', ?)",
                (company_slug, question, answer, now),
            )
            inserted += 1
        conn.execute(
            "INSERT INTO qa_migrations (device_id, company_slug, migrated_at) VALUES (?, ?, ?)",
            (device_id, company_slug, now),
        )
        conn.commit()
        return inserted


def insert_pending_qa(company_slug: str, question: str) -> int:
    """질문을 status='pending'으로 즉시 저장하고 id(=message_id)를 반환한다. 응답이 오기
    전에 먼저 호출 — 클라이언트가 화면을 나가도 이 행이 "지금 대기 중"이라는 증거로 남는다."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO qa_messages (company_slug, question, status, created_at) "
            "VALUES (?, ?, 'pending', ?)",
            (company_slug, question, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return cur.lastrowid


def mark_qa_done(message_id: int, answer: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE qa_messages SET status = 'done', answer = ? WHERE id = ?",
            (answer, message_id),
        )
        conn.commit()


def mark_qa_failed(message_id: int, error: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE qa_messages SET status = 'failed', error = ? WHERE id = ?",
            (error, message_id),
        )
        conn.commit()


def list_qa_history(company_slug: str) -> list[dict]:
    """이 회사의 QnA 메시지 전체(pending/failed 포함) — 오래된 순, 페이지 로드 시 채팅
    화면을 그대로 복원하는 용도."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, question, answer, status, error, created_at FROM qa_messages "
            "WHERE company_slug = ? ORDER BY id ASC",
            (company_slug,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_qa_context(company_slug: str, limit: int = 20) -> list[dict]:
    """LLM 컨텍스트 조립용 — status='done'인 것만, 최근 limit턴을 오래된 순으로 반환.
    기존 프론트가 유지하던 분량(메시지 40개=20턴, frontend/app.js의 .slice(-40))과
    동일하게 맞추려면 limit=20이어야 한다(한 행에 질문+답변이 같이 들어있으므로)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT question, answer FROM qa_messages "
            "WHERE company_slug = ? AND status = 'done' ORDER BY id DESC LIMIT ?",
            (company_slug, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def delete_qa_history_for_slug(company_slug: str) -> int:
    """회사 삭제 시 그 slug의 QnA 대화 기록을 전부 지운다. delete_fit_history_for_slug()와
    같은 이유 — slug는 회사명+직무명으로 결정적 생성되므로, 안 지우면 같은 이름으로
    재등록했을 때 예전(별개) 지원의 QnA 대화가 새 회사에 다시 붙어버린다. 반환값은
    지워진 행 수."""
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM qa_messages WHERE company_slug = ?", (company_slug,))
        conn.commit()
        return cur.rowcount


def migrate_rag_chat(chat_id: str, title: str | None, created_at: str, entries: list[tuple[str, str]]) -> int:
    """chat_id 방이 이미 있으면 0을 반환하고 아무것도 안 한다(재이관 방지 — RAG 방 id는
    기기별로 이미 고유하게 생성되므로 QnA처럼 기기 단위 구분이 따로 필요 없다). 아니면
    방 생성과 메시지(entries: (질문, data JSON 문자열) 튜플 목록) 전부 삽입을 **한
    트랜잭션**으로 커밋하고 삽입 건수를 반환한다.

    예전엔 방 생성(create_rag_chat)과 메시지 삽입(insert_pending_rag_message+
    mark_rag_message_done)이 각각 별도 커밋이라, 방만 만들어진 직후나 메시지 일부만 들어간
    뒤 서버가 죽으면 재시도해도 "이미 있는 방"으로 판정돼 나머지가 영구 누락됐다. 한
    트랜잭션으로 묶으면 중간에 실패해도 전부 롤백되어 재시도 시 처음부터 다시 시도할 수 있다.

    방 생성은 SELECT로 먼저 존재를 확인하지 않고 INSERT ... ON CONFLICT(id) DO NOTHING으로
    원자적으로 처리한다 — 두 기기가 같은 chat_id를 동시에 이관하면 SELECT-후-INSERT는 둘 다
    "없음"을 보고 진행해 한쪽이 기본키 충돌(IntegrityError)로 500이 난다. rowcount==0이면
    이미 다른 쪽이 방을 만든 것이므로 멱등하게 0을 반환한다."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO rag_chats (id, title, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO NOTHING",
            (chat_id, title, created_at),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return 0
        for question, data_json in entries:
            conn.execute(
                "INSERT INTO rag_messages (chat_id, question, data, status, created_at) "
                "VALUES (?, ?, ?, 'done', ?)",
                (chat_id, question, data_json, created_at),
            )
        conn.commit()
        return len(entries)


def create_rag_chat(chat_id: str, title: str | None = None, created_at: str | None = None) -> None:
    """새 채팅방 생성. `created_at`을 지정하면(마이그레이션으로 넘어온 옛 createdAt 보존)
    그 값을, 없으면 지금 시각을 쓴다."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO rag_chats (id, title, created_at) VALUES (?, ?, ?)",
            (chat_id, title, created_at or datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()


def list_rag_chats() -> list[dict]:
    """채팅방 목록 — 최신순(드롭다운용)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at FROM rag_chats ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_rag_chat(chat_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, title, created_at FROM rag_chats WHERE id = ?", (chat_id,)
        ).fetchone()
        return dict(row) if row else None


def set_rag_chat_title_if_empty(chat_id: str, title: str) -> None:
    """제목이 아직 없는 채팅방에만 첫 질문 앞부분으로 제목을 채운다(기존 프론트
    `frontend/app.js`의 `if (!chat.title) chat.title = question.slice(0, 24) + ...`와
    동일 규칙, 서버로 이전)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE rag_chats SET title = ? WHERE id = ? AND (title IS NULL OR title = '')",
            (title, chat_id),
        )
        conn.commit()


def delete_rag_chat(chat_id: str) -> bool:
    """채팅방 삭제 — 소속 `rag_messages`는 FK(`ON DELETE CASCADE`)로 DB가 자동 정리한다."""
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM rag_chats WHERE id = ?", (chat_id,))
        conn.commit()
        return cur.rowcount > 0


def insert_pending_rag_message(chat_id: str, question: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO rag_messages (chat_id, question, status, created_at) "
            "VALUES (?, ?, 'pending', ?)",
            (chat_id, question, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return cur.lastrowid


def mark_rag_message_done(message_id: int, data_json: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE rag_messages SET status = 'done', data = ? WHERE id = ?",
            (data_json, message_id),
        )
        conn.commit()


def mark_rag_message_failed(message_id: int, error: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE rag_messages SET status = 'failed', error = ? WHERE id = ?",
            (error, message_id),
        )
        conn.commit()


def list_rag_messages(chat_id: str) -> list[dict]:
    """이 채팅방의 메시지 전체(pending/failed 포함) — 오래된 순."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, question, data, status, error, created_at FROM rag_messages "
            "WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_rag_context(chat_id: str, limit: int = 20) -> list[dict]:
    """LLM 컨텍스트 조립용 — `list_qa_context()`와 동일한 이유로 limit=20(20턴=40메시지,
    기존 프론트 `.slice(-40)`과 같은 분량)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT question, data FROM rag_messages "
            "WHERE chat_id = ? AND status = 'done' ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
