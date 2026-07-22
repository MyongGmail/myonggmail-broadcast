"""S-bizdemo_php: 미래융합대학 학과 PHP 사이트 공통 어댑터 (cafe24 bizdemo 게시판 엔진).

미래융합대학 소속 학과 사이트들이 같은 엔진(cafe24 bizdemo36208 템플릿, EUC-KR,
서버렌더 PHP)을 공유한다. 2026-07-18 실측: mjwelfare.mju.ac.kr(사회복지),
real.mju.ac.kr(부동산) — 마크업·URL 스킴 동일.

URL 구조:
  목록  : https://{host}{path}?{query}      (예: /default/05/01.php?topmenu=5&left=1)
  페이지 : 목록 URL + &com_board_page={n}   (페이지바 com_board_page=N 링크 최댓값 =
          마지막 페이지; 행 내부 링크의 com_board_page= 는 빈 값이라 오탐 없음)
  상세  : {path}?com_board_basic=read_form&com_board_idx={idx}&{query}&com_board_id={id}

목록 마크업(행): td.bbsno(번호|공지) | td.bbsnewf5 > a[href*=com_board_idx] >
span.notice_subject(제목) | td.bbswriter(작성자 — 이미지만 있는 경우 있음) |
td.bbsetc_dateof_write(YYYY-MM-DD). 제목의 <, >는 &lt;&gt;로 이스케이프되어 온다.
com_board_id 는 채널 설정에 없으면 목록 HTML의 행 링크에서 추출.

상세 본문 = div#post_area (바로 뒤에 resizeImage <script>가 따라온다 — 정규식
폴백의 절단 경계). 첨부는 href에 file_download 가 들어가는 앵커(내용은 download.gif
이미지뿐) — 파일명은 같은 div 안의 앞선 <span>에 있다. 고정 공지(bbsno=공지)가
모든 페이지에 반복되므로 seen 집합으로 걸러낸다.

incremental = 각 board 첫 페이지, backfill = ctx["pages"] 상한까지 com_board_page
페이지네이션. dedup_key = "bizdemo:{host}:{board_id}:{idx}".

channel params 예:
  {"host": "mjwelfare.mju.ac.kr",
   "boards": [{"path": "/default/05/01.php",
               "query": {"topmenu": "5", "left": "1"}, "label": "학과공지"}]}
"""

from __future__ import annotations

import html as html_mod
import re
from urllib.parse import urlencode, urljoin

from ..core.model import make_notice
from .base import HAVE_BS4, soup, strip_tags, take_detail

DEFAULT_BOARDS = [
    {"path": "/default/05/01.php", "query": {"topmenu": "5", "left": "1"}, "label": "학과공지"}
]

IDX_RE = re.compile(r"com_board_idx=(\d+)")
BOARD_ID_RE = re.compile(r"com_board_id=(\d+)")
PAGE_RE = re.compile(r"com_board_page=(\d+)")

# 폴백(bs4 부재): 목록 행 — <tr 단위 청크에서 제목 앵커/날짜/작성자 추출
ROW_ANCHOR_RE = re.compile(
    r"<a\s+href=[\"']([^\"']*com_board_idx=\d+[^\"']*)[\"'][^>]*>(.*?)</a>", re.S
)
DATE_CELL_RE = re.compile(r"class=[\"']bbsetc_dateof_write[\"'][^>]*>\s*([0-9.\-]{8,10})", re.S)
WRITER_CELL_RE = re.compile(r"class=[\"']bbswriter[\"'][^>]*>(.*?)</td>", re.S)
# 폴백: 상세 본문 div#post_area — 뒤따르는 <script>(resizeImage)를 경계로 절단
BODY_FALLBACK_RE = re.compile(r"<div id=[\"']post_area[\"'][^>]*>(.*?)</div>\s*<script", re.S)
BODY_LOOSE_RE = re.compile(r"<div id=[\"']post_area[\"'][^>]*>(.*?)</div>", re.S)
# 첨부: <span>파일명</span> 다음에 file_download 앵커(내용은 이미지) — 이름은 span에서
FILE_WITH_NAME_RE = re.compile(
    r"<span[^>]*>([^<]*)</span>\s*<a[^>]+href=[\"']([^\"']*download[^\"']*)[\"']", re.S | re.I
)
FILE_FALLBACK_RE = re.compile(
    r"<a[^>]+href=[\"']([^\"']*download[^\"']*)[\"'][^>]*>(.*?)</a>", re.S | re.I
)


