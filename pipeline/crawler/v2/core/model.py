"""공지 레코드 스키마 + 정규화 유틸.

모든 어댑터는 make_notice()로 레코드를 만든다. dedup_key는 학교 안에서 전역 유일해야
하며, 같은 글이 여러 지면(미러/교차게재 중 '같은 원본'인 경우)에 나타나도 같은 키가
나오도록 어댑터가 원본 식별자(K2Web이면 site:fnctNo:artclSeq)를 쓴다.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

CAMPUS_RE = re.compile(r"^\[(인문|자연)\]")
_DATE_PATTERNS = ("%Y.%m.%d", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d.")


def now_utc_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_date(raw):
    """다양한 날짜 표기 → 'YYYY-MM-DD' (실패 시 None)."""
    if not raw:
        return None
    raw = str(raw).strip()
    for pat in _DATE_PATTERNS:
        try:
            return datetime.strptime(raw, pat).strftime("%Y-%m-%d")
        except ValueError:
            pass
    # RSS pubDate: 'Wed, 16 Jul 2026 09:00:00 +0900' / ISO8601
    for pat in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, pat).strftime("%Y-%m-%d")
        except ValueError:
            pass
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", raw)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def clean_text(raw, limit=20000):
    if raw is None:
        return None
    text = re.sub(r"[ \t ]+", " ", raw)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return text[:limit] if text else None


def make_notice(
    school_id,
    channel_key,
    dedup_key,
    title,
    url,
    date=None,
    body_text=None,
    category_hint=None,
    operator=None,
    attachments=None,
    extra=None,
):
    title = re.sub(r"\s+", " ", str(title)).strip()
    campus_m = CAMPUS_RE.match(title)
    if not title or not url or not dedup_key:
        raise ValueError(f"필수 필드 누락: dedup_key={dedup_key} url={url} title={title!r}")
    return {
        "school_id": school_id,
        "channel_key": channel_key,
        "dedup_key": dedup_key,
        "title": title,
        "url": url,
        "date": normalize_date(date),
        "campus": campus_m.group(1) if campus_m else None,
        "body_text": clean_text(body_text),
        "category_hint": category_hint,
        "operator": operator,
        "attachments": attachments or [],
        "extra": extra or {},
        "scraped_at": now_utc_iso(),
    }
