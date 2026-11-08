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
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

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


def _log_url(url: str) -> str:
    """로그에는 주소의 ? 앞부분만 남긴다 — 뒤의 추적용 코드가 길고 원인 파악에 필요 없다."""
    return url.split("?")[0]


def _error_summary(e: httpx.HTTPError) -> str:
    """오류 응답은 번호만(기본 문구에는 접속에 쓴 IP와 안내문이 길게 붙는다), 나머지는 종류와 내용."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    return f"{type(e).__name__}: {e}"


class SSRFBlockedError(ValueError):
    """사용자 입력 URL이 내부/사설 네트워크를 가리켜 요청을 차단했을 때 발생."""


def _is_blocked_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


async def _resolve_public_address(hostname: str) -> str:
    """호스트명의 DNS 응답 중 사설/루프백 등이 하나라도 있으면 차단하고, 검증에 사용한
    주소를 그대로 반환한다. 검증(DNS 조회)과 실제 연결을 별도로 조회하면 그 사이 응답이
    바뀌는 DNS 리바인딩으로 SSRF 방어가 무력화될 수 있어, 반환된 주소로 직접 연결해야 한다."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(hostname, None)
    except OSError as e:
        raise SSRFBlockedError(f"호스트를 확인할 수 없습니다: {hostname}") from e
    if not infos:
        raise SSRFBlockedError(f"호스트를 확인할 수 없습니다: {hostname}")
    for info in infos:
        ip_str = info[4][0]
        if _is_blocked_ip(ip_str):
            raise SSRFBlockedError(f"내부/사설 네트워크로의 요청은 차단됩니다: {hostname} → {ip_str}")
    return infos[0][4][0]


def _pin_to_ip(url: str, ip: str) -> str:
    """URL의 호스트 부분만 검증된 IP로 교체한다 (포트는 유지)."""
    parsed = urlparse(url)
    ip_literal = f"[{ip}]" if ":" in ip else ip
    netloc = f"{ip_literal}:{parsed.port}" if parsed.port else ip_literal
    return parsed._replace(netloc=netloc).geturl()


def is_wanted_host(url: str) -> bool:
    """호스트명이 정확히 wanted.co.kr 도메인(또는 원티드 단축 링크 wntd.co)인지 검사 (부분 문자열 검사 대체)."""
    host = (urlparse(url).hostname or "").lower()
    return host in ("wanted.co.kr", "wntd.co") or host.endswith(".wanted.co.kr")


def _listing_label(corp_class: str | None, ticker: str | None) -> str | None:
    """상장 여부를 확실히 알 수 있을 때만 값을 돌려준다 — 상장 구분이 코스피·코스닥이거나
    종목 코드가 있을 때. 둘 다 없으면 모르는 것이라 None이다(법인 유형은 상장 여부와 무관)."""
    if corp_class in ("코스피", "코스닥"):
        return corp_class
    return "상장" if ticker else None


