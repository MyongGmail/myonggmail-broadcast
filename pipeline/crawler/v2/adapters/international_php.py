"""S: 국제교류처 자체 PHP 게시판 어댑터 (international.mju.ac.kr).

URL 구조 (2026-07-18 실측):
  목록 : https://international.mju.ac.kr/notice/list.php?sMenu={sMenu}&tname={tname}
         페이지네이션은 &pagestartnum=20*(n-1) (페이지당 20건 + 상단 고정공지)
  상세 : https://international.mju.ac.kr/notice/view.php?sMenu={sMenu}&tname={tname}&itemnum={글id}
  첨부 : globaluniv.mycafe24.com/mju/download.php?... (URL만 기록, 요청하지 않음)

특징:
- 응답의 모든 링크에 PHPSESSID가 붙지만 없어도 동작 → 정규 URL에서는 제거.
- 없는 페이지는 404 대신 enter.mju.ac.kr/error/error.php 로 리다이렉트되어 200이 온다
  → 목록 테이블(table.main_cmnct) 부재로 감지해 0건 처리.
- 여러 sMenu(kor61/67/68)가 같은 tname(info) 테이블을 공유하고 itemnum은 tname 안에서
  유일하므로 dedup_key는 sMenu가 아닌 tname 기준: "intl:{tname}:{itemnum}".
- 인코딩은 UTF-8(meta charset) — PoliteClient가 처리.

기본 커버 게시판(목록 페이지 메뉴에서 확인, params["boards"]로 재정의 가능):
  kor23/enter  국제교류처 선발 공지 (파견·교환 선발)
  kor32/notice 공지사항
  kor61/info   교내공지 (외국인 유학생 대상)
"""

from __future__ import annotations

import re

from ..core.model import make_notice
from .base import HAVE_BS4, soup, take_detail

DEFAULT_HOST = "international.mju.ac.kr"
DEFAULT_BOARDS = [
    {"sMenu": "kor23", "tname": "enter", "label": "국제교류처 선발 공지"},
    {"sMenu": "kor32", "tname": "notice", "label": "공지사항"},
    {"sMenu": "kor61", "tname": "info", "label": "교내공지"},
]

ITEMNUM_RE = re.compile(r"[?&]itemnum=(\d+)")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
PAGESTART_RE = re.compile(r"pagestartnum=(\d+)")
# 목록 행: <a href='view.php?...itemnum=NNN...'>&nbsp;&nbsp;<b>제목</b></a> (작은따옴표 속성)
ROW_ANCHOR_RE = re.compile(
    r"<a\s+href=['\"](view\.php\?[^'\"]*itemnum=\d+[^'\"]*)['\"][^>]*>(.*?)</a>", re.S
)
# 상세 본문: <div class='alert alert-success' ...>본문</div> 다음에 BACK 버튼 블록
ALERT_RE = re.compile(
    r"<div class=['\"]alert[^>]*>(.*?)</div>\s*<div class=['\"]text-center['\"]>", re.S
)
DOWNLOAD_RE = re.compile(
    r"<a\s+href=['\"](https?://[^'\"]*download\.php[^'\"]*)['\"][^>]*>(.*?)</a>", re.S
)


def _clean(text):
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


# 이 게시판은 제목의 <>를 이스케이프하지 않고 그대로 내보낸다(예: "<다음학기 장학금 신청방법>").
# base.strip_tags는 그런 제목을 태그로 오인해 삭제하므로, ASCII 문자로 시작하는
# 진짜 HTML 태그만 제거한다(html.parser의 동작과 동일).
ASCII_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def _strip_ascii_tags(html):
    return ASCII_TAG_RE.sub(" ", html or "")


def _parse_list_bs4(html):
    """목록 HTML → [(title, itemnum, date)] | None(게시판 표 자체가 없음 = 오류 페이지)."""
    doc = soup(html)
    table = doc.select_one("table.main_cmnct") or doc.select_one("table.table")
    if table is None:
        return None
    rows = []
    for tr in table.select("tr"):
        anchors = [a for a in tr.select("a[href]") if ITEMNUM_RE.search(a.get("href") or "")]
        if not anchors:
            continue
        m = ITEMNUM_RE.search(anchors[0]["href"])
        title = _clean(anchors[0].get_text(" "))
        # 행의 마지막 앵커(작성일 칸)가 날짜 — 아니면 행 텍스트에서 마지막 날짜 패턴
        date = None
        last_text = anchors[-1].get_text(strip=True)
        if DATE_RE.fullmatch(last_text):
            date = last_text
        else:
            found = DATE_RE.findall(tr.get_text(" "))
            date = found[-1] if found else None
        rows.append((title, m.group(1), date))
    return rows


