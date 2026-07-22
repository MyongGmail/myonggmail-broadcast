"""S1: 명지대 K2Web CMS 게시판 어댑터 (~75개 게시판을 설정만으로 커버).

URL 구조 (v1 스파이크 + 2026-07-18 전수조사에서 실증):
  목록 1p : https://www.mju.ac.kr/{site}/{menu_id}/subview.do  (fnctNo 발견용)
  목록 Np : https://www.mju.ac.kr/bbs/{site}/{fnctNo}/artclList.do?page=N
  RSS     : https://www.mju.ac.kr/bbs/{site}/{fnctNo}/rssList.do?row=50
  상세    : https://www.mju.ac.kr/bbs/{site}/{fnctNo}/{artclSeq}/artclView.do

모든 사이트(부속기관·학과 서브도메인 포함)가 www.mju.ac.kr 경로로 접근 가능하므로
호스트는 www 하나로 통일한다(서브도메인 미러 중복도 자동 해소).
dedup_key = "k2web:{site}:{fnctNo}:{artclSeq}".
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

from ..core.model import make_notice
from .base import HAVE_BS4, soup, strip_tags, take_detail

BASE = "https://www.mju.ac.kr"

FNCT_RE_TMPL = r'name="pageForm"[^>]*action="/bbs/%s/(\d+)/artclList\.do"'
PAGE_LINK_RE = re.compile(r"page_link\('(\d+)'\)")
VIEW_HREF_RE = re.compile(r"/bbs/([^/]+)/(\d+)/(\d+)/artclView\.do")
ROW_FALLBACK_RE = re.compile(
    r'<a href="(/bbs/[^/]+/\d+/\d+/artclView\.do[^"]*)"[^>]*class="artclLinkView">(.*?)</a>', re.S
)
DATE_FALLBACK_RE = re.compile(r'<td class="_artclTdRdate">\s*([0-9.]+)\s*</td>')


def _clean_title(raw):
    text = re.sub(r"\s+", " ", raw).strip()
    text = re.sub(r"\s*새글\s*$", "", text)
    text = re.sub(r"^\[\s+[^\[\]]*?\s+\]\s*", "", text)  # 고정공지 카테고리 배지
    return text.strip()


def _dedup_key(href):
    m = VIEW_HREF_RE.search(href)
    if not m:
        return None
    return "k2web:%s:%s:%s" % m.groups(), m.groups()


def _parse_list(html, log):
    """목록 HTML → [(title, view_url, date)] — bs4 우선, 정규식 폴백."""
    rows = []
    if HAVE_BS4:
        doc = soup(html)
        for tr in doc.select("tbody tr"):
            td = tr.select_one("td._artclTdTitle")
            if td is None:
                continue
            a = td.select_one("a.artclLinkView") or td.select_one("a[href]")
            if a is None or not a.get("href"):
                continue
            strong = a.select_one("strong")
            title = _clean_title((strong or a).get_text(" "))
            date_td = tr.select_one("td._artclTdRdate")
            rows.append((title, a["href"], date_td.get_text(strip=True) if date_td else None))
    else:
        anchors = ROW_FALLBACK_RE.findall(html)
        dates = DATE_FALLBACK_RE.findall(html)
        for i, (href, inner) in enumerate(anchors):
            rows.append((_clean_title(strip_tags(inner)), href, dates[i] if i < len(dates) else None))
    return rows


def _parse_rss(xml_text):
    """rssList.do → [(title, link, pubDate)]. K2Web RSS에 제어문자가 섞이는 경우가
    있어(일반공지 등) XML 파싱 전에 새니타이즈한다."""
    xml_text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", xml_text)
    root = ET.fromstring(xml_text)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or item.findtext("dc:date") or "").strip()
        if title and link:
            out.append((title, link, pub))
    return out


def _fetch_detail(client, url, log):
    """상세 페이지 → (body_text, attachments)."""
    try:
        html = client.get(url)
    except Exception as exc:
        log(f"상세 수집 실패 {url}: {exc}")
        return None, []
    attachments = []
    body = None
    if HAVE_BS4:
        doc = soup(html)
        for a in doc.select('a[href*="/download.do"]'):
            name = re.sub(r"\s+", " ", a.get_text(" ")).strip()
            if name:
                attachments.append({"name": name, "url": urljoin(BASE, a["href"])})
        node = doc.select_one("div.artclView") or doc.select_one("div._artclContent")
        if node is not None:
            body = node.get_text("\n")
    if body is None:  # 폴백: artclView 블록을 정규식으로 절단
        m = re.search(r'<div class="artclView"[^>]*>(.*?)</div>\s*<!--', html, re.S)
        if m:
            body = strip_tags(m.group(1))
    return body, attachments


def _discover_fnct(client, site, menu_id, log):
    html = client.get(f"{BASE}/{site}/{menu_id}/subview.do")
    m = re.search(FNCT_RE_TMPL % re.escape(site), html)
    if not m:
        log(f"{site}/{menu_id}: pageForm에서 fnctNo 미발견")
        return None, html
    return m.group(1), html


def collect(ctx):
    ch = ctx["channel"]
    p = ch["params"]
    site = p["site"]
    client = ctx["client"]
    log = ctx["log"]
    school_id = ctx["school"]["id"]
    known = ctx.get("known_keys") or set()

    fnct_no = p.get("fnct_no")
    first_html = None
    if not fnct_no:
        fnct_no, first_html = _discover_fnct(client, site, p["menu_id"], log)
        if not fnct_no:
            return []

    raw_rows = []  # (title, href, date)
    if ctx["mode"] == "incremental":
        # 1차: RSS (요청 1회로 최신 50건 감지)
        try:
            rss = _parse_rss(client.get(f"{BASE}/bbs/{site}/{fnct_no}/rssList.do?row=50"))
            raw_rows = [(t, l, d) for (t, l, d) in rss]
        except Exception as exc:
            log(f"{ch['key']}: RSS 실패({exc}) — HTML 목록 폴백")
        if not raw_rows:
            html = first_html or client.get(f"{BASE}/{site}/{p.get('menu_id', '')}/subview.do")
            raw_rows = _parse_list(html, log)
            try:
                raw_rows += _parse_list(
                    client.get(f"{BASE}/bbs/{site}/{fnct_no}/artclList.do?page=2"), log
                )
            except Exception as exc:
                log(f"{ch['key']}: 2페이지 폴백 실패: {exc}")
    else:  # backfill: 전량 페이지네이션
        if first_html is None:
            first_html = client.get(f"{BASE}/{site}/{p.get('menu_id', '')}/subview.do")
        raw_rows = _parse_list(first_html, log)
        max_page = max([int(n) for n in PAGE_LINK_RE.findall(first_html)] or [1])
        limit = ctx["pages"] or max_page
        for page in range(2, min(max_page, limit) + 1):
            try:
                raw_rows += _parse_list(
                    client.get(f"{BASE}/bbs/{site}/{fnct_no}/artclList.do?page={page}"), log
                )
            except Exception as exc:
                log(f"{ch['key']}: {page}페이지 실패: {exc}")
                break

    notices = []
    seen = set()
    for title, href, date in raw_rows:
        keyed = _dedup_key(href)
        if not keyed:
            log(f"{ch['key']}: artclView 패턴 아님 — {href}")
            continue
        dedup_key, _parts = keyed
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        url = urljoin(BASE, href)
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
                    date=date,
                    body_text=body,
                    category_hint=ch.get("category_hint"),
                    operator=ch.get("operator"),
                    attachments=attachments,
                    extra={"site": site, "fnct_no": fnct_no},
                )
            )
        except ValueError as exc:
            log(f"{ch['key']}: {exc}")
    return notices
