"""S-intern: 현장실습지원센터(intern.mju.ac.kr) 어댑터.

사이트 구조 (2026-07-18 브라우저 실사로 실증):
  메인      : https://intern.mju.ac.kr/main.do
              → <section class="notice"> 안에 3개 게시판 탭이 카드로 서버렌더링됨
                (탭 1개만 화면 노출, 나머지는 hidden — 원본 HTML에는 전부 존재):
                  BD_NO=1   공지사항  5건(고정 3 + 최신 2)
                  BD_NO=168 채용정보  5건
                  BD_NO=2   자료실    3건
                <div class="notice-card" data-bd-no="1" data-bbs-no="294" data-gubun="1">
                (날짜: <div class="date"><span class="day">23</span>…<span>2026.06</span>)
              BBS_NO는 게시판을 가로지르는 단일 시퀀스(전역 유일 — 114~296이 날짜순
              단조 증가로 실증)라 dedup_key에 BD_NO를 섞지 않아도 충돌하지 않는다.
  게시판 목록: /user/Board/comm_notice.do?BD_NO=1   (POST 페이지네이션, csrf_token)
  게시판 상세: /user/Board/comm_notice_view.do?BD_NO={bd}&BBS_NO={id}
              (비로그인 GET 열람 가능 — BD_NO 1·168 모두 동일 경로로 렌더링 확인)

robots.txt 정책 (핵심 제약):
  User-agent: *
  Disallow: /
  Allow: /main.do

  → 사이트 운영자는 크롤러에게 "/main.do만 허용"을 명시했다. 따라서 이 어댑터의
  크롤 표면은 /main.do 하나뿐이며, /user/Board/* 목록·상세는 절대 요청하지 않는다.
  (상세 URL은 요청 없이 레코드의 사람용 링크로만 조립한다 → 본문 수집 불가.)

  주의: stdlib urllib.robotparser는 규칙을 파일 순서대로 첫 일치(first-match)로
  판정해 "Disallow: /"가 항상 이겨 /main.do까지 차단한다. RFC 9309 §2.2.2와 주요
  검색엔진은 가장 구체적인(경로가 긴) 규칙 우선(longest-match)이라 /main.do는 허용
  이다 — 운영자가 "Allow: /main.do"를 써 둔 의도도 그것뿐이다(첫 일치 의미론에서는
  해당 줄이 완전히 죽은 규칙이 된다). 그래서 이 어댑터는 PoliteClient가 차단할 때
  robots.txt를 RFC 9309 최장 일치로 재판정해 '표준 의미론으로도 허용인 URL'만
  재시도한다. 표준 의미론으로도 불허면 그대로 존중하고 빈 리스트를 반환한다.

수집 범위 한계: 메인 카드 = 고정공지 3건 + 최신 2건. 게시글 빈도가 낮아(전체 134건)
incremental 감지에는 대체로 충분하나, 짧은 주기에 3건 이상 올라오면 놓칠 수 있다.
backfill 불가(robots가 목록 페이지 크롤 불허).

dedup_key = "intern:{BBS_NO}".
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlparse, urlunparse

from ..core.http import DisallowedByRobots
from ..core.model import make_notice
from .base import HAVE_BS4, clean_text, soup

VIEW_PATH = "/user/Board/comm_notice_view.do"
MENU_QUERY = "CURRENT_MENU_CODE=MENU0028&TOP_MENU_CODE=MENU0005"

# 실증 마크업 기준 폴백 (bs4 부재 시):
#   <div class="card cursor-pointer notice-card" data-bd-no="1" data-bbs-no="294" data-gubun="1">
#     <div class="date"><span class="day">23</span><br><span>2026.06</span></div>
#     <div class="desc"><p class="tit">제목</p></div>
#   </div>
CARD_FALLBACK_RE = re.compile(
    r'data-bd-no="(\d+)"\s+data-bbs-no="(\d+)"[^>]*>\s*'
    r'<div class="date">\s*<span class="day">\s*(\d{1,2})\s*</span>\s*(?:<br\s*/?>)?\s*'
    r"<span>\s*(\d{4})\.(\d{1,2})\s*</span>\s*</div>\s*"
    r'<div class="desc">\s*<p class="tit">(.*?)</p>',
    re.S,
)
DATE_TEXT_RE = re.compile(r"(\d{1,2})\D+(\d{4})\.(\d{1,2})")


# -------------------------------------------------- robots (RFC 9309 보정)
class _Rfc9309Robots:
    """stdlib RobotFileParser가 파싱한 규칙을 RFC 9309 최장 일치로 재판정하는 뷰.

    PoliteClient의 robots 캐시에 꽂혀 rp.can_fetch(ua, url) 인터페이스로 동작한다.
    와일드카드('*'/'$' 포함 경로)는 미지원 → 그런 규칙이 보이면 stdlib 판정으로
    보수 회귀한다. (이 호스트의 robots.txt에는 와일드카드가 없다.)
    """

    def __init__(self, rp):
        self._rp = rp

    @staticmethod
    def _path_of(url):
        # stdlib can_fetch와 동일한 경로 정규화
        parsed = urlparse(unquote(url))
        path = urlunparse(("", "", parsed.path, parsed.params, parsed.query, ""))
        path = quote(path)
        return path or "/"

    def _entry_for(self, useragent):
        for entry in getattr(self._rp, "entries", []):
            if entry.applies_to(useragent):
                return entry
        return getattr(self._rp, "default_entry", None)

    def can_fetch(self, useragent, url):
        rp = self._rp
        if getattr(rp, "disallow_all", False):
            return False
        if getattr(rp, "allow_all", False):
            return True
        entry = self._entry_for(useragent)
        if entry is None:
            return True
        path = self._path_of(url)
        best_len = -1
        best_allow = True
        for line in entry.rulelines:
            rule_path = line.path
            if rule_path == "*":
                match_len = 0
            elif "*" in rule_path or "$" in rule_path:
                return rp.can_fetch(useragent, url)
            elif path.startswith(rule_path):
                match_len = len(rule_path)
            else:
                continue
            if match_len > best_len or (match_len == best_len and line.allowance and not best_allow):
                best_len = match_len
                best_allow = line.allowance
        return best_allow


def _polite_get(client, host, url, log):
    """client.get() — stdlib first-match 차단 시 RFC 9309로 재판정 후 1회 재시도.

    표준 의미론으로도 불허인 URL은 재시도하지 않고 그대로 예외를 올린다.
    (client._robots는 PoliteClient 내부 캐시 — core를 수정하지 않기 위한
    호스트 한정 접근이며, 구조가 바뀌면 안전하게 차단 상태로 남는다.)
    """
    try:
        return client.get(url)
    except DisallowedByRobots:
        robots_cache = getattr(client, "_robots", None)
        if not isinstance(robots_cache, dict):
            raise
        rp = robots_cache.get(host)
        if rp is None or isinstance(rp, _Rfc9309Robots):
            raise
        view = _Rfc9309Robots(rp)
        if not view.can_fetch(client.user_agent, url):
            raise  # RFC 9309로도 불허 → 사이트 정책 존중
        log(
            "intern: stdlib robotparser(첫 일치)가 'Allow: /main.do'를 사문화 — "
            "RFC 9309 최장 일치로 재판정해 허용 경로만 재시도"
        )
        robots_cache[host] = view
        return client.get(url)


# -------------------------------------------------- 파싱
def _parse_cards(html, log):
    """main.do HTML → [(bd_no, bbs_no, title, date 'YYYY.M.D')]."""
    cards = []
    if HAVE_BS4:
        doc = soup(html)
        for card in doc.select("div.notice-card[data-bbs-no]"):
            bbs_no = card.get("data-bbs-no")
            bd_no = card.get("data-bd-no") or "1"
            tit = card.select_one("p.tit") or card.select_one(".desc p")
            title = re.sub(r"\s+", " ", tit.get_text(" ")).strip() if tit else ""
            if not bbs_no or not title:
                log(f"intern: 카드 파싱 불완전(bbs_no={bbs_no}, title={title!r}) — 건너뜀")
                continue
            date = None
            date_div = card.select_one("div.date")
            if date_div:
                m = DATE_TEXT_RE.search(date_div.get_text(" "))
                if m:
                    date = "%s.%s.%s" % (m.group(2), m.group(3), m.group(1))
            cards.append((bd_no, bbs_no, title, date))
    else:
        for bd_no, bbs_no, day, year, month, title_html in CARD_FALLBACK_RE.findall(html):
            title = clean_text(title_html)
            if not title:
                continue
            cards.append((bd_no, bbs_no, title, "%s.%s.%s" % (year, month, day)))
    return cards


# -------------------------------------------------- collect
def collect(ctx):
    ch = ctx["channel"]
    p = ch.get("params") or {}
    host = p.get("host", "intern.mju.ac.kr")
    base = "https://" + host
    client = ctx["client"]
    log = ctx["log"]
    school_id = ctx["school"]["id"]

    if ctx.get("mode") == "backfill":
        log(f"{ch['key']}: robots.txt가 목록 페이지(/user/Board/*) 크롤을 불허 — backfill 불가, 메인 카드만 수집")

    try:
        html = _polite_get(client, host, base + "/main.do", log)
    except Exception as exc:
        log(f"{ch['key']}: main.do 수집 실패 — {exc}")
        return []

    cards = _parse_cards(html, log)
    if not cards:
        log(f"{ch['key']}: main.do에서 notice-card 미발견 — 마크업 변경 여부 점검 필요")
        return []

    if ctx.get("with_detail"):
        # 상세(/user/Board/comm_notice_view.do)는 robots 불허 경로 → 요청하지 않고
        # 사람용 링크로만 제공. detail_budget도 차감하지 않는다.
        log(f"{ch['key']}: robots.txt가 상세 페이지 크롤을 불허 — 본문 없이 제목·URL·날짜만 수집")

    notices = []
    seen = set()
    for bd_no, bbs_no, title, date in cards:
        dedup_key = f"intern:{bbs_no}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        url = f"{base}{VIEW_PATH}?BD_NO={bd_no}&BBS_NO={bbs_no}&{MENU_QUERY}"
        try:
            notices.append(
                make_notice(
                    school_id,
                    ch["key"],
                    dedup_key,
                    title,
                    url,
                    date=date,
                    category_hint=ch.get("category_hint"),
                    operator=ch.get("operator"),
                    extra={"bd_no": bd_no, "surface": "main.do"},
                )
            )
        except ValueError as exc:
            log(f"{ch['key']}: {exc}")
    return notices
