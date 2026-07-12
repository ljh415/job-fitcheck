"""
URL 스크래핑 모듈.

지원 사이트:
- 원티드(wanted.co.kr): __NEXT_DATA__ JSON에서 직접 파싱 (JS 렌더링 불필요)
- 일반 사이트: BeautifulSoup으로 main/article 영역 추출

JS 렌더링이 필요한 사이트(잡코리아, 사람인 등)는 본문이 짧게 나오므로
ValueError를 raise해 프론트에서 텍스트 붙여넣기를 유도한다.
"""
import asyncio
import ipaddress
import json
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
}

# LLM 입력 토큰 절약을 위해 약 6,000 토큰 분량으로 제한
_MAX_TEXT_CHARS = 24_000

_ALLOWED_SCHEMES = {"http", "https"}
_MAX_REDIRECTS = 5
# 채용공고 페이지는 보통 수백 KB~2MB 수준. 그 대비 넉넉하게 잡아 비정상적으로
# 큰 응답(잘못된 URL, 대용량 파일 등)만 걸러내는 안전판.
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class SSRFBlockedError(ValueError):
    """사용자 입력 URL이 내부/사설 네트워크를 가리켜 요청을 차단했을 때 발생."""


def _is_blocked_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


async def _assert_public_url(url: str) -> None:
    """스킴·호스트·DNS 목적지를 검사해 내부/사설 네트워크로의 요청을 차단한다."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise SSRFBlockedError(f"허용되지 않은 URL 스킴입니다: {parsed.scheme}")
    if not parsed.hostname:
        raise SSRFBlockedError("URL에 호스트가 없습니다.")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, None)
    except OSError as e:
        raise SSRFBlockedError(f"호스트를 확인할 수 없습니다: {parsed.hostname}") from e
    for info in infos:
        ip_str = info[4][0]
        if _is_blocked_ip(ip_str):
            raise SSRFBlockedError(f"내부/사설 네트워크로의 요청은 차단됩니다: {parsed.hostname} → {ip_str}")


def is_wanted_host(url: str) -> bool:
    """호스트명이 정확히 wanted.co.kr 도메인인지 검사 (부분 문자열 검사 대체)."""
    host = (urlparse(url).hostname or "").lower()
    return host == "wanted.co.kr" or host.endswith(".wanted.co.kr")


async def _safe_get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """SSRF 방지: 요청 전 및 각 redirect마다 스킴/호스트/DNS 목적지를 재검사한다.
    응답은 스트리밍으로 받아 _MAX_RESPONSE_BYTES를 넘기면 즉시 중단한다
    (Content-Length가 없거나 거짓인 응답도 실제 수신 바이트 기준으로 막기 위함)."""
    current_url = url
    for _ in range(_MAX_REDIRECTS + 1):
        await _assert_public_url(current_url)
        async with client.stream("GET", current_url, follow_redirects=False, **kwargs) as response:
            if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
                current_url = urljoin(current_url, response.headers["location"])
                continue
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > _MAX_RESPONSE_BYTES:
                raise ValueError(f"응답 크기가 너무 큽니다 ({int(content_length):,} bytes).")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > _MAX_RESPONSE_BYTES:
                    raise ValueError(f"응답 크기가 제한({_MAX_RESPONSE_BYTES // (1024*1024)}MB)을 초과했습니다.")
            # aiter_bytes()는 이미 압축을 해제한 바이트를 반환하므로, 원본 헤더의
            # content-encoding/content-length를 그대로 넘기면 httpx.Response가 이미 풀린
            # 데이터를 다시 압축 해제하려다 실패한다 (DecodingError). 재구성 시 제거.
            headers = httpx.Headers(response.headers)
            headers.pop("content-encoding", None)
            headers.pop("content-length", None)
            return httpx.Response(
                status_code=response.status_code,
                headers=headers,
                content=bytes(body),
                request=response.request,
            )
    raise SSRFBlockedError("리다이렉트 횟수가 너무 많습니다.")


def _clean_rich_text(value: str) -> str:
    """리치텍스트 에디터로 작성된 JSON 필드(주요업무·자격요건 등)에 섞여 있는
    HTML 태그(<p>, <li>, <br> 등)를 제거하고 순수 텍스트만 남긴다."""
    if not value or "<" not in value:
        return value
    return BeautifulSoup(value, "lxml").get_text(separator="\n", strip=True)


async def fetch_url_text(url: str) -> str:
    """URL에서 채용공고 텍스트를 추출한다.

    Raises:
        ValueError: JS 렌더링이 필요해 스크래핑이 불가능한 사이트
        httpx.HTTPError: 네트워크 오류 또는 4xx/5xx 응답
    """
    async with httpx.AsyncClient(headers=_HEADERS, timeout=20) as client:
        response = await _safe_get(client, url)
        response.raise_for_status()
        html = response.text

        # 원티드는 SSR JSON에 구조화된 공고 데이터가 있어 파싱 품질이 높음
        if "wanted.co.kr" in url:
            text, company_id = _parse_wanted(html)
            if text:
                if company_id:
                    extra = await _fetch_wanted_company(client, company_id)
                    if extra:
                        text = text + "\n\n" + extra
                return text[:_MAX_TEXT_CHARS]

        # 리멤버 커리어도 __NEXT_DATA__ JSON에 구조화 데이터 포함
        if "rememberapp.co.kr" in url:
            text = _parse_remember(html)
            if text:
                return text[:_MAX_TEXT_CHARS]

    soup = BeautifulSoup(html, "lxml")
    text = _extract_main_content(soup)

    # 추출된 본문이 300자 미만이면 JS 렌더링 사이트로 판단
    if len(text) < 300:
        raise ValueError(
            "이 URL은 JavaScript 렌더링이 필요해 자동 스크래핑이 어렵습니다. "
            "'텍스트 붙여넣기' 탭에서 공고 내용을 복사해 입력해주세요."
        )

    return text[:_MAX_TEXT_CHARS]


def _parse_wanted(html: str) -> tuple[str | None, int | None]:
    """원티드 페이지의 __NEXT_DATA__ JSON에서 공고 정보를 추출.

    Wanted는 pageProps 구조가 바뀐 이력이 있음:
      구버전: pageProps.job.*
      현버전: pageProps.initialData.* (2025년 이후 확인)
    두 경로를 모두 시도한다.

    Returns:
        (공고 텍스트 | None, company_id | None)
    """
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not match:
        return None, None
    try:
        data = json.loads(match.group(1))
        page_props = data.get("props", {}).get("pageProps", {})

        # 현재 구조: pageProps.initialData
        job = page_props.get("initialData") or {}
        # 폴백: 구버전 구조 pageProps.job
        if not job:
            job = page_props.get("job") or {}
        if not job:
            return None, None

        # 현재 구조에서 회사 정보
        company = job.get("company", {})
        company_id = company.get("company_id")
        career = job.get("career", {})

        # 경력 범위 문자열 조합
        career_str = ""
        if career:
            if career.get("is_newbie"):
                career_str = "신입"
            elif career.get("is_expert"):
                career_str = "경력"
            else:
                a = career.get("annual_from")
                b = career.get("annual_to")
                if a and b:
                    career_str = f"{a}~{b}년"
                elif a:
                    career_str = f"{a}년 이상"

        employment_map = {"regular": "정규직", "contract": "계약직", "intern": "인턴"}
        employment = employment_map.get(job.get("employment_type", ""), job.get("employment_type", ""))

        parts = [
            f"회사명: {company.get('company_name', '')}",
            f"직무: {job.get('position', '')}",
            f"회사 소개: {_clean_rich_text(job.get('intro', '') or company.get('company_description', ''))}",
            f"주요업무: {_clean_rich_text(job.get('main_tasks', ''))}",
            f"자격요건: {_clean_rich_text(job.get('requirements', ''))}",
            f"우대사항: {_clean_rich_text(job.get('preferred_points', ''))}",
            f"혜택 및 복지: {_clean_rich_text(job.get('benefits', ''))}",
            f"채용 전형: {_clean_rich_text(job.get('hire_rounds', ''))}",
            f"근무지: {job.get('address', {}).get('full_location', '')}",
            f"경력: {career_str}",
            f"고용형태: {employment}",
            f"산업군: {company.get('industry_name', '')}",
        ]
        text = "\n".join(p for p in parts if p.split(": ", 1)[-1].strip())
        return text or None, company_id
    except (json.JSONDecodeError, KeyError):
        return None, None


def _parse_remember(html: str) -> str | None:
    """리멤버 커리어 페이지의 __NEXT_DATA__ JSON에서 공고 정보를 추출.

    URL 패턴: career.rememberapp.co.kr/job/posting/{id}
    데이터 경로: props.pageProps.dehydratedState.queries[0].state.data.data
    """
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        queries = (
            data.get("props", {})
            .get("pageProps", {})
            .get("dehydratedState", {})
            .get("queries", [])
        )
        if not queries:
            return None

        posting = queries[0].get("state", {}).get("data", {}).get("data", {})
        if not posting:
            return None

        org = posting.get("organization") or {}

        # 근무지
        addresses = posting.get("addresses") or []
        location = ""
        if addresses:
            addr = addresses[0]
            location = " ".join(
                p for p in [addr.get("addressLevel1", ""), addr.get("addressLevel2", "")] if p
            )

        # 경력
        min_exp = posting.get("minExperience")
        max_exp = posting.get("maxExperience")
        if min_exp and max_exp:
            career_str = f"{min_exp}~{max_exp}년"
        elif min_exp:
            career_str = f"{min_exp}년 이상"
        else:
            career_str = ""

        # 연봉 (원 단위 → 만원)
        min_sal = posting.get("minSalary")
        max_sal = posting.get("maxSalary")
        if min_sal and max_sal:
            salary_str = f"{int(min_sal) // 10_000:,}만원 ~ {int(max_sal) // 10_000:,}만원"
        elif min_sal:
            salary_str = f"{int(min_sal) // 10_000:,}만원 이상"
        else:
            salary_str = ""

        # 임직원 수 (chips에서 startup_info 카테고리)
        chips = posting.get("chips") or []
        employee_count = next(
            (c.get("value", "") for c in chips if c.get("category") == "startup_info"), ""
        )

        parts = [
            f"회사명: {org.get('name', '')}",
            f"직무: {posting.get('title', '')}",
            f"회사 소개: {_clean_rich_text(posting.get('introduction') or posting.get('companyDescription') or '')}",
            f"주요업무:\n{_clean_rich_text(posting.get('jobDescription', ''))}",
            f"자격요건:\n{_clean_rich_text(posting.get('qualifications', ''))}",
            f"우대사항:\n{_clean_rich_text(posting.get('preferredQualifications', ''))}",
            f"혜택 및 복지:\n{_clean_rich_text(posting.get('additionalInformation', ''))}",
            f"채용절차:\n{_clean_rich_text(posting.get('recruitingProcess', ''))}",
            f"근무지: {location}",
            f"경력: {career_str}",
            f"연봉: {salary_str}",
            f"임직원 수: {employee_count}",
            f"웹사이트: {org.get('url', '')}",
        ]
        text = "\n".join(
            p for p in parts
            if (": " in p and p.split(": ", 1)[1].strip())
            or (":\n" in p and p.split(":\n", 1)[1].strip())
        )
        return text or None
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


async def _fetch_wanted_company(client: httpx.AsyncClient, company_id: int) -> str | None:
    """Wanted 기업 페이지에서 회사 규모·재무 정보를 추출.

    dehydrateState 안의 companyInfo / companySummary 쿼리를 파싱한다.
    실패해도 공고 분석 전체를 막지 않도록 None을 반환한다.
    """
    try:
        resp = await _safe_get(
            client,
            f"https://www.wanted.co.kr/company/{company_id}",
            timeout=15,
        )
        resp.raise_for_status()
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
        if not m:
            return None

        queries = (
            json.loads(m.group(1))
            .get("props", {})
            .get("pageProps", {})
            .get("dehydrateState", {})
            .get("queries", [])
        )
        info, summary = {}, {}
        for q in queries:
            key = q.get("queryKey", [None])[0]
            qdata = q.get("state", {}).get("data") or {}
            if key == "companyInfo":
                info = qdata
            elif key == "companySummary":
                summary = qdata

        if not info and not summary:
            return None

        detail = summary.get("detail") or {}
        employee = summary.get("employee") or {}
        sales_data = summary.get("sales") or {}
        salary_data = summary.get("salary") or {}

        # 임직원 수: 국민연금(NPS) 우선, 없으면 고용보험(EI)
        emp_count = employee.get("total") or detail.get("eiEmployeeCount")
        emp_source = "국민연금 기준" if employee.get("total") else "고용보험 기준"

        # 매출 (원 → 억 단위)
        total_sales = sales_data.get("total") or detail.get("totalSales")
        sales_str = f"{int(total_sales) // 100_000_000:,}억원" if total_sales else None

        # 평균 연봉 (원 → 만원)
        avg_salary = salary_data.get("salary") or detail.get("salary")
        salary_str = f"{int(avg_salary) // 10_000:,}만원" if avg_salary else None

        # 상장 구분
        corp_class = detail.get("corpClass")  # 코스피, 코스닥, None
        ticker = detail.get("tickerSymbol")

        parts = ["[원티드 기업 정보]"]
        if info.get("foundedYear"):
            parts.append(f"설립연도: {info['foundedYear']}년 (업력 {info.get('age', '')}년)")
        if emp_count:
            parts.append(f"임직원 수: {emp_count:,}명 ({emp_source})")
        if sales_str:
            parts.append(f"연매출: {sales_str} (출처: {sales_data.get('source', '')})")
        if salary_str:
            parts.append(f"평균 연봉: {salary_str}")
        if corp_class:
            parts.append(f"상장 구분: {corp_class}" + (f" ({ticker})" if ticker else ""))
        elif corp_class is None and detail.get("corpType"):
            parts.append("상장 구분: 비상장")
        if info.get("status") == "RUNNING":
            parts.append("사업자 상태: 영업 중")
        if info.get("location"):
            parts.append(f"본사 위치: {info['location']}")

        # mainTags에서 연봉 티어, 임직원 범위 등 가져오기
        main_tags = [t["title"] for t in info.get("mainTags", [])]
        if main_tags:
            parts.append(f"원티드 태그: {', '.join(main_tags)}")

        return "\n".join(parts) if len(parts) > 1 else None
    except Exception:
        return None


async def fetch_wanted_facts(source_url: str) -> dict | None:
    """Wanted URL(공고 /wd/ 또는 기업 /company/)에서 구조화된 기업 정보를 반환.

    반환 dict 키:
        employee_count, employee_count_meets_threshold, website, location,
        investment_stage, stability, revenue_status, founded_year, avg_salary,
        corp_class, ticker, main_tags
    실패 시 None.
    """
    async with httpx.AsyncClient(headers=_HEADERS, timeout=20) as client:
        try:
            # /company/{id} URL이면 바로 사용, /wd/{id}면 공고 페이지에서 company_id 추출
            company_match = re.search(r"wanted\.co\.kr/company/(\d+)", source_url)
            if company_match:
                company_id = int(company_match.group(1))
            else:
                resp = await _safe_get(client, source_url)
                resp.raise_for_status()
                _, company_id = _parse_wanted(resp.text)
                if not company_id:
                    return None

            resp = await _safe_get(client, f"https://www.wanted.co.kr/company/{company_id}")
            resp.raise_for_status()
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
            if not m:
                return None

            queries = (
                json.loads(m.group(1))
                .get("props", {})
                .get("pageProps", {})
                .get("dehydrateState", {})
                .get("queries", [])
            )
            info, summary = {}, {}
            for q in queries:
                key = q.get("queryKey", [None])[0]
                qdata = q.get("state", {}).get("data") or {}
                if key == "companyInfo":
                    info = qdata
                elif key == "companySummary":
                    summary = qdata

            detail = summary.get("detail") or {}
            employee = summary.get("employee") or {}
            sales_data = summary.get("sales") or {}
            salary_data = summary.get("salary") or {}

            emp_count = employee.get("total") or detail.get("eiEmployeeCount")
            total_sales = sales_data.get("total") or detail.get("totalSales")
            avg_salary = salary_data.get("salary") or detail.get("salary")
            corp_class = detail.get("corpClass")
            ticker = detail.get("tickerSymbol")
            founded_year = info.get("foundedYear") or detail.get("foundedYear")
            main_tags = [t["title"] for t in info.get("mainTags", [])]

            # stability 추론: 상장 여부 기준으로만 결정 (나머지는 LLM에 맡김)
            if corp_class in ("코스피", "코스닥"):
                stability = "강"
            else:
                stability = None  # 변경하지 않음

            investment_stage = None
            if corp_class in ("코스피", "코스닥"):
                investment_stage = f"{corp_class} 상장" + (f" ({ticker})" if ticker else "")

            return {
                "employee_count": f"{emp_count:,}명" if emp_count else None,
                "employee_count_meets_threshold": emp_count >= 50 if emp_count else None,
                "website": info.get("link") or None,
                "location": info.get("location") or None,
                "investment_stage": investment_stage,
                "stability": stability,
                "revenue_status": "확인됨" if total_sales else None,
                "founded_year": founded_year,
                "avg_salary_man_won": int(avg_salary) // 10_000 if avg_salary else None,
                "corp_class": corp_class,
                "ticker": ticker,
                "main_tags": main_tags,
            }
        except Exception:
            return None


def _extract_main_content(soup: BeautifulSoup) -> str:
    """불필요한 태그를 제거하고 본문 영역만 추출."""
    for tag in soup(["nav", "header", "footer", "script", "style", "aside", "iframe"]):
        tag.decompose()

    # main > article > #content 순으로 주요 영역 우선 탐색
    for selector in ["main", "article", "#content", "#job-content", ".job-description"]:
        el = soup.select_one(selector)
        if el:
            return el.get_text(separator="\n", strip=True)

    return soup.get_text(separator="\n", strip=True)
