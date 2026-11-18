"""
잡플래닛 평점 수집 모듈.

잡플래닛 사이트 직접 접근은 Cloudflare 403으로 불가 (2026-11-05 재확인).
검색 엔진 스니펫에서 평점을 파싱하는 방식을 사용한다.

검색 전략 (순서대로 시도):
  0. Gemini 구글 검색 연결 — GOOGLE_API_KEY가 있을 때만. 모델이 구글 검색을 실행해
     잡플래닛 페이지 제목("기업리뷰 N건, N.N 리뷰평점")을 읽어 답한다. 구글 검색 연결은
     유료 티어 키에서만 되고(무료 키는 429) 답이 요약 글이라 숫자가 1~2건 흔들릴 수 있어,
     답의 회사명이 조회한 회사명과 정확히 같을 때만 채택하고 아니면 다음 경로로 넘어간다.
  1. Naver  — 검색결과 안의 안정적인 평점 블록(class="fds-listitem")을 파싱.
     (2026-11-05 기준 이 블록이 사라져 후보 0건 — 보류 중)
     예전엔 JSON title 필드(최대 300자)를 정규식으로 훑었는데, Naver가 그
     텍스트를 매번 다른 지점에서 잘라버려서 평점 부분이 종종 통째로 사라졌다
     (카카오처럼 리뷰 많은 회사도 not_found로 오탐). fds-listitem 블록은 title
     JSON과 별도로 안정적으로 렌더링되고 잘릴 걱정이 없다.
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
import asyncio
import logging
import re
import unicodedata

import httpx
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

from config import settings
from services import usage_tracker

logger = logging.getLogger(__name__)

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

# Gemini 구글 검색 연결(그라운딩) 조회. 모델은 앱의 LLM 설정과 무관하게 고정한다 —
# GOOGLE_API_KEY만 있으면 동작하게 하려는 것(가격표상 3 이상 모델은 유료 티어에서만 검색 연결 제공).
_GEMINI_MODEL = "gemini-3.1-flash-lite"
_GEMINI_TIMEOUT_SEC = 30
_GEMINI_PROMPT = (
    "잡플래닛(jobplanet.co.kr)에서 회사 '{name}'의 기업리뷰 평점과 리뷰 수를 찾아줘. "
    "검색 결과 제목에 나온 숫자('기업리뷰 N건, N.N 리뷰평점')를 그대로 인용하고, "
    "계열사·부서·이름이 비슷한 다른 회사는 제외하고 정확히 그 회사만 답해. "
    "같은 이름의 페이지가 여럿이면 리뷰 수가 가장 많은 페이지를 골라. "
    "찾지 못하면 '없음'이라고만 답해. "
    "형식: 회사명=<잡플래닛에 표시된 회사명>, 평점=N.N, 리뷰수=N"
)
_GEMINI_ANSWER_RE = re.compile(r"회사명\s*=\s*(.+?)\s*,\s*평점\s*=\s*(\d+(?:\.\d+)?)\s*,\s*리뷰수\s*=\s*([\d,]+)")


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
        # source: "gemini_search" | "search_snippet" | "not_found" | "error"
        self.source = source
        self.raw_snippet = raw_snippet


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


# 같은 회사로 이미 아는 다른 이름(AI가 뽑은 display_name 등)은 정규화 후 이 글자 수 이상일 때만 쓴다 —
# 짧고 흔한 이름("삼성")으로 엉뚱한 회사의 평점이 채택되는 것을 막는다.
_MIN_KNOWN_NAME_LEN = 3


def _usable_known_names(known_names: list[str | None] | None) -> list[str]:
    """아는 다른 이름 중 쓸 수 있는 것(정규화 후 _MIN_KNOWN_NAME_LEN 글자 이상)만 원래 모양대로 돌려준다."""
    return [n for n in known_names or [] if n and len(_normalize(n)) >= _MIN_KNOWN_NAME_LEN]


def _best_candidate(
    company_name: str, candidates: list[tuple[float, int, str]], known_names: list[str | None] | None = None,
) -> JobplanetResult | None:
    """후보 목록에서 회사명 유사도 0.5 이상인 최선 항목을 반환. 같은 회사로 아는 다른 이름도
    조회한 이름과 똑같이 비교해 가장 높은 유사도를 쓴다."""
    names = [company_name] + _usable_known_names(known_names)
    best_sim = 0.0
    best: tuple[float, int, str] | None = None
    best_name = company_name
    for jp_score, count, title in candidates:
        m = _SNIPPET_COMPANY_RE.match(title)
        snippet_company = m.group(1) if m else ""
        for name in names:
            sim = _match_score(name, snippet_company)
            if sim > best_sim:
                best_sim = sim
                best = (jp_score, count, title)
                best_name = name
    if best and best_sim >= 0.5:
        if best_name != company_name:
            logger.info("잡플래닛 후보가 조회한 이름(%s)이 아니라 같은 회사의 다른 이름(%s)과 맞아 채택: %s", company_name, best_name, best[2][:60])
        return JobplanetResult(best[0], best[1], "search_snippet", best[2][:200])
    return None


def _extract_naver_candidates(html: str) -> list[tuple[float, int, str]]:
    """Naver 검색 결과 HTML의 안정적인 평점 블록(class="fds-listitem")에서
    후보를 추출한다. 네트워크와 분리해뒀다 — self-check에서 fixture HTML로
    직접 검증할 수 있도록.

    각 평점 블록에서 위쪽 조상 요소(최대 6단계)를 훑어 "...기업정보" 링크를
    찾아 회사명을 페어링한다 — 못 찾으면 그 후보는 버린다(회사명 없인 유사도
    매칭이 불가능하므로). 다만 조상이 평점 블록을 2개 이상 포함하게 되면 그건
    검색 결과 카드 하나의 범위를 넘어 여러 카드를 감싸는 공통 조상이라는
    뜻이라 — 거기서 찾은 링크는 남의 카드 것일 수 있으므로 더 올라가지 않고
    포기한다(자기 카드에 링크가 없는 항목이 옆 카드 링크를 가로채 틀린
    회사명과 페어링되는 걸 방지).
    """
    soup = BeautifulSoup(html, "lxml")
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
            # 이 조상 안에 평점 블록이 2개 이상이면 카드 경계를 이미 넘은 것
            # — 남의 카드 링크를 잘못 집어올 수 있으니 여기서 포기한다.
            if len(node.select(".fds-listitem")) >= 2:
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


def _read_gemini_answer(
    text: str, company_name: str, known_names: list[str | None] | None = None,
) -> tuple[JobplanetResult | None, str]:
    """Gemini 답에서 (회사명, 평점, 리뷰 수)를 읽는다. 채택하면 (결과, ""), 못 쓰면 (None, 사유).
    네트워크와 분리해뒀다 — self-check에서 답 문장으로 직접 검증할 수 있도록.

    답의 회사명이 조회한 회사명, 또는 같은 회사로 이미 아는 다른 이름(known_names — AI가 뽑은
    display_name 등)과 정규화 후 정확히 같을 때만 채택한다 — 계열사·부서(더스윙오퍼레이션)나
    이름이 비슷한 다른 회사가 섞인 답을 걸러낸다. 구글은 조회한 이름(케이티밀리의서재)이 아니라
    잡플래닛 등록명(밀리의서재)을 돌려줄 수 있어서 아는 이름도 인정한다. 리뷰가 0건이면
    평점이 의미 없으므로(0.0) 정보 없음으로 본다."""
    m = _GEMINI_ANSWER_RE.search(text)
    if not m:
        return None, f"답에서 값을 못 읽음: {text[:60]!r}"
    found, score, count = m.group(1).strip(), float(m.group(2)), int(m.group(3).replace(",", ""))
    found_norm = _normalize(found)
    via_known = None
    if found_norm != _normalize(company_name):
        via_known = next((n for n in _usable_known_names(known_names) if _normalize(n) == found_norm), None)
        if via_known is None:
            return None, f"답의 회사명이 다름 (조회 {company_name} ≠ 답 {found})"
    if count == 0 or not 0 < score <= 5:
        return None, f"쓸 수 없는 값 (평점 {score}, 리뷰 {count}건)"
    if via_known is not None:
        logger.info("잡플래닛 답의 회사명(%s)이 조회한 이름(%s)이 아니라 같은 회사의 다른 이름(%s)과 같아 채택", found, company_name, via_known)
    return JobplanetResult(score, count, "gemini_search", text[:200]), ""


async def _search_gemini(company_name: str, known_names: list[str | None] | None = None) -> JobplanetResult | None:
    """Gemini의 구글 검색 연결로 잡플래닛 평점을 조회한다. 키가 없거나 한도 초과(무료 키)·
    오류·답이 맞지 않으면 사유를 로그로 남기고 None을 돌려줘 다음 경로로 넘어가게 한다."""
    key = settings.google_api_key
    if not key:
        logger.info("잡플래닛 Gemini 조회 건너뜀: GOOGLE_API_KEY 없음")
        return None
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0,
    )
    client = genai.Client(api_key=key)  # 변수에 붙잡아 둬야 한다 — 임시 객체로 쓰면 요청 중에 연결이 닫힌다
    try:
        with usage_tracker.timer() as t:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=_GEMINI_MODEL, contents=_GEMINI_PROMPT.format(name=company_name), config=config,
                ),
                _GEMINI_TIMEOUT_SEC,
            )
    except Exception as e:
        reason = str(e).replace(key, "***")[:120]
        if "RESOURCE_EXHAUSTED" in reason or getattr(e, "code", None) == 429:
            logger.warning(
                "잡플래닛 Gemini 조회 한도 초과(429) — 구글 검색 연결은 유료 티어 키에서만 쓸 수 있음, 다음 경로로 (%s)",
                company_name,
            )
        else:
            logger.warning("잡플래닛 Gemini 조회 실패 (%s): %s: %s", company_name, type(e).__name__, reason)
        return None

    usage = response.usage_metadata
    if usage:
        usage_tracker.append_usage(
            "잡플래닛 조회", _GEMINI_MODEL,
            usage.prompt_token_count or 0, usage.candidates_token_count or 0, t["ms"],
        )
    grounding = response.candidates[0].grounding_metadata if response.candidates else None
    searches = len(getattr(grounding, "web_search_queries", None) or [])
    result, reason = _read_gemini_answer((response.text or "").strip(), company_name, known_names)
    if result:
        logger.info("잡플래닛 Gemini 조회 (%s): %.1f점 %d건, 검색 %d회", company_name, result.score, result.review_count, searches)
    else:
        logger.info("잡플래닛 Gemini 조회 (%s) 채택 안 함, 검색 %d회: %s", company_name, searches, reason)
    return result


async def _search_naver(company_name: str, client: httpx.AsyncClient) -> list[tuple[float, int, str]]:
    """'site:jobplanet.co.kr' 쿼리로 Naver 검색 결과를 받아 후보를 추출한다.

    이 쿼리를 쓰면 잡플래닛 페이지만 인덱싱되어 일반 쿼리보다 훨씬 더 많은
    회사를 찾을 수 있다 (에너닷 등 소규모 포함). 실제 파싱은
    _extract_naver_candidates() 참고.
    """
    query = f"site:jobplanet.co.kr {company_name}"
    resp = await client.get(_NAVER_URL, params={"query": query})
    resp.raise_for_status()
    return _extract_naver_candidates(resp.text)


async def _search_ddg(company_name: str, client: httpx.AsyncClient) -> list[tuple[float, int, str]]:
    """DuckDuckGo HTML 검색 결과 제목에서 후보를 추출한다."""
    query = f"{company_name} 잡플래닛 평점 리뷰"
    resp = await client.get(_DDG_URL, params={"q": query})
    # 202 = rate limited, 결과 없음으로 처리
    if resp.status_code != 200:
        logger.warning("잡플래닛 DuckDuckGo 응답 %d — 요청 제한으로 보여 결과 없음 처리 (%s)", resp.status_code, company_name)
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


# 아는 이름으로 다시 검색할 때의 최소 길이. 이 검색은 company_name으로 못 찾았을 때만 돌아서 짧은 이름
# (티빙·SK)도 허용한다 — 답 검증에 쓰는 아는 이름(_MIN_KNOWN_NAME_LEN)보다 느슨하다.
_MIN_SEARCH_NAME_LEN = 2


def _search_names(company_name: str, known_names: list[str | None] | None) -> list[str]:
    """검색할 이름 후보: company_name 먼저, 그다음 아는 이름(정규화 후 _MIN_SEARCH_NAME_LEN 글자 이상,
    정규화 후 같은 이름은 한 번만)."""
    names, seen = [company_name], {_normalize(company_name)}
    for n in known_names or []:
        if n and len(_normalize(n)) >= _MIN_SEARCH_NAME_LEN and _normalize(n) not in seen:
            seen.add(_normalize(n))
            names.append(n)
    return names


async def fetch_jobplanet_score(company_name: str, known_names: list[str | None] | None = None) -> JobplanetResult:
    """잡플래닛 평점을 조회한다. company_name으로 먼저 조회하고, 못 찾으면(not_found) 같은 회사로
    이미 아는 다른 이름들(AI가 뽑은 display_name·덮어쓰기 전 company_name·별칭)로 차례로 다시 조회한다.
    점수를 찾거나 오류가 나면 거기서 멈춘다. 이름마다 검증은 똑같이 적용되고, 아는 이름은 검증·유사도
    비교에서도 인정된다.

    Returns:
        JobplanetResult:
            - score/review_count가 None이면 미확인 (소규모·미등록 회사)
            - source="error" 면 네트워크 오류 (호출 측에서 무시해도 됨)
    """
    all_known = [company_name] + list(known_names or [])
    result = JobplanetResult(None, None, "not_found")
    for name in _search_names(company_name, known_names):
        result = await _fetch_one(name, all_known)
        if result.source != "not_found":
            if name != company_name and result.score is not None:
                logger.info("잡플래닛 %s(으)로 조회해 찾음 (company_name %s로는 못 찾음)", name, company_name)
            return result
    return result


async def _fetch_one(company_name: str, known_names: list[str | None] | None = None) -> JobplanetResult:
    """이름 하나로 Gemini 검색 연결 → Naver → DuckDuckGo 순서로 조회한다."""
    # 괄호 내 영문 제거 — "딥파인(DEEP.FINE)" → "딥파인" 으로 검색
    search_name = re.sub(r"\([^)]*\)", "", company_name).strip() or company_name

    # 0차: Gemini 구글 검색 연결 (키가 없거나 실패하면 None → 아래 경로로)
    result = await _search_gemini(search_name, known_names)
    if result:
        return result

    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=15, follow_redirects=True) as client:
            # 1차: Naver
            candidates = await _search_naver(search_name, client)
            result = _best_candidate(search_name, candidates, known_names)
            if result:
                return result
            logger.info("잡플래닛 Naver 후보 %d건, 일치 없음 (%s)", len(candidates), search_name)

            # 2차: DuckDuckGo fallback
            candidates = await _search_ddg(search_name, client)
            result = _best_candidate(search_name, candidates, known_names)
            if result:
                return result
            logger.info("잡플래닛 DuckDuckGo 후보 %d건, 일치 없음 (%s)", len(candidates), search_name)

    except Exception as e:
        logger.warning("잡플래닛 조회 오류 (%s): %s: %s", search_name, type(e).__name__, e)
        return JobplanetResult(None, None, "error", str(e))

    return JobplanetResult(None, None, "not_found")


if __name__ == "__main__":
    import asyncio

    # 링크 없는 평점 블록(4.9/999, 가짜)이 다음 카드의 "(주) 카카오" 링크를
    # 가로채 틀린 점수로 채택되는 걸 막는 회귀 테스트(네트워크 불필요)
    _CROSS_CARD_FIXTURE = """
    <div class="container">
      <div class="card">
        <div class="fds-listitem">평점 4.9/5 999 참여</div>
      </div>
      <div class="card">
        <a href="x">(주) 카카오 기업정보 - 산업: ...</a>
        <div class="fds-listitem">평점 3.8/5 1,307 참여</div>
      </div>
    </div>
    """
    _fixture_candidates = _extract_naver_candidates(_CROSS_CARD_FIXTURE)
    assert _fixture_candidates == [(3.8, 1307, "(주) 카카오 기업정보")], _fixture_candidates
    print("교차 카드 fixture:", _fixture_candidates)

    # Gemini 답 읽기(네트워크 불필요): 정상 / 천 단위 쉼표 / 부서·다른 회사 / 없음 / 리뷰 0건
    _ok, _ = _read_gemini_answer("회사명=(주)더스윙, 평점=2.4, 리뷰수=1,110", "더스윙")
    assert _ok and (_ok.score, _ok.review_count, _ok.source) == (2.4, 1110, "gemini_search"), _ok
    for _text, _name in [
        ("회사명=(주)더스윙오퍼레이션, 평점=1.9, 리뷰수=11", "더스윙"),
        ("회사명=카카오뱅크, 평점=3.7, 리뷰수=500", "카카오"),
        ("없음", "더스윙"),
        ("회사명=(주)스윙부스, 평점=0.0, 리뷰수=0", "스윙부스"),
    ]:
        _bad, _why = _read_gemini_answer(_text, _name)
        assert _bad is None and _why, (_text, _bad)
    print("Gemini 답 읽기 fixture 통과")

    # 아는 다른 이름(display_name 등)과의 일치 — 구글이 조회한 이름이 아니라 잡플래닛 등록명을 돌려주는 경우
    _MILLIE = "회사명=(주)밀리의서재, 평점=3.2, 리뷰수=143"
    _known_ok, _ = _read_gemini_answer(_MILLIE, "케이티밀리의서재", ["밀리의서재"])
    assert _known_ok and (_known_ok.score, _known_ok.review_count) == (3.2, 143), _known_ok
    _known_ok2, _ = _read_gemini_answer(_MILLIE, "케이티밀리의서재", ["주식회사 케이티 밀리의서재", None, "(주)밀리의서재"])
    assert _known_ok2 and _known_ok2.score == 3.2  # 목록에 None·다른 모양이 섞여 있어도
    for _args in [
        (_MILLIE, "케이티밀리의서재"),                                                        # 아는 이름이 없으면 기존처럼 거절
        (_MILLIE, "케이티밀리의서재", ["다른회사이름"]),                                        # 다른 이름이면 거절
        ("회사명=(주)더스윙오퍼레이션, 평점=1.9, 리뷰수=11", "더스윙", ["SWING"]),              # 계열사는 아는 이름이 있어도 거절
        ("회사명=삼성, 평점=3.5, 리뷰수=999", "삼성SDS", ["삼성"]),                           # 정규화 3글자 미만의 아는 이름은 안 씀
        ("회사명=(주)밀리의서재, 평점=0.0, 리뷰수=0", "케이티밀리의서재", ["밀리의서재"]),         # 아는 이름이어도 리뷰 0건은 거절
    ]:
        _bad2, _why2 = _read_gemini_answer(*_args)
        assert _bad2 is None and _why2, (_args, _bad2)
    # 네이버·DuckDuckGo 후보 비교: 아는 이름이 있으면 그 이름과의 유사도도 본다
    _snippet = [(3.2, 143, "(주) 밀리의서재 기업정보")]
    assert _best_candidate("케이티밀리의서재", _snippet) is None
    _cand = _best_candidate("케이티밀리의서재", _snippet, ["밀리의서재"])
    assert _cand and (_cand.score, _cand.source) == (3.2, "search_snippet"), _cand
    assert _best_candidate("케이티밀리의서재", _snippet, ["밀리"]) is None  # 3글자 미만은 안 씀
    print("아는 이름 fixture 통과")

    # 검색할 이름 후보: company_name 먼저, 중복·2글자 미만·None 제외
    assert _search_names("롯데그룹", ["롯데이노베이트", "(주)롯데그룹", None, "LOTTE", "롯", "롯데"]) == ["롯데그룹", "롯데이노베이트", "LOTTE", "롯데"]
    assert _search_names("에스케이그룹", ["SK"]) == ["에스케이그룹", "SK"]  # 두 글자 이름도 후보
    assert _search_names("TVING", ["티빙", "(주)티빙"]) == ["TVING", "티빙"]
    assert _search_names("토스", ["토스"]) == ["토스"]
    # 앞 이름이 not_found면 다음 이름을 시도하고, 찾으면 멈춘다(네트워크 대신 가짜 조회)
    _real_fetch_one = _fetch_one
    _calls = []

    async def _fake_fetch_one(name, known=None):
        _calls.append(name)
        if name == "에스케이그룹":
            return JobplanetResult(None, None, "not_found")
        if name == "SK에너지":
            return JobplanetResult(None, None, "error", "x")
        return JobplanetResult(3.6, 114, "gemini_search")

    _fetch_one = _fake_fetch_one
    try:
        _r = asyncio.run(fetch_jobplanet_score("에스케이그룹", ["SK그룹", "SK에너지"]))
        assert _calls == ["에스케이그룹", "SK그룹"] and _r.score == 3.6, (_calls, _r.score)
        _calls.clear()
        _r = asyncio.run(fetch_jobplanet_score("에스케이그룹", ["SK에너지", "SK그룹"]))
        assert _calls == ["에스케이그룹", "SK에너지"] and _r.source == "error", (_calls, _r.source)  # 오류는 바로 돌려줌
        _calls.clear()
        _r = asyncio.run(fetch_jobplanet_score("에스케이그룹", []))
        assert _calls == ["에스케이그룹"] and _r.source == "not_found", (_calls, _r.source)  # 후보가 없으면 not_found
    finally:
        _fetch_one = _real_fetch_one
    print("이름 후보·다음 이름 시도 fixture 통과")

    async def _check():
        # 카카오: title JSON 절단 방식이었다면 not_found로 오탐했을 사례
        kakao = await fetch_jobplanet_score("카카오")
        assert kakao.source in ("gemini_search", "search_snippet"), f"카카오: {kakao.source}"
        assert kakao.score is not None and kakao.score > 0
        print("카카오:", kakao.score, kakao.review_count, kakao.source)

        # 에너닷: 소규모 회사, 예전에도 되던 케이스라 회귀 확인용
        enerdat = await fetch_jobplanet_score("에너닷")
        assert enerdat.source in ("gemini_search", "search_snippet"), f"에너닷: {enerdat.source}"
        print("에너닷:", enerdat.score, enerdat.review_count, enerdat.source)

        # 모회사 접두가 붙은 이름: 구글이 등록명(밀리의서재)을 돌려줘도 아는 이름(display_name)이 있으면 채택
        millie = await fetch_jobplanet_score("(주)케이티밀리의서재", ["밀리의서재"])
        assert millie.source in ("gemini_search", "search_snippet") and millie.score, f"밀리의서재: {millie.source}"
        print("밀리의서재(아는 이름):", millie.score, millie.review_count, millie.source)

        # 존재하지 않는 회사: false positive 없이 not_found여야 함
        none_result = await fetch_jobplanet_score("존재하지않는가상의회사이름ABCXYZ123")
        assert none_result.source == "not_found", f"미등록 회사인데: {none_result.source}"
        print("미등록 회사:", none_result.source)

    asyncio.run(_check())
    print("jobplanet self-check 통과")
