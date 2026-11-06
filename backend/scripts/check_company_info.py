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

    # 6. 공고 삭제 → 공고 파일은 사라지고 회사 정보 줄은 그대로(회사 단위라 다른 공고가 쓸 수 있다.
    #    안 쓰는 줄의 정리는 원문 버전 표가 생긴 뒤 추가한다)
    rows_before = len(_rows())
    assert await companies.delete_company(wanted_slug) == {"status": "deleted"}
    assert storage.read_company(wanted_slug) is None
    assert len(_rows()) == rows_before
    print("6. 삭제: 공고 파일은 사라지고 회사 정보 줄은 그대로")

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


with tempfile.TemporaryDirectory() as tmp:
    settings.data_dir = pathlib.Path(tmp)
    app_db.DB_PATH = settings.data_dir / "app.db"
    settings.companies_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    asyncio.run(main())

print("company_info 연결 self-check 통과")
