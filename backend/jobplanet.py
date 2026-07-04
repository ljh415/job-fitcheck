"""
잡플래닛 평점 수집 모듈.

잡플래닛 사이트 직접 접근은 Cloudflare 403으로 불가 (확인됨).
검색 엔진 스니펫에서 평점을 파싱하는 방식을 사용한다.

검색 전략 (순서대로 시도):
  1. Naver  — 안정적이나 일부 소규모 회사는 인덱싱 안 됨
  2. DuckDuckGo HTML — Naver에서 못 찾은 경우 fallback
     단, 연속 요청 시 rate limiting (202) 발생 가능

스니펫 형식 예시:
  "(주)에너닷 2026년 상반기 채용 | 기업리뷰 13건, 3.8 리뷰평점"

회사명 오탐 방지:
  유사한 이름의 다른 회사가 섞일 수 있어 (예: 라이넨스 vs 라이너),
  _match_score()로 유사도를 계산해 0.5 이상인 최선 후보만 채택한다.
"""
import re
import unicodedata

import httpx
from bs4 import BeautifulSoup

_NAVER_URL = "https://search.naver.com/search.naver"
_DDG_URL = "https://html.duckduckgo.com/html/"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# 스니펫/제목에서 평점 패턴: "기업리뷰 13건, 3.8 리뷰평점"
_SCORE_RE = re.compile(r"기업리뷰\s*(\d+)건[,\s]+(\d+\.\d+)\s*리뷰평점")
# Naver JSON title 필드
_NAVER_TITLE_RE = re.compile(r'"title"\s*:\s*"([^"]{0,300})"')
_MARK_RE = re.compile(r"</?mark>")
# 제목 앞부분 회사명 추출
_SNIPPET_COMPANY_RE = re.compile(r"^(.+?)\s*(?:\d{4}년|기업정보|기업리뷰)")


class JobplanetResult:
    def __init__(
        self,
        score: float | None,
        review_count: int | None,
        source: str,
        raw_snippet: str = "",
    ):
        self.score = score
        self.review_count = review_count
        # source: "search_snippet" | "not_found" | "error"
        self.source = source
        self.raw_snippet = raw_snippet

    def to_dict(self) -> dict:
        return {
            "jobplanet_score": self.score,
            "jobplanet_review_count": self.review_count,
            "jobplanet_source": self.source,
        }


def _normalize(name: str) -> str:
    """법인 접두/접미사 제거 후 소문자 정규화."""
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"\(주\)|\(유\)|\(사\)|주식회사|유한회사|\s+", "", name)
    return name.lower()


def _match_score(query: str, snippet_company: str) -> float:
    """prefix 기반 유사도 (0~1). 더 긴 쪽 길이로 나눠 짧은 이름 오탐 방지."""
    q = _normalize(query)
    s = _normalize(snippet_company)
    if not q or not s:
        return 0.0
    if q == s:
        return 1.0
    common = 0
    for a, b in zip(q, s):
        if a == b:
            common += 1
        else:
            break
    return common / max(len(q), len(s))


def _best_candidate(company_name: str, candidates: list[tuple[float, int, str]]) -> JobplanetResult | None:
    """후보 목록에서 회사명 유사도 0.5 이상인 최선 항목을 반환."""
    best_sim = 0.0
    best: tuple[float, int, str] | None = None
    for jp_score, count, title in candidates:
        m = _SNIPPET_COMPANY_RE.match(title)
        snippet_company = m.group(1) if m else ""
        sim = _match_score(company_name, snippet_company)
        if sim > best_sim:
            best_sim = sim
            best = (jp_score, count, title)
    if best and best_sim >= 0.5:
        return JobplanetResult(best[0], best[1], "search_snippet", best[2][:200])
    return None


async def _search_naver(company_name: str, client: httpx.AsyncClient) -> list[tuple[float, int, str]]:
    """Naver 검색 결과 HTML의 JSON title 필드에서 후보를 추출한다.

    'site:jobplanet.co.kr' 쿼리를 사용하면 잡플래닛 페이지만 인덱싱되어
    일반 쿼리보다 훨씬 더 많은 회사를 찾을 수 있다 (에너닷 등 소규모 포함).
    """
    query = f"site:jobplanet.co.kr {company_name}"
    resp = await client.get(_NAVER_URL, params={"query": query})
    resp.raise_for_status()
    candidates = []
    for m in _NAVER_TITLE_RE.finditer(resp.text):
        raw = _MARK_RE.sub("", m.group(1))
        sm = _SCORE_RE.search(raw)
        if sm:
            candidates.append((float(sm.group(2)), int(sm.group(1)), raw))
    return candidates


async def _search_ddg(company_name: str, client: httpx.AsyncClient) -> list[tuple[float, int, str]]:
    """DuckDuckGo HTML 검색 결과 제목에서 후보를 추출한다."""
    query = f"{company_name} 잡플래닛 평점 리뷰"
    resp = await client.get(_DDG_URL, params={"q": query})
    # 202 = rate limited, 결과 없음으로 처리
    if resp.status_code != 200:
        return []
    soup = BeautifulSoup(resp.text, "lxml")
    candidates = []
    for result in soup.select(".result"):
        title_el = result.select_one(".result__title")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        sm = _SCORE_RE.search(title)
        if sm:
            candidates.append((float(sm.group(2)), int(sm.group(1)), title))
    return candidates


async def fetch_jobplanet_score(company_name: str) -> JobplanetResult:
    """Naver → DuckDuckGo 순서로 잡플래닛 평점을 조회한다.

    Returns:
        JobplanetResult:
            - score/review_count가 None이면 미확인 (소규모·미등록 회사)
            - source="error" 면 네트워크 오류 (호출 측에서 무시해도 됨)
    """
    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=15, follow_redirects=True) as client:
            # 1차: Naver
            candidates = await _search_naver(company_name, client)
            result = _best_candidate(company_name, candidates)
            if result:
                return result

            # 2차: DuckDuckGo fallback
            candidates = await _search_ddg(company_name, client)
            result = _best_candidate(company_name, candidates)
            if result:
                return result

    except Exception as e:
        return JobplanetResult(None, None, "error", str(e))

    return JobplanetResult(None, None, "not_found")
