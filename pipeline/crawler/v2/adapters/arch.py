"""S-arch: 건축대학 자체 사이트(arch.mju.ac.kr) Velocity(.vm) 게시판 어댑터.

건축학·전통건축·공간디자인 3개 학과를 커버하는 단일 카테고리형 게시판.
URL 구조 (2026-07-18 실사이트 실증):
  목록 : https://arch.mju.ac.kr/board/board_list.vm?category={c}&page={n}
  상세 : https://arch.mju.ac.kr/board/board_view.vm?id={id}&category={c}
  첨부 : /upload/board/board_*.{ext} (상세 div.file-area 안 a[download])

카테고리: a=News, b=공지, c=공간디자인, e=학생회, h=채용, n=국제교류
— mju.json params.categories 로 제어.

목록 행 = ul.board-list > li →
  span.title > a[href*=board_view.vm] (제목/id/카테고리), span.date (YYYY.MM.DD),
  span.num (게시판 번호 — 표형에만 존재). 지면마다 표형(div.board-area)과
  썸네일 갤러리형(div.board-thm-area, 예: e=학생회) 두 변형이 있으나 ul.board-list
  구조는 공통. 헤더 내비게이션에도 board_view.vm 링크(Donation 등)가 있으므로
  반드시 ul.board-list 범위로 한정해 파싱한다.

페이지네이션 = GET 파라미터 page (form name=nav 가 hidden input page 로 제출).
페이지바의 goPage('N') 최댓값(맨끝 이동 버튼 포함)이 전체 페이지 수.

incremental = 각 카테고리 첫 페이지만, backfill = ctx["pages"] 상한까지 페이지네이션.
dedup_key = "arch:{category}:{id}" — category는 행 링크의 실제 값 우선(교차 노출 대비).
"""

from __future__ import annotations

import html as html_mod
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from ..core.model import make_notice
from .base import HAVE_BS4, soup, strip_tags, take_detail

DEFAULT_HOST = "arch.mju.ac.kr"
DEFAULT_CATEGORIES = ["b", "e", "h", "n"]

GOPAGE_RE = re.compile(r"goPage\('(\d+)'\)")
# 폴백: 목록 행 — span.title 안의 board_view.vm 링크와 뒤따르는 span.date
ROW_FALLBACK_RE = re.compile(
    r'<span class="title">\s*<a href="(board_view\.vm\?[^"]+)"[^>]*>(.*?)</a>'
    r".*?<span class=\"date\">\s*([^<]*?)\s*</span>",
    re.S,
)
# 폴백: 본문 div#board_content — 뒤따르는 고정 블록(file-area/btn-area)을 경계로 절단
BODY_FALLBACK_RE = re.compile(
    r'<div id="board_content"[^>]*>(.*?)(?=<div class="(?:file-area|btn-area)")',
    re.S,
)
FILE_AREA_FALLBACK_RE = re.compile(r'<div class="file-area">(.*?)</div>', re.S)
FILE_LINK_FALLBACK_RE = re.compile(
    r'<a[^>]*href="([^"]+)"[^>]*download="([^"]*)"[^>]*>(.*?)</a>', re.S
)


def _post_ref(href):
    """board_view.vm 링크에서 (id, category) 추출 (실패 시 (None, None))."""
    try:
        q = parse_qs(urlsplit(href.replace("&amp;", "&")).query)
    except ValueError:
        return None, None
    ids = q.get("id")
    cats = q.get("category")
    post_id = ids[0] if ids and ids[0].isdigit() else None
    return post_id, (cats[0] if cats else None)


def _clean_title(raw):
    return re.sub(r"\s+", " ", raw).strip()


def _list_segment(html):
    """정규식 폴백용: 헤더 내비게이션을 제외한 ul.board-list 범위만 절단."""
    start = html.find('class="board-list"')
    if start < 0:
        return html
    end = html.find("</ul>", start)
    return html[start : end if end > 0 else len(html)]


