"""S-mjujob: MJ대학일자리플러스센터(www.mjujob.ac.kr) JSP 게시판 어댑터.

말근소프트(malgnsoft) LMS 게시판. URL 구조 (2026-07-18 실사이트 실증):
  목록 : https://www.mjujob.ac.kr/board/index.jsp?code={code}&page={n}
  상세 : https://www.mjujob.ac.kr/board/read.jsp?id={id}&code={code}
  첨부 : https://www.mjujob.ac.kr/main/download.jsp?ek=...&id=...

목록 행 = tbody tr → td.tal > p.subject > a[href*=read.jsp] (제목/id),
td.tac 중 'YYYY.MM.DD' 패턴이 등록일. 페이지바의 page=N 링크 최댓값(마지막
페이지 버튼 포함)이 전체 페이지 수. code: notice(교내프로그램), notice2(교외
프로그램) — mju.json params.codes 로 제어.

incremental = 각 code 첫 페이지만, backfill = ctx["pages"] 상한까지 페이지네이션.
dedup_key = "mjujob:{code}:{id}".
참고: www.mjujob.com 은 ac.kr 로 리다이렉트되는 별칭 — www.mjujob.ac.kr 만 사용.
"""

from __future__ import annotations

import html as html_mod
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from ..core.model import make_notice
from .base import HAVE_BS4, soup, strip_tags, take_detail

DEFAULT_HOST = "www.mjujob.ac.kr"

DATE_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}$")
# 폴백: 목록 행의 제목 링크 (p.subject 안에만 등장 — 헤더 퀵메뉴의 read.jsp 링크 제외)
ROW_FALLBACK_RE = re.compile(
    r'<p class="subject">\s*.*?<a href="([^"]*read\.jsp\?[^"]*)"[^>]*>(.*?)</a>', re.S
)
DATE_FALLBACK_RE = re.compile(r'<td class="tac">\s*(\d{4}\.\d{1,2}\.\d{1,2})\s*</td>')
# 폴백: 본문 div.read_text — 내부에 중첩 div가 흔해 뒤따르는 고정 블록을 경계로 절단
BODY_FALLBACK_RE = re.compile(
    r'<div class="read_text"[^>]*>(.*?)'
    r'(?=<div class="(?:recomm_area|file_info|board btn)"|</td>)',
    re.S,
)
FILE_FALLBACK_RE = re.compile(r'<a href="([^"]*download\.jsp[^"]*)"[^>]*>(.*?)</a>', re.S)


def _post_id(href):
    """read.jsp 링크에서 게시글 id 추출 (실패 시 None)."""
    try:
        q = parse_qs(urlsplit(href.replace("&amp;", "&")).query)
    except ValueError:
        return None
    vals = q.get("id")
    return vals[0] if vals and vals[0].isdigit() else None


def _clean_title(raw):
    return re.sub(r"\s+", " ", raw).strip()


def _parse_list(html, log):
    """목록 HTML → [(title, post_id, date, writer)] — bs4 우선, 정규식 폴백."""
    rows = []
    if HAVE_BS4:
        doc = soup(html)
        for tr in doc.select("div.board_list tbody tr, div.type_list tbody tr"):
            a = tr.select_one("p.subject a[href]")
            if a is None:
                continue
            post_id = _post_id(a["href"])
            if not post_id:
                continue
            texts = [td.get_text(strip=True) for td in tr.find_all("td")]
            date = next((t for t in texts if DATE_RE.match(t)), None)
            # 열 순서: NO | 제목 | 작성자 | 등록일 | 조회수
            writer = texts[2] if len(texts) >= 5 else None
            rows.append((_clean_title(a.get_text(" ")), post_id, date, writer))
    else:
        anchors = ROW_FALLBACK_RE.findall(html)
        dates = DATE_FALLBACK_RE.findall(html)
        for i, (href, inner) in enumerate(anchors):
            post_id = _post_id(href)
            if not post_id:
                continue
            rows.append(
                (
                    _clean_title(html_mod.unescape(strip_tags(inner))),
                    post_id,
                    dates[i] if i < len(dates) else None,
                    None,
                )
            )
    return rows


def _max_page(html, code):
    """페이지바의 code={code}&page=N 링크에서 최대 페이지 번호 (없으면 1)."""
    pat = r"code=" + re.escape(code) + r"&(?:amp;)?page=(\d+)"
    nums = [int(n) for n in re.findall(pat, html)]
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
        for a in doc.select('a[href*="download.jsp"]'):
            name = re.sub(r"\s+", " ", a.get_text(" ")).strip()
            if name:
                attachments.append({"name": name, "url": urljoin(url, a["href"])})
        node = doc.select_one("div.read_text")
        if node is not None:
            body = node.get_text("\n")
    if body is None:  # 폴백: read_text 블록 정규식 절단
        m = BODY_FALLBACK_RE.search(html)
        if m:
            body = html_mod.unescape(strip_tags(m.group(1)))
        if not attachments:
            for href, inner in FILE_FALLBACK_RE.findall(html):
                name = re.sub(r"\s+", " ", html_mod.unescape(strip_tags(inner))).strip()
                if name:
                    attachments.append(
                        {"name": name, "url": urljoin(url, href.replace("&amp;", "&"))}
                    )
    return body, attachments


def collect(ctx):
    ch = ctx["channel"]
    p = ch.get("params") or {}
    host = p.get("host") or DEFAULT_HOST
    codes = p.get("codes") or ["notice"]
    base = f"https://{host}"
    client = ctx["client"]
    log = ctx["log"]
    school_id = ctx["school"]["id"]
    known = ctx.get("known_keys") or set()

    notices = []
    seen = set()
    for code in codes:
        try:
            first_html = client.get(f"{base}/board/index.jsp?code={code}")
        except Exception as exc:
            log(f"{ch['key']}: {code} 목록 1페이지 실패: {exc}")
            continue
        raw_rows = _parse_list(first_html, log)
        if not raw_rows:
            log(f"{ch['key']}: {code} 목록에서 행 0건 — 마크업 변경 여부 확인 필요")
        if ctx["mode"] != "incremental":  # backfill: 전량 페이지네이션
            max_page = _max_page(first_html, code)
            limit = ctx["pages"] or max_page
            for page in range(2, min(max_page, limit) + 1):
                try:
                    raw_rows += _parse_list(
                        client.get(f"{base}/board/index.jsp?code={code}&page={page}"), log
                    )
                except Exception as exc:
                    log(f"{ch['key']}: {code} {page}페이지 실패: {exc}")
                    break

        for title, post_id, date, writer in raw_rows:
            dedup_key = f"mjujob:{code}:{post_id}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            url = f"{base}/board/read.jsp?id={post_id}&code={code}"
            body, attachments = (None, [])
            if dedup_key not in known and take_detail(ctx):
                body, attachments = _fetch_detail(client, url, log)
            extra = {"code": code}
            if writer:
                extra["writer"] = writer
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