async def _safe_get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """SSRF 방지: 요청 전 및 각 redirect마다 스킴/호스트/DNS 목적지를 재검사하고,
    검증에 사용한 IP로 직접 연결한다 (DNS 리바인딩으로 검증 후 연결 대상이 바뀌는 것을 방지).
    Host 헤더와 TLS SNI는 원래 호스트명으로 유지해 가상호스팅/인증서 검증이 정상 동작하게 한다.
    응답은 스트리밍으로 받아 _MAX_RESPONSE_BYTES를 넘기면 즉시 중단한다
    (Content-Length가 없거나 거짓인 응답도 실제 수신 바이트 기준으로 막기 위함)."""
    current_url = url
    for _ in range(_MAX_REDIRECTS + 1):
        parsed = urlparse(current_url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            raise SSRFBlockedError(f"허용되지 않은 URL 스킴입니다: {parsed.scheme}")
        if not parsed.hostname:
            raise SSRFBlockedError("URL에 호스트가 없습니다.")
        ip = await _resolve_public_address(parsed.hostname)
        pinned_url = _pin_to_ip(current_url, ip)
        extensions = {"sni_hostname": parsed.hostname} if parsed.scheme == "https" else {}
        request = client.build_request(
            "GET", pinned_url, headers={"Host": parsed.netloc}, extensions=extensions, **kwargs
        )
        response = await client.send(request, stream=True, follow_redirects=False)
        try:
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
        finally:
            await response.aclose()
    raise SSRFBlockedError("리다이렉트 횟수가 너무 많습니다.")


def _clean_rich_text(value: str) -> str:
    """리치텍스트 에디터로 작성된 JSON 필드(주요업무·자격요건 등)에 섞여 있는
    HTML 태그(<p>, <li>, <br> 등)를 제거하고 순수 텍스트만 남긴다."""
    if not value or "<" not in value:
        return value
    return BeautifulSoup(value, "lxml").get_text(separator="\n", strip=True)


async def fetch_url(url: str) -> tuple[str, dict | None]:
    """URL에서 채용공고 텍스트와 회사 정보를 추출한다.

    반환은 (원문 글자, 회사 정보). 회사 정보는 기업 페이지 수집에 성공한 원티드·리멤버
    공고만 있고 나머지는 None이다. 회사 정보 키: source, source_company_id,
    source_url(기업 페이지 주소), company_payload(기업 페이지 원본),
    posting_company_payload(리멤버 공고 페이지의 회사 부분, 원티드는 None),
    employee_count·revenue_won·revenue_as_of·listing_status(원티드·리멤버),
    funding_stage·funding_total(리멤버만)은 원본에서 뽑은 평가용 값, 모르면 None.

    Raises:
        ValueError: JS 렌더링이 필요해 스크래핑이 불가능한 사이트
        httpx.HTTPError: 네트워크 오류 또는 4xx/5xx 응답
    """
    async with httpx.AsyncClient(headers=_HEADERS, timeout=20) as client:
        response = await _safe_get(client, url)
        response.raise_for_status()
        html = response.text

        # 원티드는 SSR JSON에 구조화된 공고 데이터가 있어 파싱 품질이 높음.
        # 개별 공고(/wd/)가 아니라 회사 채용정보 목록(/company/) 등 다른 페이지면
        # __NEXT_DATA__에 job/initialData가 없어 _parse_wanted()가 None을 반환하는데,
        # 이때 아래 범용 스크래핑으로 넘어가면 회사 소개문 등을 공고 원문으로 오인해
        # 실제 요건과 무관한 분석·적합도 평가가 생성된다(2026-09-06 실사용 중 발견 —
        # /company/{id} URL로 등록 시 회사 소개 텍스트가 공고인 것처럼 분석됨). 원티드
        # 도메인이 확인되면 구조화 파싱 실패 시 바로 명확히 거부한다.
        if is_wanted_host(url):
            text, company_id = _parse_wanted(html)
            if text:
                company = None
                if company_id:
                    extra = await _fetch_wanted_company(client, company_id)
                    if extra:
                        text = text + "\n\n" + extra[0]
                        company = {
                            "source": "wanted",
                            "source_company_id": str(company_id),
                            "source_url": f"https://www.wanted.co.kr/company/{company_id}",
                            "company_payload": extra[1],
                            "posting_company_payload": None,
                            **_wanted_eval_columns(extra[1]),
                        }
                else:
                    logger.warning("원티드 공고에서 회사 번호를 찾지 못해 기업 페이지를 건너뜀 (url=%s)", _log_url(url))
                return text[:_MAX_TEXT_CHARS], company
            logger.info("원티드 공고 본문을 읽지 못해 거부 (url=%s)", _log_url(url))
            raise ValueError(
                "이 URL에서 채용공고 정보를 찾을 수 없습니다. 회사 페이지가 아니라 "
                "개별 채용공고 페이지(wanted.co.kr/wd/...) URL인지 확인해주세요."
            )

        # 리멤버 커리어도 __NEXT_DATA__ JSON에 구조화 데이터 포함 — 같은 이유로
        # 구조화 파싱 실패 시 범용 스크래핑으로 넘어가지 않고 거부한다.
        if "rememberapp.co.kr" in url:
            text = _parse_remember(html)
            if text:
                company = None
                company_id = _remember_company_id(html)
                if company_id:
                    extra = await _fetch_remember_company(client, company_id)
                    if extra:
                        text = text + "\n\n" + extra[0]
                        posting_company = _remember_posting_company(html)
                        company = {
                            "source": "remember",
                            "source_company_id": str(company_id),
                            "source_url": f"https://career.rememberapp.co.kr/job/company/{company_id}",
                            "company_payload": extra[1],
                            "posting_company_payload": posting_company,
                            **_remember_eval_columns(extra[1], posting_company),
                        }
                else:
                    logger.warning("리멤버 공고에서 회사 번호를 찾지 못해 기업 페이지를 건너뜀 (url=%s)", _log_url(url))
                return text[:_MAX_TEXT_CHARS], company
            logger.info("리멤버 공고 본문을 읽지 못해 거부 (url=%s)", _log_url(url))
            raise ValueError(
                "이 URL에서 채용공고 정보를 찾을 수 없습니다. 개별 채용공고 페이지 URL인지 확인해주세요."
            )

    soup = BeautifulSoup(html, "lxml")
    text = _extract_main_content(soup)

    # 추출된 본문이 300자 미만이면 JS 렌더링 사이트로 판단
    if len(text) < 300:
        logger.info("본문이 %d자뿐이라 JS 렌더링 사이트로 판단해 거부 (url=%s)", len(text), _log_url(url))
        raise ValueError(
            "이 URL은 JavaScript 렌더링이 필요해 자동 스크래핑이 어렵습니다. "
            "'텍스트 붙여넣기' 탭에서 공고 내용을 복사해 입력해주세요."
        )

    return text[:_MAX_TEXT_CHARS], None


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
            a = career.get("annual_from")
            b = career.get("annual_to")
            # annual_to 100은 상한 없음(사이트의 "경력 N년 이상"). 경력자 표시가 있어도 연차 숫자를 버리지 않는다.
            if b is not None and b >= 100:
                b = None
            if career.get("is_newbie") and career.get("is_expert"):
                career_str = f"신입-경력 {b}년" if b else "신입-경력"
            elif career.get("is_newbie"):
                career_str = "신입"
            elif a and b:
                career_str = f"{a}~{b}년"
            elif a:
                career_str = f"{a}년 이상"
            elif career.get("is_expert"):
                career_str = "경력"

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
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("원티드 공고 데이터 해석 실패 — 사이트 구조가 바뀌었을 수 있음: %s: %s", type(e).__name__, e)
        return None, None


def _remember_chip_facts(chips: list) -> tuple[str, str, str]:
    """리멤버 공고 페이지의 회사 요약 태그(chips)에서 (임직원 규모 구간, 투자 단계, 누적 투자금)을 읽는다.

    `startup_info`에 투자 단계("시리즈 C")와 임직원 규모 구간("51-300명")이 같은 이름으로
    섞여 오므로 값의 모양으로 구분한다 — 숫자+"명"이면 규모, 시리즈·Pre IPO 등이면 투자 단계.
    어느 쪽도 아닌 값은 모르는 것으로 두고 쓰지 않는다. 없으면 빈 문자열.
    """
    size = stage = funding = ""
    for chip in chips:
        value = (chip.get("value") or "").strip()
        if chip.get("category") == "startup_info":
            if re.search(r"\d\s*명", value):
                size = size or value
            elif re.search(r"시리즈|series|pre[\s-]*ipo|시드|seed|엔젤", value, re.IGNORECASE):
                stage = stage or value
        elif chip.get("category") == "investment":
            funding = funding or value.removeprefix("누적 투자").strip()
    return size, stage, funding


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
        elif min_exp is None and max_exp is None:
            career_str = "경력 무관"  # 사이트가 두 값이 비면 "경력 무관"으로 표시한다
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

        employee_range, funding_stage, funding_total = _remember_chip_facts(posting.get("chips") or [])

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
            f"임직원 규모: {employee_range}",
            f"투자 단계: {funding_stage}",
            f"누적 투자금: {funding_total}",
            f"웹사이트: {org.get('url', '')}",
        ]
        text = "\n".join(
            p for p in parts
            if (": " in p and p.split(": ", 1)[1].strip())
            or (":\n" in p and p.split(":\n", 1)[1].strip())
        )
        return text or None
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("리멤버 공고 데이터 해석 실패 — 사이트 구조가 바뀌었을 수 있음: %s: %s", type(e).__name__, e)
        return None


