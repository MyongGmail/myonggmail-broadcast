"""S3: 네이티브 RSS 채널 (유튜브·네이버 블로그 등) — 폴링만, 유지보수 ≈ 0."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

from ..core.model import make_notice
from .base import clean_text

ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://search.yahoo.com/mrss/}"


def _items(root):
    """RSS 2.0 <item> 또는 Atom <entry> → (title, link, date, summary).

    ⚠️ 제목·본문은 `clean_text`를 **반드시** 거친다. XML 파서가 엔티티를 한 번 풀지만,
    피드가 이중 이스케이프한 것(`&amp;#38;` → 파서 후 `&#38;`)은 그대로 남는다. 실제로
    mjuecon 본문 2건이 `&#38;`를 달고 저장돼 있었다(2026-07-28). HTML 경로가 아니라
    `strip_tags`를 쓰지 않으므로, 그 사용을 감시하는 계약 테스트만으로는 잡히지 않는다 —
    코퍼스 엔티티 잔존 0을 단정하는 테스트가 이 구멍의 유일한 감시자다.
    """
    out = []
    for item in root.iter("item"):  # RSS 2.0
        title = clean_text(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        date = (item.findtext("pubDate") or "").strip()
        desc = clean_text(item.findtext("description") or "")
        if title and link:
            out.append((title, link, date, desc))
    if out:
        return out
    for entry in root.iter(f"{ATOM}entry"):  # Atom (유튜브)
        title = clean_text(entry.findtext(f"{ATOM}title") or "")
        link_el = entry.find(f"{ATOM}link")
        link = link_el.get("href", "") if link_el is not None else ""
        date = (entry.findtext(f"{ATOM}published") or "").strip()
        media = entry.find(f"{MEDIA}group/{MEDIA}description")
        desc = clean_text(media.text) if media is not None and media.text else ""
        if title and link:
            out.append((title, link, date, desc))
    return out


def collect(ctx):
    ch = ctx["channel"]
    log = ctx["log"]
    try:
        root = ET.fromstring(ctx["client"].get(ch["params"]["url"]))
    except Exception as exc:
        log(f"{ch['key']}: RSS 수집 실패: {exc}")
        return []
    notices = []
    for title, link, date, desc in _items(root):
        dedup_key = "rss:%s:%s" % (ch["key"], hashlib.sha1(link.encode()).hexdigest()[:16])
        try:
            notices.append(
                make_notice(
                    ctx["school"]["id"],
                    ch["key"],
                    dedup_key,
                    title,
                    link,
                    date=date,
                    body_text=desc or None,
                    category_hint=ch.get("category_hint"),
                    operator=ch.get("operator"),
                )
            )
        except ValueError as exc:
            log(f"{ch['key']}: {exc}")
    return notices
