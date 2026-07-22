"""그누보드(GNUBOARD5) 게시판 어댑터 — 미래융합대학 future.mju.ac.kr (채널 "future").

URL 구조 (2026-07-18 실측 — 표준 그누보드5 + mjfuture 스킨):
  목록: https://{host}/bbs/board.php?bo_table={bo_table}&page=N
  상세: https://{host}/bbs/board.php?bo_table={bo_table}&wr_id={wr_id}

목록 행 = tr > td.td_subject(제목 앵커, wr_id 포함) + td.td_datetime(날짜).
당일 게시물은 날짜 칸이 "HH:MM"으로 나오므로 오늘 날짜로 치환한다.
상세 본문 = div#bo_v_con, 첨부 = section#bo_v_file a[href*=download.php].
robots.txt는 전체 허용(User-agent: * / Allow:/) 확인됨.

dedup_key = "gnuboard:{bo_table}:{wr_id}"  (예: gnuboard:notice:2382)
"""

from __future__ import annotations

import re
from datetime import datetime

from ..core.model import make_notice
from .base import HAVE_BS4, soup, strip_tags, take_detail

WR_ID_RE = re.compile(r"[?&;]wr_id=(\d+)")
PAGE_NUM_RE = re.compile(r"[?&;]page=(\d+)")
TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}$")

# ---- 정규식 폴백용 (bs4 부재 시) ----
TBODY_RE = re.compile(r"<tbody[^>]*>(.*?)</tbody>", re.S)
ROW_ANCHOR_RE = re.compile(
    r'<a href="([^"]*board\.php\?[^"]*wr_id=\d+[^"]*)"[^>]*>(.*?)</a>', re.S
)
DATE_CELL_RE = re.compile(r'<td class="td_datetime[^"]*"[^>]*>\s*([^<]*?)\s*</td>')
BODY_RE = re.compile(r'<div id="bo_v_con">(.*?)<!--\s*}\s*본문 내용 끝', re.S)
PG_WRAP_RE = re.compile(r'<nav class="pg_wrap">(.*?)</nav>', re.S)


def _clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _fix_date(raw):
    """그누보드는 당일 글을 'HH:MM'으로 표기 → 오늘 날짜로 치환."""
    if raw and TIME_ONLY_RE.match(raw.strip()):
        return datetime.now().strftime("%Y-%m-%d")
    return raw


def _parse_list(html, log):
    """목록 HTML → [(title, wr_id, date, category)] — bs4 우선, 정규식 폴백."""
    rows = []
    if HAVE_BS4:
        doc = soup(html)
        for tr in doc.select("tbody tr"):
            td = tr.select_one("td.td_subject")
            if td is None:
                continue
            link = None
            for a in td.select("a[href]"):
                if WR_ID_RE.search(a["href"]):
                    link = a
                    break
            if link is None:
                continue
            m = WR_ID_RE.search(link["href"])
            cate = td.select_one("a.bo_cate_link")
            date_td = tr.select_one("td.td_datetime")
            rows.append(
                (
                    _clean(link.get_text(" ")),
                    m.group(1),
                    date_td.get_text(strip=True) if date_td else None,
                    _clean(cate.get_text(" ")) if cate else None,
                )
            )
    else:
        tb = TBODY_RE.search(html)
        scope = tb.group(1) if tb else html
        anchors = ROW_ANCHOR_RE.findall(scope)
        dates = DATE_CELL_RE.findall(scope)
        for i, (href, inner) in enumerate(anchors):
            m = WR_ID_RE.search(href)
            if not m:
                continue
            rows.append(
                (
                    _clean(strip_tags(inner)),
                    m.group(1),
                    dates[i] if i < len(dates) else None,
                    None,
                )
            )
    return rows


def _fetch_detail(client, url, log):
    """상세 페이지 → (body_text, attachments)."""
    try:
        html = client.get(url)
    except Exception as exc:
        log(f"상세 수집 실패 {url}: {exc}")
        return None, []
    body = None
    attachments = []
    if HAVE_BS4:
        doc = soup(html)
        for a in doc.select('#bo_v_file a[href*="download.php"]'):
            name = _clean(a.get_text(" "))
            if name:
                attachments.append({"name": name, "url": a["href"]})
        node = doc.select_one("#bo_v_con")
        if node is not None:
            body = node.get_text("\n")
    if body is None:  # 폴백: 본문 블록을 정규식으로 절단
        m = BODY_RE.search(html)
        if m:
            body = strip_tags(m.group(1))
    return body, attachments


def _max_page(html):
    pg = PG_WRAP_RE.search(html)
    if not pg:
        return 1
    nums = [int(n) for n in PAGE_NUM_RE.findall(pg.group(1))]
    return max(nums) if nums else 1


def collect(ctx):
    ch = ctx["channel"]
    p = ch["params"]
    host = p["host"]
    bo_table = p.get("bo_table", "notice")
    list_url = f"https://{host}/bbs/board.php?bo_table={bo_table}"
    client = ctx["client"]
    log = ctx["log"]
    school_id = ctx["school"]["id"]
    known = ctx.get("known_keys") or set()

    try:
        first_html = client.get(list_url)
    except Exception as exc:
        log(f"{ch['key']}: 목록 수집 실패 {list_url}: {exc}")
        return []

    raw_rows = _parse_list(first_html, log)
    if not raw_rows:
        # 사전조사에서 봇 UA에 빈 목록을 준 정황 — page 명시 재시도 1회
        log(f"{ch['key']}: 목록 0건(응답 {len(first_html)}b) — page=1 재시도")
        try:
            raw_rows = _parse_list(client.get(f"{list_url}&page=1"), log)
        except Exception as exc:
            log(f"{ch['key']}: page=1 재시도 실패: {exc}")
        if not raw_rows:
            log(f"{ch['key']}: 목록 크롤 불가 — 빈 리스트 반환 (대안 필요)")
            return []

    if ctx["mode"] != "incremental":  # backfill: 전량 페이지네이션
        limit = ctx["pages"] or _max_page(first_html)
        for page in range(2, min(_max_page(first_html), limit) + 1):
            try:
                raw_rows += _parse_list(client.get(f"{list_url}&page={page}"), log)
            except Exception as exc:
                log(f"{ch['key']}: {page}페이지 실패: {exc}")
                break

    notices = []
    seen = set()
    for title, wr_id, date, category in raw_rows:
        dedup_key = f"gnuboard:{bo_table}:{wr_id}"
        if dedup_key in seen:  # 상단 고정공지는 본래 위치에도 중복 노출됨
            continue
        seen.add(dedup_key)
        url = f"{list_url}&wr_id={wr_id}"  # sca/page 파라미터 제거한 정규 URL
        body, attachments = (None, [])
        if dedup_key not in known and take_detail(ctx):
            body, attachments = _fetch_detail(client, url, log)
        try:
            notices.append(
                make_notice(
                    school_id,
                    ch["key"],
                    dedup_key,
                    title,
                    url,
                    date=_fix_date(date),
                    body_text=body,
                    category_hint=ch.get("category_hint"),
                    operator=ch.get("operator"),
                    attachments=attachments,
                    extra={"bo_table": bo_table, "category": category},
                )
            )
        except ValueError as exc:
            log(f"{ch['key']}: {exc}")
    return notices
