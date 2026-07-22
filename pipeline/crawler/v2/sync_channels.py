"""config(pipeline/config/schools/*.json) → Supabase channels 테이블 동기화.

크롤러 config가 원본(source of truth). 워치독이 priority를 읽을 수 있도록
채널 행을 upsert 한다.

사용: SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \
      python3 -m pipeline.crawler.v2.sync_channels --school mju
"""

from __future__ import annotations

import argparse
import sys

from .core.registry import load_school
from .core.storage import _sb_env, _sb_post


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--school", default="mju")
    args = ap.parse_args()

    url, key = _sb_env()
    if not url:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 환경변수가 필요합니다.")
        return 1

    cfg = load_school(args.school)
    school_id = cfg["school"]["id"]
    rows = [
        {
            "school_id": school_id,
            "key": ch["key"],
            "name": ch["name"],
            "strategy": ch["strategy"],
            "enabled": bool(ch.get("enabled")),
            "priority": ch.get("priority", "low"),
            "category_hint": ch.get("category_hint"),
            "operator": ch.get("operator"),
            "media_group": ch.get("media_group"),
            "params": ch.get("params", {}),
        }
        for ch in cfg["channels"]
    ]
    _sb_post(
        url, key,
        "/rest/v1/channels?on_conflict=school_id,key",
        rows,
        "resolution=merge-duplicates,return=minimal",
    )
    groups = [
        {
            "school_id": school_id,
            "key": g["key"],
            "label": g["label"],
            "description": g.get("desc"),
            "aux": bool(g.get("aux")),
            "sort": i,
        }
        for i, g in enumerate(cfg.get("media_groups", []))
    ]
    if groups:
        _sb_post(
            url, key,
            "/rest/v1/media_groups?on_conflict=school_id,key",
            groups,
            "resolution=merge-duplicates,return=minimal",
        )
    print(f"{school_id}: 채널 {len(rows)}행 + 매체그룹 {len(groups)}행 동기화 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