def _parse_list_regex(html):
    """bs4 없는 환경 폴백. <tr 단위로 잘라 행마다 제목/글id/날짜 추출."""
    rows = []
    for chunk in html.split("<tr")[1:]:
        chunk = chunk.split("</tr>")[0]
        m = ROW_ANCHOR_RE.search(chunk)
        if not m:
            continue
        num = ITEMNUM_RE.search(m.group(1))
        title = _clean(_strip_ascii_tags(m.group(2)))
        dates = DATE_RE.findall(chunk)
        rows.append((title, num.group(1), dates[-1] if dates else None))
    return rows


def _parse_list(html):
    if HAVE_BS4:
        rows = _parse_list_bs4(html)
        if rows is not None:
            return rows
        return []  # 표 부재 — 오류 페이지 리다이렉트로 판단
    return _parse_list_regex(html)


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
        wrap = doc.select_one("div.form-bg-w3ls") or doc
        node = wrap.select_one("div.alert")
        if node is not None:
            body = node.get_text("\n")
        for a in wrap.select("a[href*='download.php']"):
            href = a.get("href")
            if not href:
                continue
            name = _clean(a.get_text(" "))
            attachments.append({"name": name or href, "url": href})
    if body is None:
        m = ALERT_RE.search(html)
        if m:
            body = _strip_ascii_tags(m.group(1))
        if not attachments:
            for href, inner in DOWNLOAD_RE.findall(html):
                name = _clean(_strip_ascii_tags(inner))
                attachments.append({"name": name or href, "url": href})
    return body, attachments


def _collect_board(ctx, base, board, seen, notices):
    ch = ctx["channel"]
    client = ctx["client"]
    log = ctx["log"]
    known = ctx.get("known_keys") or set()
    smenu = board["sMenu"]
    tname = board["tname"]
    label = board.get("label") or f"{smenu}/{tname}"
    list_url = f"{base}list.php?sMenu={smenu}&tname={tname}"

    html = client.get(list_url)  # 목록 1페이지 실패는 호출부에서 잡아 게시판 단위 스킵
    raw_rows = _parse_list(html)
    if not raw_rows:
        log(f"{ch['key']}/{label}: 목록 0행 — 게시판 이동/오류 페이지 가능성")

    if ctx["mode"] == "backfill":
        limit = ctx.get("pages") or 0
        fetched = 1
        done = {0}
        pending = sorted({int(n) for n in PAGESTART_RE.findall(html)} - {0})
        while pending and not (limit and fetched >= limit):
            off = pending.pop(0)
            if off in done:
                continue
            done.add(off)
            try:
                page_html = client.get(f"{list_url}&pagestartnum={off}")
            except Exception as exc:
                log(f"{ch['key']}/{label}: pagestartnum={off} 실패: {exc}")
                break
            fetched += 1
            more = _parse_list(page_html)
            if not more:
                break
            raw_rows += more
            for n in PAGESTART_RE.findall(page_html):
                n = int(n)
                if n and n not in done and n not in pending:
                    pending.append(n)
            pending.sort()

    for title, itemnum, date in raw_rows:
        dedup_key = f"intl:{tname}:{itemnum}"
        if dedup_key in seen:
            continue  # 고정공지/교차게재 중복
        seen.add(dedup_key)
        url = f"{base}view.php?sMenu={smenu}&tname={tname}&itemnum={itemnum}"
        body, attachments = (None, [])
        if dedup_key not in known and take_detail(ctx):
            body, attachments = _fetch_detail(client, url, log)
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
                    extra={"sMenu": smenu, "tname": tname, "board": label},
                )
            )
        except ValueError as exc:
            log(f"{ch['key']}/{label}: {exc}")


def collect(ctx):
    ch = ctx["channel"]
    p = ch.get("params") or {}
    host = p.get("host", DEFAULT_HOST)
    scheme = p.get("scheme", "https")
    boards = (p.get("boards") or DEFAULT_BOARDS)[:3]
    base = f"{scheme}://{host}/notice/"
    log = ctx["log"]

    notices = []
    seen = set()
    for board in boards:
        try:
            _collect_board(ctx, base, board, seen, notices)
        except Exception as exc:
            log(f"{ch['key']}/{board.get('label', board.get('sMenu'))}: 게시판 수집 실패: {exc}")
    return notices