def _remember_company_id(html: str) -> int | None:
    """리멤버 공고 페이지의 __NEXT_DATA__에서 기업 ID를 읽는다."""
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not match:
        return None
    try:
        queries = json.loads(match.group(1))["props"]["pageProps"]["dehydratedState"]["queries"]
        return int(queries[0]["state"]["data"]["data"]["company"]["id"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _remember_posting_company(html: str) -> dict | None:
    """리멤버 공고 페이지에서 회사 관련 세 항목(chips·company·organization)만 원본 그대로
    꺼낸다. 공고 본문은 .raw.txt에 있어 보관하지 않는다. 못 찾으면 None."""
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not match:
        return None
    try:
        queries = json.loads(match.group(1))["props"]["pageProps"]["dehydratedState"]["queries"]
        posting = queries[0]["state"]["data"]["data"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        logger.warning("리멤버 공고의 회사 부분을 읽지 못함: %s: %s", type(e).__name__, e)
        return None
    return {k: posting.get(k) for k in ("chips", "company", "organization")}


async def _fetch_remember_company(client: httpx.AsyncClient, company_id: int) -> tuple[str, dict] | None:
    """리멤버 기업 페이지에서 설립일·임직원 수·평균 연봉·본사 위치를 추출한다.

    공고 페이지에는 임직원 수 구간(chips)만 있고 정확한 수치는 기업 페이지에만 있다.
    리멤버에 값이 없는 회사(null)는 해당 줄을 빼고, 실패해도 공고 분석 전체를
    막지 않도록 None을 반환한다. 반환은 (원문에 붙일 글자, 기업 페이지 원본 dict).
    """
    try:
        resp = await _safe_get(client, f"https://career.rememberapp.co.kr/job/company/{company_id}")
        resp.raise_for_status()
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
        if not m:
            logger.warning("리멤버 기업 페이지에 데이터가 없음 (company_id=%s)", company_id)
            return None
        queries = json.loads(m.group(1))["props"]["pageProps"]["dehydratedState"]["queries"]
        info = {}
        for q in queries:
            data = q.get("state", {}).get("data") or {}
            data = data.get("data", data)
            if isinstance(data, dict) and "employeeStatistics" in data:
                info = data
                break

        parts = ["[리멤버 기업 정보]"]
        if info.get("establishmentDate"):
            parts.append(f"설립일: {info['establishmentDate']}")
        # 월별 통계는 최신 월부터 오고 최신 월은 null일 수 있어 값이 있는 첫 달을 쓴다
        latest = next((s for s in info.get("employeeStatistics") or [] if s.get("total")), None)
        if latest:
            parts.append(f"임직원 수: {latest['total']:,}명 ({latest['month']} 기준)")
        avg_salary = (info.get("salaryStatistics") or {}).get("average")
        if avg_salary:
            parts.append(f"평균 연봉: {int(avg_salary) // 10_000:,}만원")
        if info.get("address"):
            parts.append(f"본사 위치: {info['address']}")
        if len(parts) == 1:
            logger.warning("리멤버 기업 페이지에서 뽑을 값이 없음 (company_id=%s)", company_id)
            return None
        return "\n".join(parts), info
    except httpx.HTTPError as e:
        logger.warning("리멤버 기업 페이지 수집 실패 (company_id=%s): %s", company_id, _error_summary(e))
        return None
    except Exception:
        logger.exception("리멤버 기업 페이지 수집 중 예상 못 한 오류 (company_id=%s)", company_id)
        return None


async def _fetch_wanted_company(client: httpx.AsyncClient, company_id: int) -> tuple[str, dict] | None:
    """Wanted 기업 페이지에서 회사 규모·재무 정보를 추출.

    dehydrateState 안의 companyInfo / companySummary 쿼리를 파싱한다. 반환은
    (원문에 붙일 글자, 쿼리 전체를 {queryKey: data}로 보존한 원본 dict).
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
            logger.warning("원티드 기업 페이지에 데이터가 없음 (company_id=%s)", company_id)
            return None

        queries = (
            json.loads(m.group(1))
            .get("props", {})
            .get("pageProps", {})
            .get("dehydrateState", {})
            .get("queries", [])
        )
        info, summary = {}, {}
        payload = {}
        for q in queries:
            key = q.get("queryKey", [None])[0]
            qdata = q.get("state", {}).get("data") or {}
            payload[key] = qdata
            if key == "companyInfo":
                info = qdata
            elif key == "companySummary":
                summary = qdata

        if not info and not summary:
            logger.warning("원티드 기업 페이지에 회사 데이터가 없음 (company_id=%s)", company_id)
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
        listing = _listing_label(corp_class, ticker)
        if listing:
            parts.append(f"상장 구분: {listing}" + (f" ({ticker})" if ticker else ""))
        if info.get("status") == "RUNNING":
            parts.append("사업자 상태: 영업 중")
        if info.get("location"):
            parts.append(f"본사 위치: {info['location']}")

        # mainTags에서 연봉 티어, 임직원 범위 등 가져오기
        main_tags = [t["title"] for t in info.get("mainTags", [])]
        if main_tags:
            parts.append(f"원티드 태그: {', '.join(main_tags)}")

        if len(parts) == 1:
            logger.warning("원티드 기업 페이지에서 뽑을 값이 없음 (company_id=%s)", company_id)
            return None
        return "\n".join(parts), payload
    except httpx.HTTPError as e:
        logger.warning("원티드 기업 페이지 수집 실패 (company_id=%s): %s", company_id, _error_summary(e))
        return None
    except Exception:
        logger.exception("원티드 기업 페이지 수집 중 예상 못 한 오류 (company_id=%s)", company_id)
        return None


def _wanted_eval_columns(payload: dict) -> dict:
    """원티드 기업 페이지 원본(payload)에서 평가용 컬럼 값을 뽑는다. 모르면 None."""
    summary = payload.get("companySummary") or {}
    detail = summary.get("detail") or {}
    employee = summary.get("employee") or {}
    sales = summary.get("sales") or {}
    nps = employee.get("total")
    emp = nps or detail.get("eiEmployeeCount")
    revenue = sales.get("total") or detail.get("totalSales")
    return {
        "employee_count": f"{emp:,}명 ({'국민연금' if nps else '고용보험'} 기준)" if emp else None,
        "revenue_won": int(revenue) if revenue else None,
        "revenue_as_of": sales.get("updatedAt") if revenue else None,
        "listing_status": _listing_label(detail.get("corpClass"), detail.get("tickerSymbol")),
    }


def _remember_eval_columns(payload: dict, posting: dict | None) -> dict:
    """리멤버 기업 페이지 원본(payload)과 공고 페이지의 회사 부분(posting)에서 평가용 컬럼
    값을 뽑는다. 임직원 수는 기업 페이지의 정확한 수치를 쓰고, 그게 비었을 때만 공고
    페이지 태그의 규모 구간("301-1,000명")을 원본 그대로 쓴다. 매출·상장 여부는 원본에 없다."""
    size, stage, funding = _remember_chip_facts((posting or {}).get("chips") or [])
    latest = next((s for s in payload.get("employeeStatistics") or [] if s.get("total")), None)
    return {
        "employee_count": f"{latest['total']:,}명 ({latest['month']} 기준)" if latest else size or None,
        "revenue_won": None,
        "revenue_as_of": None,
        "listing_status": None,
        "funding_stage": stage or None,
        "funding_total": funding or None,
    }


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
