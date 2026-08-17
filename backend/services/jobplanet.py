"""
잡플래닛 평점 수집 모듈.

잡플래닛 사이트 직접 접근은 Cloudflare 403으로 불가 (확인됨).
검색 엔진 스니펫에서 평점을 파싱하는 방식을 사용한다.

검색 전략 (순서대로 시도):
  1. Naver  — 검색결과 안의 안정적인 평점 블록(class="fds-listitem")을 파싱.
     예전엔 JSON title 필드(최대 300자)를 정규식으로 훑었는데, Naver가 그
     텍스트를 매번 다른 지점에서 잘라버려서 평점 부분이 종종 통째로 사라졌다
     (카카오처럼 리뷰 많은 회사도 not_found로 오탐, 2026-08-15 발견 → 2026-08-17
     수정). fds-listitem 블록은 title JSON과 별도로 안정적으로 렌더링되고 잘릴
     걱정이 없다.
  2. DuckDuckGo HTML — Naver에서 못 찾은 경우 fallback
     단, 연속 요청 시 rate limiting (202) 발생 가능

Naver 평점 블록 형식 예시 (item.get_text()):
  "평점 3.8/5 1,307 참여"
같은 컨테이너 상위에 "(주) 카카오 기업정보 - 산업: ..." 형태의 링크가 있어
회사명을 같이 뽑는다.

DDG 스니펫 형식 예시:
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

# DDG 스니펫에서 평점 패턴: "기업리뷰 13건, 3.8 리뷰평점"
_SCORE_RE = re.compile(r"기업리뷰\s*(\d+)건[,\s]+(\d+\.\d+)\s*리뷰평점")
# Naver 평점 블록(class="fds-listitem") 텍스트: "평점 3.8/5 1,307 참여"
_NAVER_SCORE_RE = re.compile(r"평점\s*(\d+\.\d+)/5\s*([\d,]+)\s*참여")
# Naver "기업정보" 링크에서 회사명 추출: "(주) 카카오 기업정보 - 산업: ..."
_NAVER_INFO_RE = re.compile(r"^(.*?)\s*기업정보")
# 제목 앞부분 회사명 추출 (DDG 스니펫용, Naver 쪽은 _NAVER_INFO_RE로 이미 순수
# 회사명만 뽑으므로 " 기업정보"를 합성해서 이 정규식과 호환되게 넘긴다)
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
    """법인 접두/접미사·괄호 내 영문 제거 후 소문자 정규화."""
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"\(주\)|\(유\)|\(사\)|주식회사|유한회사", "", name)
    name = re.sub(r"\([^)]*\)", "", name)  # (DEEP.FINE) 등 괄호 내용 제거
    name = re.sub(r"\s+", "", name)
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
    """Naver 검색 결과 HTML의 안정적인 평점 블록(class="fds-listitem")에서
    후보를 추출한다.

    'site:jobplanet.co.kr' 쿼리를 사용하면 잡플래닛 페이지만 인덱싱되어
    일반 쿼리보다 훨씬 더 많은 회사를 찾을 수 있다 (에너닷 등 소규모 포함).

    각 평점 블록에서 위쪽 조상 요소(최대 6단계)를 훑어 "...기업정보" 링크를
    찾아 회사명을 페어링한다 — 못 찾으면 그 후보는 버린다(회사명 없인 유사도
    매칭이 불가능하므로).
    """
    query = f"site:jobplanet.co.kr {company_name}"
    resp = await client.get(_NAVER_URL, params={"query": query})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    candidates = []
    for item in soup.select(".fds-listitem"):
        sm = _NAVER_SCORE_RE.search(item.get_text(" ", strip=True))
        if not sm:
            continue
        score = float(sm.group(1))
        count = int(sm.group(2).replace(",", ""))
        company = None
        node = item.parent
        for _ in range(6):
            if node is None:
                break
            for a in node.find_all("a"):
                m = _NAVER_INFO_RE.match(a.get_text(" ", strip=True))
                if m and m.group(1):
                    company = m.group(1)
                    break
            if company:
                break
            node = node.parent
        if company:
            # _best_candidate()의 _SNIPPET_COMPANY_RE가 "...기업정보" 형태를
            # 기대하므로, 이미 순수하게 뽑아낸 회사명에 마커를 다시 합성해서 넘긴다.
            candidates.append((score, count, f"{company} 기업정보"))
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
    # 괄호 내 영문 제거 — "딥파인(DEEP.FINE)" → "딥파인" 으로 검색
    search_name = re.sub(r"\([^)]*\)", "", company_name).strip() or company_name
    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=15, follow_redirects=True) as client:
            # 1차: Naver
            candidates = await _search_naver(search_name, client)
            result = _best_candidate(search_name, candidates)
            if result:
                return result

            # 2차: DuckDuckGo fallback
            candidates = await _search_ddg(search_name, client)
            result = _best_candidate(search_name, candidates)
            if result:
                return result

    except Exception as e:
        return JobplanetResult(None, None, "error", str(e))

    return JobplanetResult(None, None, "not_found")


if __name__ == "__main__":
    import asyncio

    async def _check():
        # 카카오: 이전엔 title JSON 절단으로 not_found 오탐(2026-08-15 발견)
        kakao = await fetch_jobplanet_score("카카오")
        assert kakao.source == "search_snippet", f"카카오: {kakao.source}"
        assert kakao.score is not None and kakao.score > 0
        print("카카오:", kakao.score, kakao.review_count)

        # 에너닷: 소규모 회사, 예전에도 되던 케이스라 회귀 확인용
        enerdat = await fetch_jobplanet_score("에너닷")
        assert enerdat.source == "search_snippet", f"에너닷: {enerdat.source}"
        print("에너닷:", enerdat.score, enerdat.review_count)

        # 존재하지 않는 회사: false positive 없이 not_found여야 함
        none_result = await fetch_jobplanet_score("존재하지않는가상의회사이름ABCXYZ123")
        assert none_result.source == "not_found", f"미등록 회사인데: {none_result.source}"
        print("미등록 회사:", none_result.source)

    asyncio.run(_check())
    print("jobplanet self-check 통과")
