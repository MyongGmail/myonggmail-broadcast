"""상시 발견 루프 — 수집된 공지 본문에서 '미등록 지면' 후보를 캔다.

원리: 새 매체(사업단 사이트·카톡 채널·인스타·신청 시스템)는 거의 항상 기존 공지
본문 속 링크로 먼저 등장한다(ngscc.kr이 역추적에서 발견된 경로가 정확히 이것).
그래서 본문 링크를 전수 추출해 레지스트리에 없는 지면을 빈도순으로 보고하면,
열린 집합(수시로 생기는 채널)에 대한 상시 감지기가 된다.

사용: python3 -m pipeline.crawler.v2.link_miner --school mju
출력: pipeline/snapshots/DISCOVERY_REPORT.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from .core.registry import CONFIG_DIR, load_school

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAP = REPO_ROOT / "pipeline" / "snapshots"

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
# www.mju.ac.kr 경로에서 사이트코드 추출: /{code}/{n}/subview.do 또는 /bbs/{code}/...
MJU_SITE_RE = re.compile(r"^/(?:bbs/)?([a-z0-9_]+)/", re.I)

# 발견 대상이 아닌 잡음 도메인 (플랫폼 자체·단축·정적 리소스)
NOISE_HOSTS = {
    "docs.google.com", "forms.gle", "drive.google.com", "goo.gl", "bit.ly",
    "naver.me", "youtu.be", "www.google.com", "play.google.com", "apps.apple.com",
    "zoom.us", "us02web.zoom.us", "us06web.zoom.us", "sso.mju.ac.kr",
}


def _registered_surfaces(cfg):
    """레지스트리가 이미 아는 지면 식별자 집합: 호스트 + mju 사이트코드."""
    hosts, sites = set(), set()
    for ch in cfg["channels"]:
        p = ch.get("params", {})
        if p.get("host"):
            hosts.add(p["host"].lower())
        if p.get("site"):
            sites.add(p["site"].lower())
        if p.get("url"):
            hosts.add(urlparse(p["url"]).netloc.lower())
    hosts |= {"www.mju.ac.kr", "lib.mju.ac.kr"}
    sites |= {"mjukr"}
    return hosts, sites


def classify(url, hosts, sites):
    """URL → (지면 키, 종류) 또는 None(등록됨/잡음)."""
    try:
        u = urlparse(url)
    except ValueError:
        return None
    host = u.netloc.lower()
    if not host or host in NOISE_HOSTS:
        return None

    if host.endswith("mju.ac.kr") or host == "www.mjujob.ac.kr":
        # 교내: 사이트코드 단위로 판정
        if host in ("www.mju.ac.kr",):
            m = MJU_SITE_RE.match(u.path)
            if not m:
                return None
            site = m.group(1).lower()
            if site in sites or site in ("common", "sites", "synap", "upload", "attach"):
                return None
            return (f"mju-site:{site}", "교내 K2Web 사이트(미등록)")
        if host in hosts:
            return None
        # 등록된 K2Web 사이트코드의 서브도메인 별칭(mjupsc.mju.ac.kr 등)은 등록 취급
        if host.split(".")[0] in sites:
            return None
        return (f"host:{host}", "교내 서브도메인(미등록)")

    if host == "pf.kakao.com":
        m = re.match(r"^/(_[A-Za-z0-9]+)", u.path)
        return (f"kakao-ch:{m.group(1)}" if m else f"kakao-ch:{u.path}", "카카오톡 채널")
    if host == "open.kakao.com":
        return (f"kakao-open:{u.path}", "카카오 오픈채팅")
    if host in ("www.instagram.com", "instagram.com"):
        m = re.match(r"^/([A-Za-z0-9._]+)", u.path)
        handle = m.group(1) if m else ""
        if handle in ("p", "reel", "stories", ""):
            return None
        return (f"instagram:@{handle}", "인스타그램 계정")
    if host in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        return (f"youtube:{u.path.split('?')[0]}", "유튜브")
    if host in hosts:
        return None
    return (f"host:{host}", "외부 도메인")


def mine(school_id):
    cfg = load_school(school_id)
    hosts, sites = _registered_surfaces(cfg)
    notices_path = SNAP / "parsed" / "notices_v2.json"
    notices = json.loads(notices_path.read_text(encoding="utf-8"))

    found = defaultdict(lambda: {"kind": None, "count": 0, "examples": []})
    scanned = 0
    for n in notices:
        text_parts = [n.get("body_text") or ""]
        for att in n.get("attachments", []):
            text_parts.append(att.get("url", ""))
        blob = "\n".join(text_parts)
        if not blob.strip():
            continue
        scanned += 1
        for url in URL_RE.findall(blob):
            url = url.rstrip(".,;»】)」')\"")
            hit = classify(url, hosts, sites)
            if not hit:
                continue
            key, kind = hit
            rec = found[key]
            rec["kind"] = kind
            rec["count"] += 1
            if len(rec["examples"]) < 3:
                rec["examples"].append(f"{n['title'][:40]} ({n['channel_key']})")

    ranked = sorted(found.items(), key=lambda kv: -kv[1]["count"])
    lines = [
        "# 미등록 지면 발견 리포트 (link miner)",
        "",
        f"- 본문 보유 공지 {scanned}건 스캔 / 전체 {len(notices)}건",
        f"- 미등록 지면 후보 {len(ranked)}종",
        "",
        "| 후보 | 종류 | 출현 | 예시(공지) |",
        "|---|---|---|---|",
    ]
    for key, rec in ranked[:60]:
        ex = "; ".join(rec["examples"])[:120]
        lines.append(f"| `{key}` | {rec['kind']} | {rec['count']} | {ex} |")
    if not ranked:
        lines.append("| (없음) | | | |")
    lines += [
        "",
        "운영 규칙: 상위 후보를 분기 점검에서 판정 → 채널 레지스트리 편입(enabled) 또는",
        "dead-end 기록. 판정 결과와 무관하게 이 리포트는 매 크롤마다 자동 갱신된다.",
        "",
    ]
    (SNAP / "DISCOVERY_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return len(ranked), ranked[:10]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--school", default="mju")
    args = ap.parse_args()
    total, top = mine(args.school)
    print(f"미등록 지면 후보 {total}종 → pipeline/snapshots/DISCOVERY_REPORT.md")
    for key, rec in top:
        print(f"  {rec['count']:3d}× {key} ({rec['kind']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
