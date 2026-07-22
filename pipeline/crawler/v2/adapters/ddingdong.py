"""S6: 띵동(ddingdong.mju.ac.kr) 동아리 모집공고 어댑터.

띵동은 Next.js(App Router) SPA지만 서버렌더링이라 RSC flight payload
(`self.__next_f.push([1,"..."])` 스크립트)에 데이터가 그대로 들어 있어
JS 렌더링 없이 수집 가능하다 (2026-07-18 실사이트 확인):

  목록 : GET /           → "clubs":[{"id","name","category","tag","recruitStatus"}]
                           recruitStatus ∈ {"모집 중","모집 예정","모집 마감"}
  상세 : GET /club/{id}  → {"startDate":"YYYY-MM-DD","endDate":...,"formId":...}
                           + {"introduction","activity","ideal"} 소개 텍스트

"모집 중"/"모집 예정"인 동아리만 항목화한다("마감" 포함 상태는 제외).
date = 모집 시작일(startDate) — 상세 요청에서만 얻을 수 있어 상세 예산이
없으면 None. dedup_key = "ddingdong:{club_id}" (동아리당 페이지 1개).
상세 payload의 leader/phoneNumber 등 개인정보는 저장하지 않는다.
robots.txt 없음(404 → 허용 취급). __NEXT_DATA__는 쓰지 않는 사이트다.
"""

from __future__ import annotations

import json
import re

from ..core.model import make_notice
from .base import take_detail

DEFAULT_HOST = "ddingdong.mju.ac.kr"

# <script>self.__next_f.push([1,"<JSON 문자열 리터럴>"])</script>
PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,\s*("(?:[^"\\]|\\.)*")\s*\]\)', re.S)
START_RE = re.compile(r'"startDate"\s*:\s*"(\d{4}-\d{2}-\d{2})"')
END_RE = re.compile(r'"endDate"\s*:\s*"(\d{4}-\d{2}-\d{2})"')
FORM_RE = re.compile(r'"formId"\s*:\s*(\d+)')
# 최후 폴백: flight 디코드가 통째로 실패했을 때 클럽 오브젝트 단위 스캔
CLUB_FALLBACK_RE = re.compile(
    r'\{"id":\s*(\d+)\s*,\s*"name":\s*"([^"]+)"\s*,\s*"category":\s*"([^"]*)"'
    r'\s*,\s*"tag":\s*"([^"]*)"\s*,\s*"recruitStatus":\s*"([^"]+)"\s*\}'
)

_DECODER = json.JSONDecoder()


def _flight_text(html):
    """RSC flight 스크립트 조각들을 JSON 문자열로 디코드해 합친 텍스트."""
    parts = []
    for lit in PUSH_RE.findall(html or ""):
        try:
            parts.append(json.loads(lit))
        except ValueError:
            continue
    if parts:
        return "\n".join(parts)
    # 디코드 실패 시 크루드 언이스케이프 폴백 (\" → ")
    return (html or "").replace('\\"', '"')


def _json_value_after(text, key):
    """text 안의 '"key":' 바로 뒤 JSON 값을 파싱 (없거나 실패 시 None)."""
    for m in re.finditer('"%s"\\s*:\\s*' % re.escape(key), text):
        try:
            value, _end = _DECODER.raw_decode(text, m.end())
            return value
        except ValueError:
            continue
    return None


def _parse_clubs(html, log):
    """홈 HTML → [{"id","name","category","tag","recruitStatus"}] | None."""
    text = _flight_text(html)
    clubs = _json_value_after(text, "clubs")
    if isinstance(clubs, list) and clubs:
        return clubs
    rows = [
        {"id": int(cid), "name": name, "category": cat, "tag": tag, "recruitStatus": status}
        for cid, name, cat, tag, status in CLUB_FALLBACK_RE.findall(text)
    ]
    if rows:
        log("ddingdong: raw_decode 실패 — 정규식 폴백으로 %d개 파싱" % len(rows))
        return rows
    return None


def _recruiting(status):
    """'모집 중'/'모집 예정'만 True — '마감' 포함이면 False."""
    s = (status or "").strip()
    return bool(s) and "모집" in s and "마감" not in s


def _fetch_detail(client, url, log):
    """상세 페이지 → (start_date, end_date, form_id, body_text)."""
    try:
        html = client.get(url)
    except Exception as exc:
        log("ddingdong: 상세 수집 실패 %s: %s" % (url, exc))
        return None, None, None, None
    text = _flight_text(html)
    start_m = START_RE.search(text)
    end_m = END_RE.search(text)
    form_m = FORM_RE.search(text)
    body_parts = []
    for key, label in (("introduction", "소개"), ("activity", "주요 활동"), ("ideal", "이런 사람을 찾아요")):
        value = _json_value_after(text, key)
        if isinstance(value, str) and value.strip():
            body_parts.append("[%s]\n%s" % (label, value.strip()))
    return (
        start_m.group(1) if start_m else None,
        end_m.group(1) if end_m else None,
        int(form_m.group(1)) if form_m else None,
        "\n\n".join(body_parts) or None,
    )


def collect(ctx):
    ch = ctx["channel"]
    p = ch.get("params") or {}
    host = p.get("host", DEFAULT_HOST)
    base = "https://%s" % host
    client = ctx["client"]
    log = ctx["log"]
    school_id = ctx["school"]["id"]
    known = ctx.get("known_keys") or set()

    try:
        html = client.get(base + "/")
    except Exception as exc:
        log("%s: 목록 요청 실패: %s" % (ch["key"], exc))
        return []

    clubs = _parse_clubs(html, log)
    if clubs is None:
        log("%s: clubs 배열 미발견 — 페이지 구조 변경 가능성" % ch["key"])
        return []

    notices = []
    seen = set()
    for club in clubs:
        if not isinstance(club, dict):
            continue
        club_id = club.get("id")
        name = str(club.get("name") or "").strip()
        status = str(club.get("recruitStatus") or "").strip()
        if club_id is None or not name:
            log("%s: id/name 누락 항목 건너뜀: %r" % (ch["key"], club))
            continue
        if not _recruiting(status):
            continue
        dedup_key = "ddingdong:%s" % club_id
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        url = "%s/club/%s" % (base, club_id)
        extra = {
            "club_id": club_id,
            "recruit_status": status,
            "category": club.get("category"),
            "tag": club.get("tag"),
        }
        date = None
        body = None
        if dedup_key not in known and take_detail(ctx):
            date, end_date, form_id, body = _fetch_detail(client, url, log)
            if end_date:
                extra["recruit_end"] = end_date
            if form_id is not None:
                extra["form_id"] = form_id
        try:
            notices.append(
                make_notice(
                    school_id,
                    ch["key"],
                    dedup_key,
                    "[%s] 모집 공고(%s)" % (name, status),
                    url,
                    date=date,
                    body_text=body,
                    category_hint=ch.get("category_hint"),
                    operator=ch.get("operator"),
                    extra=extra,
                )
            )
        except ValueError as exc:
            log("%s: %s" % (ch["key"], exc))
    if not notices:
        log("%s: 모집 중/예정 동아리 없음 (전체 %d개 확인)" % (ch["key"], len(clubs)))
    return notices
