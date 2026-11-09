"""추출 스키마·본문 템플릿·모델·CSV에서 50명 이상 여부·웹사이트·업종을 뺀 것 점검.

LLM 호출 없음. 임시 데이터 폴더에서만 돌아 실제 data/는 건드리지 않는다.
옛 공고 마크다운(세 키가 든 파일)이 계속 읽히고, 다시 저장하면 그 키가 파일에서 사라지는지도 본다.

실행: backend/에서 `python3 -m scripts.check_extract_schema`
"""
import asyncio
import csv
import io
import pathlib
import tempfile

import frontmatter

import prompts
import storage
from config import settings
from models import CompanyFrontmatter

REMOVED = ("employee_count_meets_threshold", "website", "industry")
KEPT_COMPANY_ITEMS = ("employee_count", "stability", "investment_stage", "funding_total", "revenue_status")


async def _csv_text(resp) -> str:
    chunks = [c.decode("utf-8") if isinstance(c, bytes) else c async for c in resp.body_iterator]
    return "".join(chunks)


async def main():
    from routers import companies

    # 1. 추출 스키마: 세 필드만 빠지고 나머지 회사 항목은 그대로, 필수 목록은 스키마 안에 있는 필드만
    schema = prompts.EXTRACT_COMPANY_TOOL_SCHEMA
    props = set(schema["properties"])
    assert not props & set(REMOVED), props & set(REMOVED)
    assert set(KEPT_COMPANY_ITEMS) <= props, set(KEPT_COMPANY_ITEMS) - props
    assert set(schema["required"]) <= props
    print("1. 추출 스키마: 50명 이상 여부·웹사이트·업종이 빠지고 임직원 수·안정성·투자 단계·누적 투자금·매출 현황은 그대로")

    # 2. 본문 생성 템플릿: "분야" 줄이 없고 자리표시자가 맞아 .format()이 그대로 돌아간다
    template = prompts.GENERATE_BODY_USER_TEMPLATE
    assert "분야" not in template and "industry" not in template
    template.format(company_json="{}", raw_text="원문")
    print("2. 본문 템플릿: \"분야\" 줄이 없고 .format()이 오류 없이 동작")

    # 3. 모델: 세 필드가 없다
    assert not set(CompanyFrontmatter.model_fields) & set(REMOVED)
    print("3. 모델: CompanyFrontmatter에 세 필드가 없음")

    # 4. 옛 공고(세 키가 든 마크다운): 읽히고, CSV 내보내기가 되고, 다시 저장하면 세 키가 파일에서 사라진다
    slug = "시험회사__시험직무"
    meta = {"company_name": "시험회사", "display_name": "시험", "job_title": "시험직무",
            "employee_count": "160명 (국민연금 기준)", "industry": "B2B SaaS",
            "website": "https://example.com", "employee_count_meets_threshold": True}
    path = settings.companies_dir / f"{slug}.md"
    path.write_text(frontmatter.dumps(frontmatter.Post("본문", **meta)), encoding="utf-8")
    storage.invalidate_companies_cache()
    rec = storage.read_company(slug)
    assert rec is not None and rec.frontmatter.employee_count == "160명 (국민연금 기준)"
    assert not any(hasattr(rec.frontmatter, k) for k in REMOVED)

    rows = list(csv.reader(io.StringIO((await _csv_text(await companies.export_companies_csv())).lstrip("﻿"))))
    assert "industry" not in rows[0] and len(rows) == 2, rows[0]
    assert rows[1][rows[0].index("employee_count")] == "160명 (국민연금 기준)"

    storage.write_company(slug, rec.frontmatter, rec.body)
    assert not set(frontmatter.load(path).metadata) & set(REMOVED)
    print("4. 옛 공고: 세 키가 든 마크다운이 읽히고 CSV 내보내기에 업종 컬럼이 없으며, 다시 저장하면 세 키가 파일에서 사라짐")


with tempfile.TemporaryDirectory() as tmp:
    settings.data_dir = pathlib.Path(tmp)
    settings.companies_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(main())

print("추출 스키마 정리 self-check 통과")
