"""저장 계층: 로컬 JSON 스냅샷 + (환경변수 존재 시) Supabase upsert.

Supabase 연동은 PostgREST 직접 호출 — SDK 의존성 없음:
  POST {SUPABASE_URL}/rest/v1/notices?on_conflict=school_id,dedup_key
  Prefer: resolution=merge-duplicates
서비스 롤 키는 GH Actions 시크릿으로만 주입한다(SUPABASE_SERVICE_ROLE_KEY).
"""

from __future__ import annotations

import json
import os

try:
    import requests

    HAVE_REQUESTS = True
except ImportError:
    import urllib.request

    HAVE_REQUESTS = False

BATCH = 300


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sb_env():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    return (url, key) if url and key else (None, None)


def _sb_post(url, key, path, payload, prefer):
    endpoint = f"{url}{path}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if HAVE_REQUESTS:
        resp = requests.post(endpoint, headers=headers, data=body, timeout=30)
        if resp.status_code >= 300:
            raise RuntimeError(f"Supabase {resp.status_code}: {resp.text[:300]}")
        return
    req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"Supabase {resp.status}")


def supabase_upsert_notices(notices, log):
    """환경변수 미설정이면 조용히 스킵(로컬 개발). 성공 건수 반환."""
    url, key = _sb_env()
    if not url:
        log("Supabase 환경변수 없음 — 로컬 JSON만 저장")
        return 0
    rows = [
        {
            "school_id": n["school_id"],
            "channel_key": n["channel_key"],
            "dedup_key": n["dedup_key"],
            "title": n["title"],
            "url": n["url"],
            "published_date": n["date"],
            "campus": n["campus"],
            "body_text": n["body_text"],
            "category_hint": n["category_hint"],
            "operator": n["operator"],
            "attachments": n["attachments"],
            "extra": n["extra"],
            "scraped_at": n["scraped_at"],
        }
        for n in notices
    ]
    sent = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        _sb_post(
            url,
            key,
            "/rest/v1/notices?on_conflict=school_id,dedup_key",
            chunk,
            "resolution=merge-duplicates,return=minimal",
        )
        sent += len(chunk)
    return sent


def supabase_insert_run(run_row, log):
    url, key = _sb_env()
    if not url:
        return
    try:
        _sb_post(url, key, "/rest/v1/crawl_runs", [run_row], "return=minimal")
    except Exception as exc:
        log(f"crawl_runs 기록 실패(치명적이지 않음): {exc}")
