"""명지메일 크롤러 v2 러너.

사용:
  python3 -m pipeline.crawler.v2.run --school mju                     # incremental (RSS 우선)
  python3 -m pipeline.crawler.v2.run --school mju --mode backfill     # 전량 페이지네이션
  python3 -m pipeline.crawler.v2.run --school mju --channels ctl,innov --no-detail

출력:
  pipeline/snapshots/parsed/notices_v2.json   (이번 실행 수집분 + 기존 병합)
  pipeline/snapshots/state/{school}_keys.json (알려진 dedup_key 상태)
  pipeline/snapshots/REPORT_V2.md
  + SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 존재 시 Supabase upsert
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .adapters.base import HAVE_BS4
from .core import storage
from .core.http import DisallowedByRobots, PoliteClient
from .core.model import now_utc_iso
from .core.registry import allowed_hosts, get_adapter, load_school

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAP = REPO_ROOT / "pipeline" / "snapshots"


def load_state(school_id):
    path = SNAP / "state" / f"{school_id}_keys.json"
    if path.exists():
        return set(json.loads(path.read_text(encoding="utf-8")))
    return set()


def save_state(school_id, keys):
    storage.write_json(SNAP / "state" / f"{school_id}_keys.json", sorted(keys))


def merge_notices(school_id, new_notices):
    """기존 notices_v2.json과 병합(새 항목 우선, body 있는 쪽 보존)."""
    path = SNAP / "parsed" / "notices_v2.json"
    old = {}
    if path.exists():
        for n in json.loads(path.read_text(encoding="utf-8")):
            old[n["dedup_key"]] = n
    for n in new_notices:
        prev = old.get(n["dedup_key"])
        if prev and prev.get("body_text") and not n.get("body_text"):
            n = dict(n, body_text=prev["body_text"], attachments=prev["attachments"])
        old[n["dedup_key"]] = n
    merged = sorted(
        old.values(), key=lambda n: (n["date"] or "0000", n["dedup_key"]), reverse=True
    )
    storage.write_json(path, merged)
    return merged


def write_report(results, run_meta):
    lines = [
        "# 크롤링 리포트 v2",
        "",
        f"- 실행(UTC): {run_meta['started_at']} → {run_meta['finished_at']}",
        f"- 모드: {run_meta['mode']} / 학교: {run_meta['school_id']}",
        f"- HTTP 요청: {run_meta['requests']}회, robots 차단: {run_meta['robots_blocked']}건",
        f"- 신규: {run_meta['new_items']}건 / 총 병합: {run_meta['total_items']}건",
        "",
        "| 채널 | 수집 | 신규 | 상세수집 | 상태 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['key']} ({r['name']}) | {r['collected']} | {r['new']} | {r['detailed']} | {r['status']} |"
        )
    lines += ["", "## 경고/로그", ""]
    warn = False
    for r in results:
        for msg in r["logs"]:
            warn = True
            lines.append(f"- [{r['key']}] {msg}")
    if not warn:
        lines.append("- 없음")
    (SNAP / "REPORT_V2.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="명지메일 크롤러 v2")
    ap.add_argument("--school", default="mju")
    ap.add_argument("--mode", choices=["incremental", "backfill"], default="incremental")
    ap.add_argument("--pages", type=int, default=0, help="backfill 목록 페이지 상한 (0=전량)")
    ap.add_argument("--channels", default="", help="쉼표 구분 채널 key 필터")
    ap.add_argument("--no-detail", action="store_true", help="상세 본문 수집 생략")
    ap.add_argument("--detail-limit", type=int, default=150, help="실행당 상세 요청 상한")
    ap.add_argument(
        "--allow-no-bs4", action="store_true",
        help="bs4 없이 정규식 폴백으로 수집(제목 품질 저하를 감수한다는 명시 선언)",
    )
    args = ap.parse_args()

    # bs4 부재는 **조용한 품질 저하**다 — 예외가 아니라 정규식 폴백으로 넘어가고, 그 폴백은
    # 엔티티를 풀지 않아 제목에 `&lt;`·`&amp;`가 그대로 실린다(bs4의 get_text()는 풀어 준다).
    # 즉 **같은 코드가 환경에 따라 다른 데이터를 만든다.**
    #
    # 2026-07-28 인구조사가 정확히 이 상태로 21,734행을 수집했고 제목 442건이 파손됐다.
    # CI는 무죄다 — beautifulsoup4는 pipeline/requirements.txt에 있고 crawl.yml이 설치한다.
    # 파손된 실행은 **로컬**이었다. 아무도 못 알아챈 이유는 크롤이 성공으로 끝났기 때문이다.
    # 그래서 가드를 CI가 아니라 여기(진입점)에 둔다 — 사고가 난 자리가 여기다.
    if not HAVE_BS4:
        msg = (
            "bs4(beautifulsoup4)가 없다 — 정규식 폴백은 HTML 엔티티를 풀지 못해\n"
            "  제목에 &lt;·&amp;가 그대로 저장된다(2026-07-28 실사고: 442건).\n"
            "  설치:  python3 -m pip install -r pipeline/requirements.txt\n"
            "  품질 저하를 감수하고 진행하려면:  --allow-no-bs4"
        )
        if not args.allow_no_bs4:
            raise SystemExit(f"[중단] {msg}")
        print(f"[경고] {msg}", flush=True)

    cfg = load_school(args.school)
    school_id = cfg["school"]["id"]
    defaults = cfg.get("defaults", {})
    client = PoliteClient(
        allowed_hosts(cfg),
        defaults.get("user_agent", "MyongGmailBot/0.2"),
        delay_sec=defaults.get("request_delay_sec", 1.2),
        timeout_sec=defaults.get("timeout_sec", 20),
    )

    known = load_state(school_id)
    # 상세(본문) 수집 스킵 기준은 '본문을 이미 가진 키' — 목록만 먼저 수집된 항목도
    # 이후 실행에서 본문이 백필되도록 state 키와 분리한다.
    detail_done = set()
    parsed_path = SNAP / "parsed" / "notices_v2.json"
    if parsed_path.exists():
        for n in json.loads(parsed_path.read_text(encoding="utf-8")):
            if n.get("body_text"):
                detail_done.add(n["dedup_key"])
    only = {k.strip() for k in args.channels.split(",") if k.strip()}
    detail_budget = {"n": args.detail_limit}
    started_at = now_utc_iso()
    t0 = time.monotonic()

    results, all_new = [], []
    for ch in cfg["channels"]:
        if only and ch["key"] not in only:
            continue
        if not only and not ch.get("enabled"):
            continue
        adapter = get_adapter(ch["strategy"])
        r = {"key": ch["key"], "name": ch["name"], "collected": 0, "new": 0, "detailed": 0,
             "status": "ok", "logs": []}
        results.append(r)
        if adapter is None:
            r["status"] = f"어댑터 미구현({ch['strategy']}) — 건너뜀"
            continue
        ctx = {
            "school": cfg["school"],
            "channel": ch,
            "client": client,
            "mode": args.mode,
            "pages": args.pages,
            "with_detail": not args.no_detail,
            "detail_budget": detail_budget,
            "known_keys": detail_done,
            "log": r["logs"].append,
        }
        print(f"[{ch['key']}] {ch['name']} ({ch['strategy']}, {args.mode}) ...", flush=True)
        try:
            notices = adapter(ctx)
        except DisallowedByRobots as exc:
            r["status"] = f"robots 차단: {exc}"
            continue
        except Exception as exc:
            r["status"] = f"오류: {exc}"
            continue
        r["collected"] = len(notices)
        new = [n for n in notices if n["dedup_key"] not in known]
        r["new"] = len(new)
        r["detailed"] = sum(1 for n in notices if n.get("body_text"))
        if r["collected"] == 0 and r["status"] == "ok":
            if ch.get("params", {}).get("allow_empty"):
                r["status"] = "0건(빈 게시판 정상)"
            else:
                r["status"] = "0건 — 구조 변경 의심(워치독 확인 필요)"
        all_new.extend(notices)
        known.update(n["dedup_key"] for n in notices)
        print(f"  -> {r['collected']}건 (신규 {r['new']})", flush=True)

    merged = merge_notices(school_id, all_new)
    save_state(school_id, known)

    log_msgs = []
    upserted = storage.supabase_upsert_notices(all_new, log_msgs.append)
    run_meta = {
        "school_id": school_id,
        "mode": args.mode,
        "started_at": started_at,
        "finished_at": now_utc_iso(),
        "duration_sec": round(time.monotonic() - t0, 1),
        "requests": client.stats["requests"],
        "robots_blocked": client.stats["robots_blocked"],
        "new_items": sum(r["new"] for r in results),
        "total_items": len(merged),
        "upserted": upserted,
        "channel_summary": {r["key"]: {"collected": r["collected"], "new": r["new"], "status": r["status"]} for r in results},
    }
    storage.supabase_insert_run(
        {k: run_meta[k] for k in ("school_id", "mode", "started_at", "finished_at",
                                  "duration_sec", "requests", "new_items", "channel_summary")},
        log_msgs.append,
    )
    for m in log_msgs:
        print(m)
    write_report(results, run_meta)

    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\n채널 성공 {ok}/{len(results)} · 신규 {run_meta['new_items']}건 · 병합 {len(merged)}건"
          f" · Supabase {upserted}건 · 리포트 pipeline/snapshots/REPORT_V2.md")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
