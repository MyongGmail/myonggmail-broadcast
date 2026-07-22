"""구조 메트릭 추출: XML bounds → 컴포넌트 수치(px·dp) + 다크/라이트 교차 검증.

실행: python3 pipeline/distill/extract_metrics.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import parse_nodes, find_node, px2dp

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def xml(app, screen):
    p = os.path.join(ROOT, "references", app, "raw", "xml", screen + ".xml")
    return parse_nodes(p) if os.path.exists(p) else None


def h(b):
    return b[3] - b[1]


def w(b):
    return b[2] - b[0]


def gmail_metrics(suffix):
    m = {}
    n = xml("gmail-android", "gmail_inbox_default" + suffix)
    if n:
        sb = find_node(n, text="Search in mail")
        if sb:
            # 검색 필드는 텍스트 bounds가 아닌 필 전체 — 부모 추정: 텍스트 y확장 실측 대신 텍스트 높이 기록
            m["searchbar.text_h"] = h(sb["bounds"])
        comp = find_node(n, text="Compose")
        if comp:
            m["fab.label_h"] = h(comp["bounds"])
        rows = [x for x in n if "Anthropic Team" in x["desc"] or "Terms of Service" in x["desc"]
                or "New privacy settings" in x["desc"]]
        hs = sorted({h(x["bounds"]) for x in rows if h(x["bounds"]) > 200})
        if hs:
            m["list.row_h"] = hs[0]
        if rows:
            m["list.row_margin_l"] = min(x["bounds"][0] for x in rows)
    n = xml("gmail-android", "gmail_search_no_results" + suffix) or \
        xml("gmail-android", "gmail_search_results" + suffix)
    if n:
        chip = find_node(n, text="Labels")
        if chip:
            m["chip.h"] = h(chip["bounds"])
            m["chip.margin_l"] = chip["bounds"][0]
    n = xml("gmail-android", "gmail_detail_overflow_menu" + suffix)
    if n:
        items = [x for x in n if x["text"] in ("Snooze", "Mute", "Print")]
        if len(items) >= 2:
            items.sort(key=lambda x: x["bounds"][1])
            m["menu.item_pitch"] = items[1]["bounds"][1] - items[0]["bounds"][1]
    return m


def naver_metrics(suffix):
    m = {}
    n = xml("navermail-android", "navermail_inbox_default" + suffix)
    if n:
        chip = find_node(n, text="프로모션", y_range=(330, 500))
        if chip:
            m["chip.label_h"] = h(chip["bounds"])
        title = find_node(n, text="받은메일함", y_range=(120, 320))
        if title:
            m["titlebar.text_h"] = h(title["bounds"])
            m["titlebar.margin_l"] = title["bounds"][0]
        senders = [x for x in n if x["text"] in
                   ("LinkedIn", "Steam", "Whole Tomato Software", "JamKazam", "네이버 전자문서")]
        tops = sorted(x["bounds"][1] for x in senders)
        pitches = [b - a for a, b in zip(tops, tops[1:]) if 200 < b - a < 600]
        if pitches:
            m["list.row_pitch"] = min(pitches)
        if senders:
            m["list.margin_l"] = min(x["bounds"][0] for x in senders)
    n = xml("navermail-android", "navermail_drawer_mailbox_list" + suffix)
    if n:
        a = find_node(n, text="전체메일")
        b = find_node(n, text_re=r"^받은메일함")
        if a and b:
            m["drawer.row_pitch"] = abs(b["bounds"][1] - a["bounds"][1])
    return m


def crosscheck(fn, name, tol=4):
    dark = fn("")
    light = fn("_light")
    report = []
    merged = {}
    for k in sorted(set(dark) | set(light)):
        d, l = dark.get(k), light.get(k)
        if d is not None and l is not None:
            ok = abs(d - l) <= tol
            report.append((k, d, l, "OK" if ok else "MISMATCH"))
            merged[k] = {"px": d, "dp": px2dp(d), "crosscheck": "ok" if ok else f"dark={d},light={l}"}
        else:
            v = d if d is not None else l
            merged[k] = {"px": v, "dp": px2dp(v),
                         "crosscheck": "single-mode(" + ("dark" if d else "light") + ")"}
    print(f"== {name} 교차검증 (다크↔라이트, 허용오차 {tol}px) ==")
    for k, d, l, s in report:
        flag = "" if s == "OK" else "  ⚠"
        print(f"  {k:22} dark={d:4} light={l:4} {s}{flag}")
    return merged


if __name__ == "__main__":
    out = {
        "$meta": {
            "source": "uiautomator XML bounds, 1440px/600dpi → dp=px×0.2667 (384dp 화면)",
            "note": "텍스트 *_h 는 텍스트 bounds 높이(폰트 수치 아님, ±2dp 근사). pitch=행 시작점 간격.",
            "generated_by": "pipeline/distill/extract_metrics.py",
        },
        "gugol": crosscheck(gmail_metrics, "gugol"),
        "never": crosscheck(naver_metrics, "never"),
    }
    p = os.path.join(ROOT, "references", "tokens", "metrics.json")
    json.dump(out, open(p, "w"), ensure_ascii=False, indent=2)
    print("→", p)