def _parse_list(html, log):
    """목록 HTML → [(title, post_id, category, date, num)] — bs4 우선, 정규식 폴백."""
    rows = []
    if HAVE_BS4:
        doc = soup(html)
        for li in doc.select("ul.board-list > li"):
            a = li.select_one('span.title a[href*="board_view.vm"]')
            if a is None:
                continue
            post_id, cat = _post_ref(a["href"])
            if not post_id:
                continue
            date_node = li.select_one("span.date")
            num_node = li.select_one("span.num")
            rows.append(
                (
                    _clean_title(a.get_text(" ")),
                    post_id,
                    cat,
                    date_node.get_text(strip=True) if date_node else None,
                    num_node.get_text(strip=True) if num_node else None,
                )
            )
    else:
        for href, inner, date in ROW_FALLBACK_RE.findall(_list_segment(html)):
            post_id, cat = _post_ref(href)
            if not post_id:
                continue
            rows.append(
                (
                    _clean_title(html_mod.unescape(strip_tags(inner))),
                    post_id,
                    cat,
                    date or None,
                    None,
                )
            )
    return rows


def _max_page(html):
    """페이지바의 goPage('N') 최댓값 (맨끝 버튼 포함, 없으면 1)."""
    nums = [int(n) for n in GOPAGE_RE.findall(html)]
    return max(nums) if nums else 1


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
        node = doc.select_one("#board_content")
        if node is not None:
            body = node.get_text("\n")
        for a in doc.select("div.file-area a[href]"):
            name = _clean_title(a.get("download") or a.get_text(" "))
            if name:
                attachments.append({"name": name, "url": urljoin(url, a["href"])})
    if body is None:  # 폴백: board_content 블록 정규식 절단
        m = BODY_FALLBACK_RE.search(html)
        if m:
            body = html_mod.unescape(strip_tags(m.group(1)))
        if not attachments:
            seg = FILE_AREA_FALLBACK_RE.search(html)
            for href, dl_name, inner in FILE_LINK_FALLBACK_RE.findall(
                seg.group(1) if seg else ""
            ):
                name = _clean_title(
                    html_mod.unescape(dl_name or strip_tags(inner))
                )
                if name:
                    attachments.append(
                        {"name": name, "url": urljoin(url, href.replace("&amp;", "&"))}
                    )
    return body, attachments


def collect(ctx):
    ch = ctx["channel"]
    p = ch.get("params") or {}
    host = p.get("host") or DEFAULT_HOST
    categories = p.get("categories") or DEFAULT_CATEGORIES
    base = f"https://{host}"
    client = ctx["client"]
    log = ctx["log"]
    school_id = ctx["school"]["id"]
    known = ctx.get("known_keys") or set()

    notices = []
    seen = set()
    for cat in categories:
        list_url = f"{base}/board/board_list.vm?category={cat}"
        try:
            first_html = client.get(list_url)
        except Exception as exc:
            log(f"{ch['key']}: {cat} 목록 1페이지 실패: {exc}")
            continue
        raw_rows = _parse_list(first_html, log)
        if not raw_rows:
            log(f"{ch['key']}: {cat} 목록에서 행 0건 — 마크업 변경 여부 확인 필요")
        if ctx["mode"] != "incremental":  # backfill: 전량 페이지네이션
            max_page = _max_page(first_html)
            limit = ctx["pages"] or max_page
            for page in range(2, min(max_page, limit) + 1):
                try:
                    raw_rows += _parse_list(
                        client.get(f"{list_url}&page={page}"), log
                    )
                except Exception as exc:
                    log(f"{ch['key']}: {cat} {page}페이지 실패: {exc}")
                    break

        for title, post_id, row_cat, date, num in raw_rows:
            cat_eff = row_cat or cat
            dedup_key = f"arch:{cat_eff}:{post_id}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            url = f"{base}/board/board_view.vm?id={post_id}&category={cat_eff}"
            body, attachments = (None, [])
            if dedup_key not in known and take_detail(ctx):
                body, attachments = _fetch_detail(client, url, log)
            extra = {"category": cat_eff}
            if num:
                extra["num"] = num
            try:
                notices.append(
                    make_notice(
                        school_id,
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
                log(f"{ch['key']}: {exc}")
    return notices
