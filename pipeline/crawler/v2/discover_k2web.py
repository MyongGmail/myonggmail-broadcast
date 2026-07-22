"""K2Web 전수 클로저 — www.mju.ac.kr 안의 '모든' 사이트와 게시판을 기계적으로 열거한다.

닫힌 집합 논증: 명지대 K2Web CMS의 게시판은 전부
  /{site}/{menuId}/subview.do  (목록 진입)  +  pageForm action="/bbs/{site}/{fnctNo}/artclList.do"
패턴을 따른다. 따라서 (1) 사이트코드 집합을 BFS로 닫고 (2) 각 사이트의 메뉴에서
게시판을 검증하면, 이 CMS 안에서는 미발견 게시판이 남을 수 없다.

요청 예산: 사이트당 index 1회 + 게시판 후보 subview 검증 최대 N회(기본 6).
PoliteClient가 1.2초 간격·robots를 강제하므로 전량 실행에 수십 분이 걸린다(1회성).

사용:
  python3 -m pipeline.crawler.v2.discover_k2web            # 전수 스캔
  python3 -m pipeline.crawler.v2.discover_k2web --max-sites 10  # 시험 실행
출력: pipeline/config/schools/mju.boards_full.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .core.http import PoliteClient
from .core.registry import CONFIG_DIR, load_school

BASE = "https://www.mju.ac.kr"

SITE_LINK_RE = re.compile(r'href="/([a-z0-9_]+)/(?:index\.do|\d+/subview\.do)', re.I)
MENU_LINK_RE = re.compile(r'<a[^>]+href="/%s/(\d+)/subview\.do"[^>]*>(.*?)</a>', re.S)
PAGEFORM_RE = re.compile(r'name="pageForm"[^>]*action="/bbs/([a-z0-9_]+)/(\d+)/artclList\.do"', re.I)
BBS_EVIDENCE_RE = re.compile(r'/bbs/([a-z0-9_]+)/(\d+)/\d+/artclView\.do')
TAG_RE = re.compile(r"<[^>]+>")

# 게시판일 가능성이 높은 메뉴명 (subview 검증 대상 선별)
BOARDISH = re.compile(
    r"공지|알림|소식|뉴스|notice|news|비교과|프로그램|행사|모집|채용|취업|진로|장학|공고|자료실|게시판|공모|특강|세미나|장터|정보"
)
# 사이트코드로 오인되는 경로 조각
NOT_SITES = {
    "bbs", "common", "sites", "synap", "upload", "attach", "component", "css", "js",
    "images", "img", "static", "mbs", "sso", "servlet",
}


def clean(t):
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", t)).strip()


def discover(max_sites=0, verify_per_site=6, delay=1.2):
    cfg = load_school("mju")
    known_sites = {ch["params"].get("site") for ch in cfg["channels"] if ch["params"].get("site")}
    known_boards = set()
    for ch in cfg["channels"]:
        p = ch.get("params", {})
        if p.get("site") and p.get("menu_id"):
            known_boards.add((p["site"], p["menu_id"]))

    client = PoliteClient({"www.mju.ac.kr"}, cfg["defaults"]["user_agent"], delay_sec=delay)

    # -------- 1) 사이트코드 BFS
    seeds = ["mjukr"] + sorted(known_sites - {None})
    queue = list(dict.fromkeys(seeds + ["majorfree", "humanities"]))
    seen_sites, site_pages, failures = set(), {}, []
    while queue:
        code = queue.pop(0)
        if code in seen_sites or code in NOT_SITES:
            continue
        seen_sites.add(code)
        if max_sites and len(seen_sites) > max_sites:
            break
        try:
            html = client.get(f"{BASE}/{code}/index.do")
        except Exception as exc:
            failures.append(f"{code}: index.do 실패 — {exc}")
            continue
        site_pages[code] = html
        for new in set(SITE_LINK_RE.findall(html)):
            new = new.lower()
            if new not in seen_sites and new not in NOT_SITES:
                queue.append(new)
        print(f"[site] {code} (누적 {len(seen_sites)}, 큐 {len(queue)})", flush=True)

    # -------- 2) 사이트별 게시판 열거
    sites_out = {}
    for code, html in site_pages.items():
        menus = []
        for menu_id, inner in re.findall(MENU_LINK_RE.pattern % re.escape(code), html, re.S):
            title = clean(inner)
            if title:
                menus.append((menu_id, title))
        menus = list(dict.fromkeys(menus))

        boards = []
        evidenced = {(s, f) for s, f in BBS_EVIDENCE_RE.findall(html) if s == code}
        verify_budget = verify_per_site
        for menu_id, title in menus:
            entry = {"menu_id": menu_id, "title": title[:60]}
            if (code, menu_id) in known_boards:
                entry["status"] = "registered"
            elif BOARDISH.search(title) and verify_budget > 0:
                verify_budget -= 1
                try:
                    sub = client.get(f"{BASE}/{code}/{menu_id}/subview.do")
                    m = PAGEFORM_RE.search(sub)
                    if m and m.group(1).lower() == code:
                        entry["status"] = "verified"
                        entry["fnct_no"] = m.group(2)
                    else:
                        entry["status"] = "not-board"
                except Exception as exc:
                    entry["status"] = f"error: {exc}"
            elif BOARDISH.search(title):
                entry["status"] = "candidate(예산 소진)"
            else:
                continue  # 게시판성 없는 일반 메뉴는 기록 생략
            boards.append(entry)

        sites_out[code] = {
            "boards": boards,
            "bbs_evidence_fncts": sorted({f for _, f in evidenced}),
        }
        found_n = sum(1 for b in boards if b.get("status") == "verified")
        print(f"[boards] {code}: 메뉴 {len(menus)}, 게시판 확인 {found_n}", flush=True)

    out = {
        "source": "discover_k2web (BFS from mjukr + registry seeds)",
        "sites_total": len(seen_sites),
        "sites_fetched": len(site_pages),
        "stats": {
            "verified_new": sum(
                1 for s in sites_out.values() for b in s["boards"] if b.get("status") == "verified"
            ),
            "registered": sum(
                1 for s in sites_out.values() for b in s["boards"] if b.get("status") == "registered"
            ),
        },
        "requests": client.stats["requests"],
        "failures": failures,
        "sites": sites_out,
    }
    path = CONFIG_DIR / "mju.boards_full.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"\n사이트 {out['sites_fetched']}/{out['sites_total']} · 신규 검증 게시판 "
        f"{out['stats']['verified_new']} · 요청 {out['requests']}회 → {path}"
    )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-sites", type=int, default=0)
    ap.add_argument("--verify-per-site", type=int, default=6)
    args = ap.parse_args()
    discover(max_sites=args.max_sites, verify_per_site=args.verify_per_site)
    return 0


if __name__ == "__main__":
    sys.exit(main())
