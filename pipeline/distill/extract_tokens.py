"""디자인 토큰 추출: 캡처(PNG+XML) → gugol/never tokens.json (dark/light/mono).

실행: python3 pipeline/distill/extract_tokens.py  (리포 루트 기준)
멱등: 같은 입력이면 같은 출력.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from PIL import Image
from lib import (parse_nodes, find_node, surface_color, text_color, to_hex,
                 desaturate, expand, text_color_directional)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load(app, screen):
    png = os.path.join(ROOT, "references", app, "raw", "screenshots", screen + ".png")
    xml = os.path.join(ROOT, "references", app, "raw", "xml", screen + ".xml")
    if not (os.path.exists(png) and os.path.exists(xml)):
        return None, None
    return Image.open(png).convert("RGB"), parse_nodes(xml)


def gmail_mode(suffix):
    """suffix '' = dark, '_light' = light"""
    colors, src = {}, {}

    def put(key, val, screen, what):
        if val is not None:
            colors[key] = to_hex(val)
            src[key] = f"{screen}:{what}"

    img, nodes = load("gmail-android", "gmail_inbox_default" + suffix)
    if img:
        n = find_node(nodes, text="Primary")
        if n:
            put("bg.app", surface_color(img, expand(n["bounds"], dx=200, dy=30)),
                "inbox_default", "Primary 라벨 주변")
            put("text.section", text_color(img, n["bounds"]), "inbox_default", "Primary 라벨")
        row = find_node(nodes, desc_has="Anthropic Team") or find_node(nodes, desc_has="Terms of Service")
        if row:
            l, t, r, b = row["bounds"]
            put("bg.surface", surface_color(img, (l + 40, t + 20, l + 180, b - 20)),
                "inbox_default", "메일 행 좌측")
        sender = find_node(nodes, text="Anthropic Team") or find_node(nodes, text="Google")
        if sender:
            put("text.primary", text_color(img, sender["bounds"]), "inbox_default", "발신자명")
        snip = find_node(nodes, text_re=r"^We're writing|^Learn more|claudeeating")
        if snip:
            put("text.secondary", text_color(img, snip["bounds"]), "inbox_default", "스니펫")
            put("bg.surface", surface_color(img, expand(snip["bounds"], dx=40, dy=22)),
                "inbox_default", "스니펫 주변 카드(중앙값)")
        comp = find_node(nodes, text="Compose")
        if comp:
            put("accent.fab.bg", surface_color(img, expand(comp["bounds"], dx=-10, dy=10)),
                "inbox_default", "Compose FAB")
            put("accent.fab.fg", text_color(img, comp["bounds"]), "inbox_default", "Compose 라벨")
        sb = find_node(nodes, text="Search in mail")
        if sb:
            put("bg.searchbar", surface_color(img, expand(sb["bounds"], dx=150, dy=20)),
                "inbox_default", "검색바")
            put("text.hint",
                text_color_directional(img, sb["bounds"], darker_text=(suffix == "_light"),
                                       band=(0.02, 0.10)),
                "inbox_default", "검색 힌트(방향성 2~10% 밴드)")
        link = find_node(nodes, text="Add recovery info")
        if link:
            put("accent.link",
                text_color_directional(img, link["bounds"], darker_text=(suffix == "_light")),
                "inbox_default", "텍스트 버튼(방향성 백분위)")

    img, nodes = load("gmail-android", "gmail_inbox_swipe_archive_undo_snackbar" + suffix)
    if img:
        n = find_node(nodes, text_re=r"archived")
        if n:
            put("bg.snackbar", surface_color(img, expand(n["bounds"], dx=60, dy=20)),
                "swipe_snackbar", "스낵바")
            put("text.snackbar",
                text_color_directional(img, n["bounds"], darker_text=(suffix != "_light")),
                "swipe_snackbar", "스낵바 텍스트(반전 서피스: 다크모드→어두운 글자)")
        u = find_node(nodes, text="Undo")
        if u:
            put("accent.snackbar.action",
                text_color_directional(img, u["bounds"], darker_text=(suffix != "_light")),
                "swipe_snackbar", "Undo(반전 서피스 방향성)")

    img, nodes = load("gmail-android", "gmail_detail_overflow_menu" + suffix)
    if img:
        n = find_node(nodes, text="Report spam")
        if n:
            put("semantic.danger", text_color(img, n["bounds"]), "overflow_menu", "Report spam")
        m = find_node(nodes, text="Snooze")
        if m:
            put("bg.menu", surface_color(img, expand(m["bounds"], dx=100, dy=30)),
                "overflow_menu", "메뉴 서피스")

    img, nodes = load("gmail-android", "gmail_navigation_drawer_open" + suffix)
    if img:
        n = find_node(nodes, text="Primary")
        if n:
            put("bg.selected", surface_color(img, expand(n["bounds"], dx=30, dy=8)),
                "drawer", "선택 항목 필")
            put("text.selected", text_color(img, n["bounds"]), "drawer", "선택 항목 라벨")
    return colors, src


def naver_mode(suffix):
    colors, src = {}, {}

    def put(key, val, screen, what):
        if val is not None:
            colors[key] = to_hex(val)
            src[key] = f"{screen}:{what}"

    img, nodes = load("navermail-android", "navermail_inbox_default" + suffix)
    if img:
        title = find_node(nodes, text="받은메일함", y_range=(120, 320))
        if title:
            put("bg.app", surface_color(img, expand(title["bounds"], dx=120, dy=40)),
                "inbox_default", "타이틀 주변")
            put("text.primary", text_color(img, title["bounds"]), "inbox_default", "타이틀")
        cnt = find_node(nodes, text="999+", y_range=(120, 340))
        if cnt:
            put("accent.count", text_color(img, cnt["bounds"]), "inbox_default", "999+ 카운트")
        chip_sel = find_node(nodes, text="받은메일함", y_range=(330, 500))
        if chip_sel:
            put("chip.selected.bg", surface_color(img, expand(chip_sel["bounds"], dx=-6, dy=-6)),
                "inbox_default", "선택 칩")
            put("chip.selected.fg", text_color(img, chip_sel["bounds"]), "inbox_default", "선택 칩 라벨")
        chip_un = find_node(nodes, text="프로모션", y_range=(330, 500))
        if chip_un:
            put("chip.bg", surface_color(img, expand(chip_un["bounds"], dx=-6, dy=-6)),
                "inbox_default", "비선택 칩")
            put("chip.fg", text_color(img, chip_un["bounds"]), "inbox_default", "비선택 칩 라벨")
        sender = find_node(nodes, text="LinkedIn") or find_node(nodes, text="Steam")
        if sender:
            put("text.sender", text_color(img, sender["bounds"]), "inbox_default", "발신자명")
            l, t, r, b = sender["bounds"]
            put("bg.surface", surface_color(img, (l, b + 130, l + 500, b + 170)),
                "inbox_default", "행 프리뷰 아래 여백")
        snip = find_node(nodes, text_re=r"아래처럼 해보고|메시지를 보는")
        if snip:
            put("text.secondary", text_color(img, snip["bounds"]), "inbox_default", "프리뷰")

    img, nodes = load("navermail-android", "navermail_inbox_swipe_action_reveal" + suffix)
    if img:
        d = find_node(nodes, text="삭제")
        if d:
            put("semantic.danger.bg", surface_color(img, expand(d["bounds"], dx=40, dy=60)),
                "swipe_reveal", "삭제 버튼")
            put("semantic.danger.fg", text_color(img, d["bounds"]), "swipe_reveal", "삭제 라벨")
        u = find_node(nodes, text="안읽음")
        if u:
            put("action.neutral.bg", surface_color(img, expand(u["bounds"], dx=40, dy=60)),
                "swipe_reveal", "안읽음 버튼")

    # FAB: XML에 라벨 없음 → 실측 고정 위치(1281,2779) 중심 샘플
    img, _ = load("navermail-android", "navermail_inbox_default" + suffix)
    if img:
        cx, cy = 1281, 2779
        fab = surface_color(img, (cx - 50, cy - 50, cx + 50, cy + 50), step=2)
        put("accent.fab.bg", fab, "inbox_default", "쓰기 FAB 실측좌표")

    img, nodes = load("navermail-android", "navermail_drawer_mailbox_list" + suffix)
    if img:
        sel = find_node(nodes, text_re=r"^받은메일함")
        if sel:
            put("drawer.selected.fg", text_color(img, sel["bounds"]), "drawer", "선택 메일함")
        allm = find_node(nodes, text="전체메일")
        if allm:
            put("drawer.fg", text_color(img, allm["bounds"]), "drawer", "일반 메일함")
            put("bg.drawer", surface_color(img, expand(allm["bounds"], dx=100, dy=30)),
                "drawer", "드로어 서피스")
    return colors, src


def build(app_fn, name):
    dark, dark_src = app_fn("")
    light, light_src = app_fn("_light")
    # 라이트 미캡처(갭) 키는 다크 값 승계 — 시맨틱 색은 테마 불변인 경우가 일반적
    for k in dark:
        if k not in light:
            light[k] = dark[k]
            light_src[k] = "inherited:dark(라이트 캡처 갭 — WiFi 백필 후 재추출)"
    # 힌트: 글리프가 영역의 2% 미만이라 픽셀 통계 불안정 → 보조 텍스트 그레이 별칭(머티리얼 onSurfaceVariant 규칙)
    for mode, msrc in ((dark, dark_src), (light, light_src)):
        if "text.hint" in mode and "text.secondary" in mode:
            mode["text.hint"] = mode["text.secondary"]
            msrc["text.hint"] = "derived:text.secondary 별칭(힌트=보조 그레이 규칙 — 직접 샘플 불안정)"
    # 반전 서피스(스낵바) 텍스트: 추출값이 배경과 동일하면(샘플 실패) 반대 모드 text.primary로 유도
    for mode, other in ((dark, light), (light, dark)):
        if "bg.snackbar" in mode and \
                mode.get("text.snackbar") == mode.get("bg.snackbar") and other.get("text.primary"):
            mode["text.snackbar"] = other["text.primary"]
            (dark_src if mode is dark else light_src)["text.snackbar"] = \
                "derived:반대모드 text.primary(반전 서피스 규칙 — 직접 샘플 실패 폴백)"
    mono = {}
    for k, v in dark.items():
        rgb = tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))
        from lib import desaturate as ds
        mono[k] = to_hex(ds(rgb))
    return {
        "$meta": {
            "source": "Galaxy S25U 실캡처 (references/*/raw), density 600dpi, 384dp 기준",
            "method": "surface=영역 중앙값, text=배경대비 상위8% 중앙값, mono=다크 휘도보존 그레이스케일 파생",
            "generated_by": "pipeline/distill/extract_tokens.py",
            "sampled_from": {"dark": dark_src, "light": light_src},
        },
        "dark": dark,
        "light": light,
        "mono": mono,
    }


if __name__ == "__main__":
    out_dir = os.path.join(ROOT, "references", "tokens")
    os.makedirs(out_dir, exist_ok=True)
    gugol = build(gmail_mode, "gugol")
    never = build(naver_mode, "never")
    json.dump(gugol, open(os.path.join(out_dir, "gugol.tokens.json"), "w"),
              ensure_ascii=False, indent=2)
    json.dump(never, open(os.path.join(out_dir, "never.tokens.json"), "w"),
              ensure_ascii=False, indent=2)
    for name, t in (("gugol", gugol), ("never", never)):
        print(f"[{name}] dark {len(t['dark'])}색 / light {len(t['light'])}색 / mono {len(t['mono'])}색")
        missing = [k for k in t["dark"] if k not in t["light"]]
        if missing:
            print(f"  ⚠ light 누락 키: {missing}")
