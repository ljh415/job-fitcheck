"""화면·알림·응답에 쓰는 회사 이름이 company_name(사이트 이름)인지 점검.

브랜드명(display_name)이 다른 공고를 만들어 알림 재료·방치 공고 요약·타임라인 응답·중복 등록 409 응답·
질의응답 컨텍스트 제목이 모두 company_name을 쓰는지 본다. LLM 호출 없음, 임시 데이터 폴더·임시 DB에서만 동작.
mcp 패키지가 없어 mcp_server.py는 실행하지 못하므로 소스 글자를, 프론트는 소스 글자와 node 문법을 본다.
검색(app.js·mcp_server.py의 haystack)은 두 이름을 모두 쓰는 것이 맞아 그대로 남았는지도 본다.

실행: backend/에서 `python3 -m scripts.check_display_names`
"""
import asyncio
import pathlib
import shutil
import subprocess
import tempfile
from datetime import date, timedelta

from fastapi import HTTPException

import storage
from config import settings
from models import CompanyFrontmatter
from services import app_db
from services.app_db import init_db

ROOT = pathlib.Path(__file__).resolve().parents[2]
SLUG = "시험회사__시험직무"
SITE_NAME, BRAND = "사이트이름(Site)", "브랜드명"
URL = "https://example.com/job/1"


class FakeProvider:
    def __init__(self):
        self.messages = None

    def stream(self, **kwargs):
        self.messages = kwargs["messages"]

        async def gen():
            if False:
                yield ""
        return gen()


async def main():
    from routers import companies, qa

    old_day = (date.today() - timedelta(days=10)).isoformat()
    fm = CompanyFrontmatter(company_name=SITE_NAME, display_name=BRAND, job_title="시험직무", status="지원", source_url=URL)
    storage.write_company(SLUG, fm, f"본문\n\n## 6. 지원 상태 로그\n- {old_day}: 지원")

    # 1. 분석 완료 알림 재료
    assert companies.build_fit_notification_materials(fm)["company"] == SITE_NAME
    print("1. 알림 재료: 회사명이 company_name")

    # 2. 방치된 공고 요약
    neglected = companies._build_weekly_summary_materials()["neglected"]
    assert [n["name"] for n in neglected] == [SITE_NAME], neglected
    print("2. 방치 공고 요약: 이름이 company_name")

    # 3. 타임라인 응답(MCP 도구 get_application_timeline도 이 응답을 그대로 돌려준다)
    timeline = await companies.get_companies_timeline()
    assert len(timeline) == 1 and timeline[0]["company_name"] == SITE_NAME and "display_name" not in timeline[0], timeline
    print("3. 타임라인 응답: 키가 company_name이고 display_name 키는 없음")

    # 4. 이미 등록된 URL의 409 응답
    try:
        await companies.add_from_url(companies.FromUrlRequest(url=URL))
        raise AssertionError("409가 나와야 한다")
    except HTTPException as e:
        assert e.status_code == 409 and e.detail == {"slug": SLUG, "name": SITE_NAME}, (e.status_code, e.detail)
    print("4. 중복 URL 409: 이름이 company_name")

    # 5. 질의응답 컨텍스트 제목(AI에게 가는 입력)
    fake = FakeProvider()
    qa.high_provider = lambda: (fake, "fake-model")
    await qa.multi_company_qa(qa.MultiQARequest(slugs=[SLUG], question="질문"))
    sent = str(fake.messages)
    assert f"=== {SITE_NAME} ===" in sent and f"=== {BRAND} ===" not in sent
    print("5. 질의응답 컨텍스트: 제목 줄이 company_name")

    # 6. 소스 글자: MCP·프론트에서 바꾼 줄은 바뀌고, 검색의 두 이름 사용은 그대로
    mcp = (ROOT / "backend/mcp_server.py").read_text()
    app_js = (ROOT / "frontend/app.js").read_text()
    timeline_js = (ROOT / "frontend/timeline.js").read_text()
    assert "display_name or" not in mcp and "display_name or" not in app_js, "표시용 폴백이 남아 있음"
    assert "e.company.display_name" not in timeline_js and "e.company.company_name" in timeline_js
    assert "r.frontmatter.display_name" not in app_js and "r.frontmatter.company_name" in app_js
    assert "fm.display_name" in mcp and "fm.display_name" in app_js  # 검색은 두 이름 모두 대상
    print("6. 소스: MCP 응답·비교 표·타임라인은 company_name, 검색은 두 이름 그대로")

    # 7. 프론트 문법
    if shutil.which("node"):
        for f in ("app.js", "timeline.js"):
            r = subprocess.run(["node", "--check", str(ROOT / "frontend" / f)], capture_output=True, text=True)
            assert r.returncode == 0, (f, r.stderr[:300])
        print("7. 프론트 문법: node --check 통과")
    else:
        print("7. node가 없어 프론트 문법 점검은 건너뜀")


with tempfile.TemporaryDirectory() as tmp:
    settings.data_dir = pathlib.Path(tmp)
    app_db.DB_PATH = settings.data_dir / "app.db"
    settings.companies_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    asyncio.run(main())

print("회사 이름 표시 self-check 통과")
