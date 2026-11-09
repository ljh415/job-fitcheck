"""routers/companies.py의 회사 정보(company_info) 저장·삭제 연결 점검.

수집(`scraper.fetch_url`)은 실제 원티드·리멤버 페이지로 하고, 추출·본문 생성·잡플래닛·
알림·RAG 재색인은 가짜로 대신한다(LLM 호출 없음, 비용 없음). 임시 데이터 폴더·임시 DB에서만
돌아 실제 data/는 건드리지 않는다. 아래 두 공고 주소가 사라지면 다른 공고 주소로 바꾼다.

실행: backend/에서 `python3 -m scripts.check_company_info`
"""
import asyncio
import json
import pathlib
import tempfile

from config import settings
from services import app_db
from services.app_db import get_connection, init_db
from services.jobplanet import JobplanetResult

WANTED_URL = "https://www.wanted.co.kr/wd/395754"
REMEMBER_URL = "https://career.rememberapp.co.kr/job/posting/328761"
EVAL_COLUMNS = ("employee_count", "revenue_won", "revenue_as_of", "listing_status", "funding_stage", "funding_total")
WANTED_QUERIES = {"companyInfo", "companySummary", "companyHire", "companyBanner"}

_fake_name = {"value": ""}  # 시나리오마다 추출 결과의 회사명을 바꿔 slug가 겹치지 않게 한다


class FakeLLM:
    async def extract_structured(self, **kwargs):
        return {"company_name": _fake_name["value"], "job_title": "시험직무", "display_name": _fake_name["value"]}

    async def complete(self, **kwargs):
        return "시험 본문"


async def _fake_jobplanet(name):
    return JobplanetResult(None, None, "not_found")


async def _noop_notify(materials):
    return None


def _rows():
    with get_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM company_info ORDER BY id")]


def _versions(slug):
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM posting_raw_versions WHERE slug = ? ORDER BY id", (slug,))]


