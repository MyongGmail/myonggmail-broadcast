#!/usr/bin/env python3
"""명지대학교 공식 홈페이지 공지 게시판 크롤러 + 스냅샷 v1.

대상: https://www.mju.ac.kr/mjukr/{menuId}/subview.do (공지 게시판 9종, 목록만 수집)

발견한 URL 구조
  - 목록 1페이지 : GET https://www.mju.ac.kr/mjukr/{menuId}/subview.do
    (풀 페이지. 내부 pageForm의 action에서 게시판 기능번호 fnctNo를 알 수 있음)
  - 목록 N페이지 : GET https://www.mju.ac.kr/bbs/mjukr/{fnctNo}/artclList.do?page=N
    (사이트 JS는 POST pageForm 제출을 쓰지만 GET 쿼리로도 동일하게 동작 확인)
  - 상세        : https://www.mju.ac.kr/bbs/mjukr/{fnctNo}/{artclSeq}/artclView.do

저장(멱등: 같은 입력이면 항상 같은 결과로 덮어씀)
  - 원본 HTML : pipeline/snapshots/raw/{boardId}/list_p{n}.html
  - 파싱 결과 : pipeline/snapshots/parsed/notices.json
  - 리포트    : pipeline/snapshots/REPORT.md

사용법
  python3 pipeline/crawler/crawl_notices.py            # 기본 2페이지/게시판
  python3 pipeline/crawler/crawl_notices.py --pages 3  # 3페이지/게시판
  python3 pipeline/crawler/crawl_notices.py --offline  # 저장된 raw HTML만 재파싱

예의(사이트 부하 방지)
  - 요청 간 최소 1.2초 지연, 명시적 User-Agent, www.mju.ac.kr 외 접근 금지,
    로그인 필요 페이지 접근 안 함, 오류 게시판은 건너뛰고 기록.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

# ---------------------------------------------------------------- 설정

BASE = "https://www.mju.ac.kr"
ALLOWED_HOST = "www.mju.ac.kr"
# 연락처는 **학교 계정**이다. 크롤러 예절(헌법 2조)상 UA에 연락처를 싣는 것은 옳지만,
# 그 주소가 개인 메일이면 학교 서버 로그에 개인 식별자가 남는다. v2(mju.json defaults)는
# 이미 학교 계정을 쓴다 — 두 크롤러가 같은 얼굴로 나가야 한다.
USER_AGENT = (
    "MyongGmailBot/0.1 (+university notice aggregation, student project; "
    "contact: canvas0420@mju.ac.kr) Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
)
REQUEST_DELAY_SEC = 1.2
TIMEOUT_SEC = 20

BOARDS: dict[str, str] = {
    "255": "일반공지",
    "256": "행사공지",
    "257": "학사공지",
    "259": "장학/학자금공지",
    "260": "진로/취업/창업공지",
    "261": "입찰공고",
    "4450": "학칙개정 사전공고",
    "5364": "학생활동공지",
    "8972": "대학안전공지",
}

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAP_DIR = REPO_ROOT / "pipeline" / "snapshots"
RAW_DIR = SNAP_DIR / "raw"
PARSED_DIR = SNAP_DIR / "parsed"
REPORT_PATH = SNAP_DIR / "REPORT.md"

# ---------------------------------------------------------------- HTTP

try:
    import requests

    _SESSION = requests.Session()
    _SESSION.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ko"})

    def http_get(url: str) -> str:
        _check_host(url)
        resp = _SESSION.get(url, timeout=TIMEOUT_SEC)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text

except ImportError:  # requests 미설치 시 표준 라이브러리 폴백
    import urllib.request

    def http_get(url: str) -> str:  # type: ignore[misc]
        _check_host(url)
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ko"}
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return resp.read().decode("utf-8", errors="replace")


def _check_host(url: str) -> None:
    host = urlparse(url).netloc
    if host != ALLOWED_HOST:
        raise RuntimeError(f"허용되지 않은 호스트 접근 차단: {host} ({url})")


_last_request_at = 0.0


def polite_get(url: str) -> str:
    """요청 간 최소 지연을 보장하는 GET."""
    global _last_request_at
    wait = REQUEST_DELAY_SEC - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()
    return http_get(url)


# ---------------------------------------------------------------- 파싱

try:
    from bs4 import BeautifulSoup

    HAVE_BS4 = True
except ImportError:
    HAVE_BS4 = False

FNCT_RE = re.compile(r'name="pageForm"[^>]*action="/bbs/mjukr/(\d+)/artclList\.do"')
PAGE_LINK_RE = re.compile(r"page_link\('(\d+)'\)")
CAMPUS_RE = re.compile(r"^\[(인문|자연)\]")
PREFIX_RE = re.compile(r"^\[([^\[\]]{1,30})\]\s*")

# 정규식 폴백용: 제목 셀 안의 앵커와 그 안쪽 텍스트
ROW_FALLBACK_RE = re.compile(
    r'<a href="(/bbs/mjukr/\d+/\d+/artclView\.do)"[^>]*class="artclLinkView">(.*?)</a>',
    re.S,
)
DATE_FALLBACK_RE = re.compile(r'<td class="_artclTdRdate">\s*([0-9.]+)\s*</td>')
TAG_RE = re.compile(r"<[^>]+>")


def extract_fnct_no(html: str) -> str | None:
    m = FNCT_RE.search(html)
    return m.group(1) if m else None


def extract_max_page(html: str) -> int:
    pages = [int(p) for p in PAGE_LINK_RE.findall(html)]
    return max(pages) if pages else 1


def _clean_title(raw: str) -> str:
    """앵커 내부 텍스트에서 제목만 추출 (머리글 카테고리 표시·'새글' 배지 제거)."""
    text = re.sub(r"\s+", " ", raw).strip()
    text = re.sub(r"\s*새글\s*$", "", text)
    # 고정공지 행은 제목 앞에 "[ 일반공지 ]" 같은 대괄호 카테고리가 붙음 (내부 공백 특징)
    text = re.sub(r"^\[\s+[^\[\]]*?\s+\]\s*", "", text)
    return text.strip()


def parse_list_html(html: str, board_id: str, board_name: str, scraped_at: str):
    """목록 HTML 1개에서 (items, failures) 반환."""
    if HAVE_BS4:
        return _parse_with_bs4(html, board_id, board_name, scraped_at)
    return _parse_with_regex(html, board_id, board_name, scraped_at)


def _make_item(board_id, board_name, title, href, date, scraped_at):
    campus_m = CAMPUS_RE.match(title)
    return {
        "board_id": board_id,
        "board_name": board_name,
        "title": title,
        "url": urljoin(BASE, href),
        "date": date,
        "campus": campus_m.group(1) if campus_m else None,
        "scraped_at": scraped_at,
    }


def _parse_with_bs4(html, board_id, board_name, scraped_at):
    soup = BeautifulSoup(html, "html.parser")
    items, failures = [], []
    for tr in soup.select("tbody tr"):
        title_td = tr.select_one("td._artclTdTitle")
        if title_td is None:  # 헤더/빈 행
            continue
        a = title_td.select_one("a.artclLinkView") or title_td.select_one("a[href]")
        if a is None or not a.get("href"):
            failures.append(
                f"{board_id}/{board_name}: 제목 앵커 없음 — "
                + re.sub(r"\s+", " ", title_td.get_text())[:80]
            )
            continue
        strong = a.select_one("strong")
        title = _clean_title((strong or a).get_text(" "))
        date_td = tr.select_one("td._artclTdRdate")
        date = date_td.get_text(strip=True) if date_td else None
        if not title:
            failures.append(f"{board_id}/{board_name}: 빈 제목 — href={a['href']}")
            continue
        if not date:
            failures.append(f"{board_id}/{board_name}: 날짜 누락 — {title[:60]}")
        items.append(_make_item(board_id, board_name, title, a["href"], date, scraped_at))
    return items, failures


def _parse_with_regex(html, board_id, board_name, scraped_at):
    """bs4 미설치 시 폴백: 앵커/날짜를 순서 기반으로 짝지음(근사)."""
    items, failures = [], []
    anchors = ROW_FALLBACK_RE.findall(html)
    dates = DATE_FALLBACK_RE.findall(html)
    if len(anchors) != len(dates):
        failures.append(
            f"{board_id}/{board_name}: 정규식 폴백 앵커({len(anchors)})/날짜({len(dates)}) 수 불일치"
        )
    for i, (href, inner) in enumerate(anchors):
        title = _clean_title(TAG_RE.sub(" ", inner))
        date = dates[i] if i < len(dates) else None
        if not title:
            failures.append(f"{board_id}/{board_name}: 빈 제목(정규식) — href={href}")
            continue
        items.append(_make_item(board_id, board_name, title, href, date, scraped_at))
    return items, failures


# ---------------------------------------------------------------- 수집

def crawl_board(board_id: str, board_name: str, max_pages: int, scraped_at: str, offline: bool):
    """게시판 1개 수집. dict(결과 요약) 반환."""
    raw_board_dir = RAW_DIR / board_id
    raw_board_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "board_id": board_id,
        "board_name": board_name,
        "fnct_no": None,
        "pages_fetched": 0,
        "max_page_seen": None,
        "items": [],
        "failures": [],
        "error": None,
        "note": None,
    }

    # 1페이지: subview.do (fnctNo 발견용 풀 페이지)
    p1_path = raw_board_dir / "list_p1.html"
    try:
        if offline:
            html1 = p1_path.read_text(encoding="utf-8")
        else:
            html1 = polite_get(f"{BASE}/mjukr/{board_id}/subview.do")
            p1_path.write_text(html1, encoding="utf-8")
    except Exception as exc:  # 게시판 단위 실패는 건너뛰고 기록
        result["error"] = f"1페이지 수집 실패: {exc}"
        return result
    result["pages_fetched"] = 1

    fnct_no = extract_fnct_no(html1)
    result["fnct_no"] = fnct_no
    result["max_page_seen"] = extract_max_page(html1)

    items, fails = parse_list_html(html1, board_id, board_name, scraped_at)
    result["items"].extend(items)
    result["failures"].extend(fails)

    if not items and "_noData" in html1:
        result["note"] = "게시물 없음(빈 게시판)"
        return result

    if fnct_no is None:
        result["failures"].append(f"{board_id}/{board_name}: pageForm에서 fnctNo 미발견 — 1페이지만 수집")
        return result

    # 2페이지 이후: artclList.do?page=N (GET)
    last_page = min(max_pages, result["max_page_seen"] or 1)
    for page in range(2, last_page + 1):
        pn_path = raw_board_dir / f"list_p{page}.html"
        try:
            if offline:
                if not pn_path.exists():
                    break
                htmln = pn_path.read_text(encoding="utf-8")
            else:
                htmln = polite_get(f"{BASE}/bbs/mjukr/{fnct_no}/artclList.do?page={page}")
                pn_path.write_text(htmln, encoding="utf-8")
        except Exception as exc:
            result["failures"].append(f"{board_id}/{board_name}: {page}페이지 수집 실패: {exc}")
            break
        result["pages_fetched"] += 1
        items, fails = parse_list_html(htmln, board_id, board_name, scraped_at)
        result["items"].extend(items)
        result["failures"].extend(fails)

    return result


def dedupe(items: list[dict]) -> list[dict]:
    """고정(headline) 공지가 여러 페이지에 반복되므로 (board_id, url) 기준 중복 제거."""
    seen, out = set(), []
    for it in items:
        key = (it["board_id"], it["url"])
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def leading_prefixes(title: str) -> list[str]:
    """제목 맨 앞의 연속된 [접두어]들을 모두 추출."""
    out, rest = [], title
    while True:
        m = PREFIX_RE.match(rest)
        if not m:
            break
        out.append(m.group(1).strip())
        rest = rest[m.end():]
    return out


# ---------------------------------------------------------------- 리포트

def write_report(results: list[dict], notices: list[dict], max_pages: int, started_at: str):
    total = len(notices)
    prefix_counter = Counter()
    campus_counter = Counter()
    for n in notices:
        campus_counter[n["campus"] or "(없음)"] += 1
        for p in leading_prefixes(n["title"]):
            prefix_counter[p] += 1

    lines = []
    lines.append("# 명지대 공지 게시판 크롤링 리포트 (v1)")
    lines.append("")
    lines.append(f"- 실행 시각(UTC): {started_at}")
    lines.append(f"- 총 수집 건수(중복 제거 후): **{total}건**")
    lines.append(f"- 게시판당 최대 페이지: {max_pages}")
    lines.append(f"- 파서: {'requests + beautifulsoup4' if HAVE_BS4 else 'urllib + 정규식 폴백'}")
    lines.append("")

    lines.append("## 게시판별 수집 건수")
    lines.append("")
    lines.append("| menuId | 게시판 | fnctNo | 수집 페이지 | 전체 페이지(관측) | 수집 건수 | 비고 |")
    lines.append("|---|---|---|---|---|---|---|")
    board_counts = Counter(n["board_id"] for n in notices)
    for r in results:
        remarks = [x for x in (r["error"], r.get("note")) if x]
        if r["failures"]:
            remarks.append(f"경고 {len(r['failures'])}건")
        note = "; ".join(remarks)
        lines.append(
            f"| {r['board_id']} | {r['board_name']} | {r['fnct_no'] or '-'} "
            f"| {r['pages_fetched']} | {r['max_page_seen'] or '-'} "
            f"| {board_counts.get(r['board_id'], 0)} | {note} |"
        )
    lines.append("")

    lines.append("## URL / 페이지네이션 구조")
    lines.append("")
    lines.append("- 목록 1페이지: `GET https://www.mju.ac.kr/mjukr/{menuId}/subview.do`")
    lines.append("  - 메뉴 ID(255 등)와 게시판 기능번호 fnctNo(141 등)는 별개이며,")
    lines.append('    페이지 내 `<form name="pageForm" action="/bbs/mjukr/{fnctNo}/artclList.do">`에서 fnctNo를 얻는다.')
    lines.append("- 페이지네이션: 사이트 JS는 `page_link(n)`이 pageForm(hidden `page`)을 **POST** 제출하지만,")
    lines.append("  `GET /bbs/mjukr/{fnctNo}/artclList.do?page={n}` 으로도 동일한 목록 HTML(부분 페이지)이 반환됨을 확인.")
    lines.append("- 상세(artclView) URL 패턴: `https://www.mju.ac.kr/bbs/mjukr/{fnctNo}/{artclSeq}/artclView.do`")
    lines.append("  (이번 v1에서는 상세 페이지를 수집하지 않고 URL만 기록)")
    lines.append("- 목록 행 구조: `tbody tr > td._artclTdTitle > a.artclLinkView > strong(제목)`,")
    lines.append("  날짜는 `td._artclTdRdate`(YYYY.MM.DD), 고정공지는 `tr.headline` + `_artclTdNum`에 카테고리 라벨.")
    lines.append("- 고정공지가 모든 페이지에 반복 출력되므로 (board_id, url) 기준으로 중복 제거함.")
    lines.append("")

    lines.append("## 캠퍼스 접두어 분포 ([인문]/[자연])")
    lines.append("")
    lines.append("| campus | 건수 |")
    lines.append("|---|---|")
    for campus, cnt in campus_counter.most_common():
        lines.append(f"| {campus} | {cnt} |")
    lines.append("")

    lines.append("## 제목 접두어 분포 (제목 맨 앞 연속 대괄호, 상위 30)")
    lines.append("")
    lines.append("| 접두어 | 건수 |")
    lines.append("|---|---|")
    for prefix, cnt in prefix_counter.most_common(30):
        lines.append(f"| [{prefix}] | {cnt} |")
    lines.append("")

    lines.append("## 파싱 실패 / 경고")
    lines.append("")
    any_fail = False
    for r in results:
        if r["error"]:
            any_fail = True
            lines.append(f"- [오류] {r['board_id']} {r['board_name']}: {r['error']}")
        for f in r["failures"]:
            any_fail = True
            lines.append(f"- [경고] {f}")
    if not any_fail:
        lines.append("- 없음")
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- 메인

def main() -> int:
    ap = argparse.ArgumentParser(description="명지대 공지 게시판 크롤러 v1")
    ap.add_argument("--pages", type=int, default=2, help="게시판당 수집할 최대 페이지 수(1~3)")
    ap.add_argument("--offline", action="store_true", help="네트워크 없이 저장된 raw HTML만 재파싱")
    args = ap.parse_args()
    max_pages = max(1, min(3, args.pages))

    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PARSED_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    for board_id, board_name in BOARDS.items():
        print(f"[{board_id}] {board_name} 수집 중...", flush=True)
        r = crawl_board(board_id, board_name, max_pages, started_at, args.offline)
        status = r["error"] or f"{r['pages_fetched']}페이지, {len(r['items'])}행"
        print(f"  -> {status}", flush=True)
        results.append(r)

    all_items = dedupe([it for r in results for it in r["items"]])
    all_items.sort(key=lambda n: (int(n["board_id"]), n["date"] or "", n["url"]), reverse=False)

    out_path = PARSED_DIR / "notices.json"
    out_path.write_text(
        json.dumps(all_items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_report(results, all_items, max_pages, started_at)

    print(f"\n총 {len(all_items)}건 저장 -> {out_path.relative_to(REPO_ROOT)}")
    print(f"리포트 -> {REPORT_PATH.relative_to(REPO_ROOT)}")
    ok_boards = sum(1 for r in results if not r["error"])
    print(f"게시판 성공 {ok_boards}/{len(BOARDS)}")
    return 0 if ok_boards > 0 and len(all_items) >= 1 else 1


if __name__ == "__main__":
    sys.exit(main())
