"""S3: 네이티브 RSS 채널 (유튜브·네이버 블로그 등) — 폴링만, 유지보수 ≈ 0."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

from ..core.model import make_notice

ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://search.yahoo.com/mrss/}"


def _items(root):
    """RSS 2.0 <item> 또는 Atom <entry> → (title, link, date, summary)."""
    out = []
    for item in root.iter("item"):  # RSS 2.0
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        date = (item.findtext("pubDate") or "").strip()
        desc = (item.findtext("description") or "").strip()
        if title and link:
            out.append((title, link, date, desc))
    if out:
        return out
    for entry in root.iter(f"{ATOM}entry"):  # Atom (유튜브)
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        link_el = entry.find(f"{ATOM}link")
        link = link_el.get("href", "") if link_el is not None else ""
        date = (entry.findtext(f"{ATOM}published") or "").strip()
        media = entry.find(f"{MEDIA}group/{MEDIA}description")
        desc = (media.text or "").strip() if media is not None and media.text else ""
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