async def main():
    from llm.router import LLMSnapshot
    from routers import companies
    import storage

    companies.capture_snapshot = lambda: LLMSnapshot("fake", "fake-light", "fake-high", "low")
    companies.high_from_snapshot = lambda snap: (FakeLLM(), "fake-high")
    companies.light_from_snapshot = lambda snap: (FakeLLM(), "fake-light")
    companies.fetch_jobplanet_score = _fake_jobplanet
    companies.send_notification = _noop_notify
    companies.trigger_reindex_background = lambda: None

    # 1. 원티드 공고 등록 → 행 1개, 기업 페이지 원본 보관, 공고 payload 없음, 평가용 컬럼 비어 있음
    _fake_name["value"] = "시험원티드"
    rec = await companies.add_from_url(companies.FromUrlRequest(url=WANTED_URL))
    wanted_slug = rec.slug
    rows = _rows()
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["source"] == "wanted"
    assert row["source_url"] == f"https://www.wanted.co.kr/company/{row['source_company_id']}"
    assert set(json.loads(row["company_payload"])) == WANTED_QUERIES
    assert row["posting_company_payload"] is None
    summary = json.loads(row["company_payload"])["companySummary"]
    assert row["employee_count"] == f"{summary['employee']['total']:,}명 (국민연금 기준)", row
    assert row["revenue_won"] == summary["sales"]["total"], row
    assert row["revenue_as_of"] == summary["sales"]["updatedAt"], row
    wanted_name = json.loads(row["company_payload"])["companyInfo"]["name"]
    assert wanted_name and row["company_name"] == wanted_name, row
    assert all(row[c] is None for c in ("funding_stage", "funding_total")), row
    assert row["collected_at"]
    print("1. 원티드 등록: 행 1개, 기업 payload 4항목, 공고 payload 없음, 임직원·매출·기준일이 원본과 일치")

    # 2. 리멤버 공고 등록 → 공고 페이지 회사 부분까지 보관
    _fake_name["value"] = "시험리멤버"
    rec = await companies.add_from_url(companies.FromUrlRequest(url=REMEMBER_URL))
    remember_slug = rec.slug
    rows = _rows()
    assert len(rows) == 2, rows  # 원티드 1 + 리멤버 1
    row = rows[1]
    assert row["source"] == "remember"
    assert row["source_url"] == f"https://career.rememberapp.co.kr/job/company/{row['source_company_id']}"
    assert "employeeStatistics" in json.loads(row["company_payload"])
    assert set(json.loads(row["posting_company_payload"])) == {"chips", "company", "organization"}
    latest = next(s for s in json.loads(row["company_payload"])["employeeStatistics"] if s.get("total"))
    assert row["employee_count"] == f"{latest['total']:,}명 ({latest['month']} 기준)", row
    remember_name = json.loads(row["company_payload"])["name"]
    assert remember_name and row["company_name"] == remember_name, row
    chips = {c["value"] for c in json.loads(row["posting_company_payload"])["chips"]}
    assert row["funding_stage"] in chips and f"누적 투자 {row['funding_total']}" in chips, row
    assert all(row[c] is None for c in ("revenue_won", "revenue_as_of", "listing_status")), row
    print("2. 리멤버 등록: 기업 payload(employeeStatistics 포함)와 공고 payload 3항목 보관, 임직원 수·투자 단계·누적 투자금이 원본과 일치")

    # 3. 텍스트 등록(기업 페이지 없음) → 공고 파일은 생기고 행은 안 생김
    _fake_name["value"] = "시험텍스트"
    rows_before = len(_rows())
    rec = await companies._process_company("붙여넣은 공고 본문", "text_paste", None)
    assert storage.read_company(rec.slug) is not None
    assert len(_rows()) == rows_before
    print("3. 텍스트 등록: 공고 파일 생성, 회사 정보 행 없음")

    # 4. 같은 회사를 다시 저장 → 줄 1개·id 유지, 새 값으로 덮어씀. 같은 회사의 다른 공고(다른 slug)도 그 줄을 쓴다
    changed = {"source": "wanted", "source_company_id": "1", "source_url": "https://x/company/1",
               "company_payload": {"changed": True}, "posting_company_payload": None}
    _fake_name["value"] = "시험공유A"
    await companies._process_company("본문", "url", WANTED_URL, company_info=changed)
    count_after_first = len(_rows())
    first = next(r for r in _rows() if (r["source"], r["source_company_id"]) == ("wanted", "1"))
    _fake_name["value"] = "시험공유B"  # 다른 공고(다른 slug), 같은 회사
    await companies._process_company(
        "본문", "url", WANTED_URL, company_info=dict(changed, company_payload={"changed": "again"}),
    )
    same = [r for r in _rows() if (r["source"], r["source_company_id"]) == ("wanted", "1")]
    assert len(same) == 1 and same[0]["id"] == first["id"], same
    assert json.loads(same[0]["company_payload"]) == {"changed": "again"}, same
    assert len(_rows()) == count_after_first
    print("4. 같은 회사 재저장: 줄 1개·id 유지, 새 값으로 덮어씀, 같은 회사의 다른 공고도 같은 줄 사용")

    # 5. DB 저장 실패 → 등록은 성공(공고 파일 저장), 행은 없음
    def boom(*args, **kwargs):
        raise RuntimeError("저장 실패 흉내")
    original = companies.upsert_company_info
    companies.upsert_company_info = boom
    rows_before = len(_rows())
    try:
        _fake_name["value"] = "시험실패"
        rec = await companies._process_company("본문", "url", WANTED_URL, company_info=changed)
    finally:
        companies.upsert_company_info = original
    assert storage.read_company(rec.slug) is not None
    assert len(_rows()) == rows_before
    print("5. 저장 실패: 등록은 성공(공고 파일 있음), 행 없음")

    # 6. 공고 삭제 → 공고 파일과 그 공고의 원문 버전 행이 삭제되고, 아무도 안 가리키는 회사 정보 행도
    #    삭제된다. 다른 회사(리멤버)의 행은 그대로
    before = _rows()
    wanted_company = before[0]
    assert wanted_company["source"] == "wanted" and _versions(wanted_slug), (wanted_company, _versions(wanted_slug))
    assert await companies.delete_company(wanted_slug) == {"status": "deleted"}
    assert storage.read_company(wanted_slug) is None
    assert _versions(wanted_slug) == []
    after = _rows()
    assert wanted_company["id"] not in [r["id"] for r in after] and len(after) == len(before) - 1, after
    assert any(r["source"] == "remember" for r in after)
    print("6. 삭제: 공고 파일·원문 버전 행·아무도 안 쓰는 회사 정보 행이 삭제되고 다른 회사 행은 그대로")

    # 7. 기업 페이지 수집만 실패 → 등록은 성공, 행 없음, 서버 로그에 회사 번호와 사유가 남음
    import logging
    import httpx
    from services import scraper

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Capture()
    logging.getLogger("services.scraper").addHandler(handler)
    original_get = scraper._safe_get

    def fail_company_page(error):
        async def fake_get(client, url, **kwargs):
            if "/company/" in url:
                raise error
            return await original_get(client, url, **kwargs)
        return fake_get

    try:
        cases = [
            (WANTED_URL, "시험실패원티드", httpx.ConnectError("연결 흉내"), logging.WARNING, "원티드 기업 페이지 수집 실패", "ConnectError"),
            (REMEMBER_URL, "시험실패리멤버", httpx.ConnectError("연결 흉내"), logging.WARNING, "리멤버 기업 페이지 수집 실패", "ConnectError"),
            (WANTED_URL, "시험예상밖", RuntimeError("코드 결함 흉내"), logging.ERROR, "원티드 기업 페이지 수집 중 예상 못 한 오류", "RuntimeError"),
        ]
        for url, name, error, level, message, detail in cases:
            records.clear()
            scraper._safe_get = fail_company_page(error)
            _fake_name["value"] = name
            rows_before = len(_rows())
            rec = await companies.add_from_url(companies.FromUrlRequest(url=f"{url}?case={name}"))  # 같은 주소 중복 등록(409) 회피
            assert storage.read_company(rec.slug) is not None
            assert len(_rows()) == rows_before
            hit = [r for r in records if message in r.getMessage() and r.levelno == level and "company_id=" in r.getMessage()]
            assert hit, [r.getMessage() for r in records]
            if level == logging.ERROR:
                assert hit[0].exc_info and detail in repr(hit[0].exc_info[1].__class__), hit[0].exc_info
            else:
                assert detail in hit[0].getMessage(), hit[0].getMessage()
    finally:
        scraper._safe_get = original_get
        logging.getLogger("services.scraper").removeHandler(handler)
    print("7. 기업 페이지 수집 실패: 등록 성공, 행 없음, 접속 실패는 경고·예상 밖 오류는 호출 경로 포함 오류로 회사 번호와 함께 기록")

    # 8. 내부 주소 접근 → 422로 거부, 공고 파일·행 없음, 경고 수준 로그
    from fastapi import HTTPException

    router_records = []

    class RouterCapture(logging.Handler):
        def emit(self, record):
            router_records.append(record)

    router_handler = RouterCapture()
    logging.getLogger("routers.companies").addHandler(router_handler)
    try:
        for blocked in ("http://127.0.0.1:8000/", "http://192.168.0.1/admin"):
            try:
                await companies.add_from_url(companies.FromUrlRequest(url=blocked))
                raise AssertionError("내부 주소가 거부되지 않음")
            except HTTPException as e:
                assert e.status_code == 422, e.status_code
        warned = [r for r in router_records if r.levelno == logging.WARNING and "내부 주소 접근 차단" in r.getMessage()]
        assert len(warned) == 2, [r.getMessage() for r in router_records]
    finally:
        logging.getLogger("routers.companies").removeHandler(router_handler)
    print("8. 내부 주소 접근: 두 주소 모두 422로 거부, 경고 로그 2건")

    # 9. 원문 버전: 등록하면 행 1개, 같은 원문이면 그대로, 바뀌면 새 행(기존 행 유지), 회사 정보가 없거나
    #    저장에 실패하면 company_info_id는 NULL, 원문 버전 저장이 실패해도 등록은 성공
    import hashlib

    def sha(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    info_a = {"source": "wanted", "source_company_id": "9001", "source_url": "https://x/company/9001",
              "company_payload": {"v": 1}, "posting_company_payload": None}
    _fake_name["value"] = "시험버전A"
    rec = await companies._process_company("원문 1판", "url", WANTED_URL, company_info=info_a)
    slug_a = rec.slug
    company_9001 = next(r for r in _rows() if r["source_company_id"] == "9001")
    v1 = _versions(slug_a)
    assert len(v1) == 1 and v1[0]["content_hash"] == sha("원문 1판") and v1[0]["raw_text"] == "원문 1판", v1
    assert v1[0]["company_info_id"] == company_9001["id"], v1

    await companies._process_company("원문 1판", "url", WANTED_URL, existing_slug=slug_a, company_info=info_a)
    assert [v["id"] for v in _versions(slug_a)] == [v1[0]["id"]]  # 같은 원문 → 행 추가 없음

    await companies._process_company("원문 2판", "url", WANTED_URL, existing_slug=slug_a, company_info=info_a)
    v2 = _versions(slug_a)
    assert len(v2) == 2 and v2[0]["id"] == v1[0]["id"] and v2[1]["content_hash"] == sha("원문 2판"), v2
    assert v2[1]["company_info_id"] == company_9001["id"], v2  # 회사 정보 행은 제자리 갱신이라 id 유지

    _fake_name["value"] = "시험버전텍스트"
    rec = await companies._process_company("텍스트 원문", "text_paste", None)
    vt = _versions(rec.slug)
    assert len(vt) == 1 and vt[0]["company_info_id"] is None, vt

    companies.upsert_company_info = boom
    try:
        _fake_name["value"] = "시험버전저장실패"
        rec = await companies._process_company("저장 실패 원문", "url", WANTED_URL, company_info=info_a)
    finally:
        companies.upsert_company_info = original
    vf = _versions(rec.slug)
    assert storage.read_company(rec.slug) is not None
    assert len(vf) == 1 and vf[0]["company_info_id"] is None, vf

    original_record = companies.record_raw_version
    companies.record_raw_version = boom
    try:
        _fake_name["value"] = "시험버전기록실패"
        rec = await companies._process_company("기록 실패 원문", "url", WANTED_URL, company_info=info_a)
    finally:
        companies.record_raw_version = original_record
    assert storage.read_company(rec.slug) is not None and _versions(rec.slug) == []
    print("9. 원문 버전: 등록 시 행 1개(해시·회사 정보 연결 일치), 같은 원문은 행 추가 없음, 바뀌면 새 행, 회사 정보 없음·저장 실패는 NULL, 원문 버전 저장이 실패해도 등록 성공")

    # 10. 같은 회사의 공고 2건 → 회사 정보 행을 공유, 하나를 지우면 행 유지, 마지막 공고를 지우면 함께 삭제
    _fake_name["value"] = "시험버전B"
    rec_b = await companies._process_company("원문 B", "url", WANTED_URL, company_info=info_a)
    slug_b = rec_b.slug
    shared = {_versions(s)[-1]["company_info_id"] for s in (slug_a, slug_b)}
    assert len(shared) == 1 and None not in shared, shared
    company_id = shared.pop()
    await companies.delete_company(slug_a)
    assert _versions(slug_a) == [] and _versions(slug_b)
    assert any(r["id"] == company_id for r in _rows())
    await companies.delete_company(slug_b)
    assert _versions(slug_b) == []
    assert not any(r["id"] == company_id for r in _rows())
    print("10. 같은 회사의 공고 2건: 회사 정보 행을 공유하고, 하나를 지워도 행이 남고 마지막 공고를 지우면 함께 삭제")

    # 11. 평가 이력이 평가에 쓴 원문 버전을 가리킨다(평가 호출은 가짜)
    async def fake_eval(snap, profile_text, company_data, raw_text, operation=""):
        return {"fit_score": 77, "fit_label": "추천"}, "시험 리포트"

    def fit_rows(slug):
        with get_connection() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT id, raw_version_id FROM fit_history WHERE company_slug = ? ORDER BY id", (slug,))]

    orig_eval = companies.company_analysis.evaluate_fit_structured
    orig_exists, orig_read = storage.profile_exists, storage.read_profile_text
    companies.company_analysis.evaluate_fit_structured = fake_eval
    storage.profile_exists = lambda: True
    storage.read_profile_text = lambda: "시험 프로필"
    try:
        _fake_name["value"] = "시험평가"
        rec = await companies._process_company("평가 원문 1판", "url", WANTED_URL, company_info=info_a)
        slug_e = rec.slug
        ver = _versions(slug_e)
        fits = fit_rows(slug_e)
        assert len(ver) == 1 and len(fits) == 1 and fits[0]["raw_version_id"] == ver[0]["id"], (ver, fits)

        await companies.refit_company(slug_e)  # 같은 원문 재평가
        fits = fit_rows(slug_e)
        assert len(_versions(slug_e)) == 1 and len(fits) == 2, fits
        assert fits[1]["raw_version_id"] == ver[0]["id"], fits

        storage.write_raw_text(slug_e, "평가 원문 2판")  # 원문이 바뀐 뒤 재평가
        await companies.refit_company(slug_e)
        ver = _versions(slug_e)
        fits = fit_rows(slug_e)
        assert len(ver) == 2 and len(fits) == 3, (ver, fits)
        assert fits[2]["raw_version_id"] == ver[1]["id"] and fits[0]["raw_version_id"] == ver[0]["id"], fits
        assert ver[1]["company_info_id"] == ver[0]["company_info_id"] and ver[0]["company_info_id"] is not None, ver

        with get_connection() as conn:  # 원문 버전 행이 없던 공고(이 기능 전에 등록된 공고)
            conn.execute("DELETE FROM posting_raw_versions WHERE slug = ?", (slug_e,))
            conn.commit()
        await companies.refit_company(slug_e)
        ver = _versions(slug_e)
        fits = fit_rows(slug_e)
        assert len(ver) == 1 and ver[0]["company_info_id"] is None, ver
        assert fits[3]["raw_version_id"] == ver[0]["id"], fits

        companies.record_raw_version = boom  # 원문 버전 기록이 실패해도 평가 이력은 남고 원문 버전 id만 비움
        try:
            await companies.refit_company(slug_e)
        finally:
            companies.record_raw_version = original_record
        fits = fit_rows(slug_e)
        assert len(fits) == 5 and fits[4]["raw_version_id"] is None, fits
    finally:
        companies.company_analysis.evaluate_fit_structured = orig_eval
        storage.profile_exists, storage.read_profile_text = orig_exists, orig_read
    print("11. 평가 이력의 원문 버전: 신규 평가·같은 원문 재평가·원문이 바뀐 뒤 재평가·버전 행이 없던 공고가 각각 맞는 버전을 가리키고, 기록 실패 시 NULL")

    # 12. 재분석(refill): URL로 등록한 공고는 사이트에서 다시 받아 원문 버전·회사 정보를 갱신하고,
    #     받기 실패 시 기존 값 보존, URL 등록이 아닌 공고는 사이트를 부르지 않음 (스크래핑은 가짜)
    import httpx
    from fastapi import HTTPException
    from services import scraper

    def fake_company(**over):
        base = {"source": "wanted", "source_company_id": "9100", "source_url": "https://x/company/9100",
                "company_payload": {"v": 1}, "posting_company_payload": None,
                "employee_count": "112명 (국민연금 기준)", "revenue_won": 1000, "revenue_as_of": "2024-12-31",
                "listing_status": None, "funding_stage": None, "funding_total": None}
        base.update(over)
        return base

    state = {"result": None, "error": None, "calls": 0}

    async def fake_fetch_url(url):
        state["calls"] += 1
        if state["error"]:
            raise state["error"]
        return state["result"]

    orig_fetch = scraper.fetch_url
    scraper.fetch_url = fake_fetch_url
    try:
        # a) 등록 → 원문 버전 1개
        _fake_name["value"] = "시험재분석"
        state["result"] = ("옛 원문 + 기업 정보 블록", fake_company())
        rec = await companies.add_from_url(companies.FromUrlRequest(url=WANTED_URL + "?r=1"))
        slug_r = rec.slug
        assert len(_versions(slug_r)) == 1
        company_before = next(r for r in _rows() if r["source_company_id"] == "9100")

        # b) 사이트의 공고가 바뀐 뒤 재분석 → 원문 파일 교체, 새 원문 버전 행, 회사 정보 행은 id 유지하고 덮어씀
        state["result"] = ("새 원문 + 기업 정보 블록", fake_company(company_payload={"v": 2}))
        await companies.refill_company(slug_r)
        ver = _versions(slug_r)
        company_after = next(r for r in _rows() if r["source_company_id"] == "9100")
        assert storage.read_raw_text(slug_r) == "새 원문 + 기업 정보 블록", storage.read_raw_text(slug_r)
        assert len(ver) == 2 and ver[1]["raw_text"] == "새 원문 + 기업 정보 블록", ver
        assert ver[1]["company_info_id"] == company_after["id"] == company_before["id"], (ver, company_before, company_after)
        assert json.loads(company_after["company_payload"]) == {"v": 2}, company_after

        # c) 내용이 같은 재분석 → 원문 버전 행 추가 없음
        await companies.refill_company(slug_r)
        assert len(_versions(slug_r)) == 2

        # d) 받기 실패 / e) 기업 페이지만 실패(이전에 회사 정보가 있던 공고) → 502, 아무것도 안 바뀜
        for error, result in ((httpx.ConnectError("연결 흉내"), None), (None, ("기업 블록 없는 원문", None))):
            state["error"], state["result"] = error, result
            try:
                await companies.refill_company(slug_r)
                raise AssertionError("실패해야 하는 재분석이 성공함")
            except HTTPException as e:
                assert e.status_code == 502, e.status_code
            assert storage.read_raw_text(slug_r) == "새 원문 + 기업 정보 블록" and len(_versions(slug_r)) == 2
        state["error"] = None

        # f) 텍스트 등록 공고 → 사이트를 부르지 않고 저장된 원문으로 재분석
        _fake_name["value"] = "시험재분석텍스트"
        rec = await companies._process_company("텍스트 원문", "text_paste", None)
        calls_before = state["calls"]
        await companies.refill_company(rec.slug)
        assert state["calls"] == calls_before and storage.read_raw_text(rec.slug) == "텍스트 원문"
    finally:
        scraper.fetch_url = orig_fetch
    print("12. 재분석: URL 등록 공고는 사이트에서 다시 받아 새 원문 버전·회사 정보(id 유지) 갱신, 같은 내용은 행 추가 없음, 받기 실패·기업 페이지 실패는 502로 기존 값 보존, 텍스트 등록 공고는 사이트를 부르지 않음")



def check_company_name_migration():
    """회사 이름 칸이 없던 옛 company_info 표도 init_db()가 칸을 추가한다."""
    import sqlite3
    with tempfile.TemporaryDirectory() as old_tmp:
        old_db = pathlib.Path(old_tmp) / "app.db"
        conn = sqlite3.connect(old_db)
        conn.execute("CREATE TABLE company_info (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, "
                     "source_company_id TEXT NOT NULL, source_url TEXT NOT NULL, collected_at TEXT NOT NULL, "
                     "UNIQUE (source, source_company_id))")
        conn.execute("INSERT INTO company_info VALUES (1, 'wanted', '1', 'u', 't')")
        conn.commit(); conn.close()
        saved = app_db.DB_PATH
        app_db.DB_PATH = old_db
        try:
            init_db()
            with get_connection() as c:
                cols = [r["name"] for r in c.execute("PRAGMA table_info(company_info)")]
                assert "company_name" in cols, cols
                assert c.execute("SELECT company_name FROM company_info WHERE id = 1").fetchone()["company_name"] is None
        finally:
            app_db.DB_PATH = saved
    print("13. 옛 표 이전: 회사 이름 칸이 없던 company_info 표에 init_db()가 칸을 추가하고 기존 행은 NULL로 유지")


with tempfile.TemporaryDirectory() as tmp:
    settings.data_dir = pathlib.Path(tmp)
    app_db.DB_PATH = settings.data_dir / "app.db"
    settings.companies_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    asyncio.run(main())
    check_company_name_migration()

print("company_info 연결 self-check 통과")