def _clean(raw):
    """태그 제거 + 엔티티 복원 + 공백 정규화. 제목의 &lt;&gt;가 태그로 오인되지 않도록
    strip_tags → unescape 순서를 지킨다."""
    return re.sub(r"\s+", " ", html_mod.unescape(strip_tags(raw))).strip()


def _parse_list(html):
    """목록 HTML → [(title, idx, date, writer)]. bs4 우선, 정규식 폴백."""
    rows = []
    if HAVE_BS4:
        doc = soup(html)
        for td in doc.find_all("td", class_="bbsnewf5"):
            # 행 셀에 <a href=''><a href='…idx…'> 이중 앵커 — idx 있는 쪽만 사용
            a = next((x for x in td.find_all("a", href=True) if IDX_RE.search(x["href"])), None)
            if a is None:
                continue  # 상세 페이지의 이전/목록 버튼 셀도 bbsnewf5 — idx 앵커 기준 제외
            idx = IDX_RE.search(a["href"]).group(1)
            title = re.sub(r"\s+", " ", a.get_text(" ")).strip()
            tr = td.find_parent("tr")
            date = writer = None
            if tr is not None:
                dtd = tr.find("td", class_="bbsetc_dateof_write")
                date = dtd.get_text(strip=True) if dtd else None
                wtd = tr.find("td", class_="bbswriter")
                writer = wtd.get_text(strip=True) if wtd else None  # 이미지면 ""
            rows.append((title, idx, date, writer or None))
    else:
        for chunk in html.split("<tr")[1:]:
            chunk = chunk.split("</tr>")[0]
            m = ROW_ANCHOR_RE.search(chunk)
            if not m:
                continue
            idx = IDX_RE.search(m.group(1)).group(1)
            dm = DATE_CELL_RE.search(chunk)
            wm = WRITER_CELL_RE.search(chunk)
            writer = _clean(wm.group(1)) if wm else None
            rows.append((_clean(m.group(2)), idx, dm.group(1) if dm else None, writer or None))
    return rows


def _fetch_detail(client, url, log):
    """상세 페이지 → (body_text, attachments). 실패해도 raise 하지 않는다."""
    try:
        html = client.get(url)
    except Exception as exc:
        log(f"상세 수집 실패 {url}: {exc}")
        return None, []
    body = None
    attachments = []
    if HAVE_BS4:
        doc = soup(html)
        node = doc.find(id="post_area")
        if node is not None:
            body = node.get_text("\n")
        for a in doc.find_all("a", href=True):
            href = a["href"]
            if "download" not in href.lower() or href.lower().startswith("javascript"):
                continue
            name = re.sub(r"\s+", " ", a.get_text(" ")).strip()
            if not name and a.parent is not None:  # 앵커는 download.gif뿐 — 파일명은 옆 span
                span = a.parent.find("span")
                if span is not None:
                    name = re.sub(r"\s+", " ", span.get_text(" ")).strip()
            attachments.append({"name": name or href, "url": urljoin(url, href)})
    if body is None:
        m = BODY_FALLBACK_RE.search(html) or BODY_LOOSE_RE.search(html)
        if m:
            body = _clean_body_fallback(m.group(1))
        if not attachments:
            found = set()
            for inner, href in FILE_WITH_NAME_RE.findall(html):
                if href.lower().startswith("javascript"):
                    continue
                found.add(href)
                name = _clean(inner)
                attachments.append(
                    {"name": name or href, "url": urljoin(url, href.replace("&amp;", "&"))}
                )
            for href, inner in FILE_FALLBACK_RE.findall(html):
                if href in found or href.lower().startswith("javascript"):
                    continue
                name = _clean(inner)
                attachments.append(
                    {"name": name or href, "url": urljoin(url, href.replace("&amp;", "&"))}
                )
    return body, attachments


def _clean_body_fallback(inner):
    """본문 HTML → 텍스트 (정규식 폴백 전용). <br>/<p>를 줄바꿈으로 보존."""
    inner = re.sub(r"(?i)<br\s*/?>|</p>", "\n", inner)
    return html_mod.unescape(strip_tags(inner))


def _collect_board(ctx, host, board, seen, notices):
    ch = ctx["channel"]
    client = ctx["client"]
    log = ctx["log"]
    known = ctx.get("known_keys") or set()
    path = board.get("path") or "/default/05/01.php"
    qs = urlencode(board.get("query") or {})
    label = board.get("label") or path
    list_url = f"https://{host}{path}" + (f"?{qs}" if qs else "")

    first_html = client.get(list_url)  # 목록 1페이지 실패는 호출부에서 게시판 단위 스킵
    board_id = board.get("board_id")
    if not board_id:
        m = BOARD_ID_RE.search(first_html)  # 행 링크/board.js 쿼리에서 추출
        board_id = m.group(1) if m else path.strip("/").replace("/", "-")
        if m is None:
            log(f"{ch['key']}/{label}: com_board_id 미검출 — 경로 기반 대체키 {board_id!r} 사용")
    raw_rows = _parse_list(first_html)
    if not raw_rows:
        log(f"{ch['key']}/{label}: 목록 0행 — 마크업 변경/오류 페이지 가능성")

    if ctx["mode"] != "incremental":  # backfill: com_board_page 페이지네이션
        max_page = max([int(n) for n in PAGE_RE.findall(first_html)] or [1])
        limit = ctx["pages"] or max_page
        sep = "&" if qs else "?"
        for page in range(2, min(max_page, limit) + 1):
            try:
                raw_rows += _parse_list(client.get(f"{list_url}{sep}com_board_page={page}"))
            except Exception as exc:
                log(f"{ch['key']}/{label}: {page}페이지 실패: {exc}")
                break

    detail_qs = f"&{qs}" if qs else ""
    for title, idx, date, writer in raw_rows:
        dedup_key = f"bizdemo:{host}:{board_id}:{idx}"
        if dedup_key in seen:
            continue  # 고정공지 반복/교차게재 중복
        seen.add(dedup_key)
        url = (
            f"https://{host}{path}?com_board_basic=read_form"
            f"&com_board_idx={idx}{detail_qs}&com_board_id={board_id}"
        )
        body, attachments = (None, [])
        if dedup_key not in known and take_detail(ctx):
            body, attachments = _fetch_detail(client, url, log)
        extra = {"board": label, "board_id": str(board_id)}
        if writer:
            extra["writer"] = writer
        try:
            notices.append(
                make_notice(
                    ctx["school"]["id"],
                    ch["key"],
                    dedup_key,
                    title,
                    url,
                    date=date,
                    body_text=body,
                    category_hint=ch.get("category_hint"),
                    operator=ch.get("operator"),
                    attachments=attachments,
                    extra=extra,
                )
            )
        except ValueError as exc:
            log(f"{ch['key']}/{label}: {exc}")


def collect(ctx):
    ch = ctx["channel"]
    p = ch.get("params") or {}
    log = ctx["log"]
    host = p.get("host")
    if not host:
        log(f"{ch['key']}: params.host 누락 — 수집 불가")
        return []
    boards = p.get("boards") or DEFAULT_BOARDS

    notices = []
    seen = set()
    for board in boards:
        try:
            _collect_board(ctx, host, board, seen, notices)
        except Exception as exc:
            log(f"{ch['key']}/{board.get('label', board.get('path'))}: 게시판 수집 실패: {exc}")
    return notices
