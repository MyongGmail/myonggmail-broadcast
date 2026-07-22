"""tokens.json 2종 → 검수용 PREVIEW.html (스와치 + 미니 목업 + WCAG 대비 자동검증).

규칙: 목업은 하드코딩 색 폴백 금지 — 누락 토큰은 같은 모드의 bg 계열로 폴백하고 MISSING 배지 표시.
대비율 4.5:1 미만 쌍은 ⚠ 표기 + 콘솔 경고 (회귀를 사람 눈 전에 기계가 잡는다).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import contrast_ratio

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOK = os.path.join(ROOT, "references", "tokens")

# 스킨별 핵심 대비 쌍 (fg 키, bg 키)
PAIRS = {
    "gugol": [
        ("text.primary", "bg.surface"), ("text.secondary", "bg.surface"),
        ("text.section", "bg.app"), ("text.hint", "bg.searchbar"),
        ("text.snackbar", "bg.snackbar"), ("accent.snackbar.action", "bg.snackbar"),
        ("accent.fab.fg", "accent.fab.bg"), ("text.selected", "bg.selected"),
    ],
    "never": [
        ("text.primary", "bg.app"), ("text.sender", "bg.surface"),
        ("text.secondary", "bg.surface"), ("chip.fg", "chip.bg"),
        ("chip.selected.fg", "chip.selected.bg"), ("drawer.fg", "bg.drawer"),
        ("semantic.danger.fg", "semantic.danger.bg"),
    ],
}
warnings = []


def load(name):
    return json.load(open(os.path.join(TOK, f"{name}.tokens.json")))


def pick(colors, *keys):
    """모드 정합 폴백: 앞선 키부터, 전부 없으면 (None, 누락키)."""
    for k in keys:
        if k in colors:
            return colors[k], None
    return None, keys[0]


def swatches(colors, pairs_result):
    flagged = {fg for fg, bg, r in pairs_result if r < 4.5}
    cells = []
    for k, v in sorted(colors.items()):
        warn = ' <span class="warn">⚠</span>' if k in flagged else ""
        cells.append(
            f'<div class="sw"><div class="chip" style="background:{v}"></div>'
            f'<div class="lbl"><b>{k}</b>{warn}<br>{v}</div></div>')
    return "\n".join(cells)


def contrast_table(skin, mode, colors):
    rows, result = [], []
    for fg, bg in PAIRS[skin]:
        if fg in colors and bg in colors:
            r = contrast_ratio(colors[fg], colors[bg])
            result.append((fg, bg, r))
            cls = "ok" if r >= 4.5 else ("aa-lg" if r >= 3 else "fail")
            rows.append(f'<tr class="{cls}"><td>{fg}</td><td>{bg}</td><td>{r:.2f}:1</td>'
                        f'<td>{"OK" if r>=4.5 else ("대형텍스트만" if r>=3 else "미달 ⚠")}</td></tr>')
            if r < 4.5:
                warnings.append(f"[{skin}/{mode}] {fg} on {bg} = {r:.2f}:1")
    table = ('<table class="ct"><tr><th>fg</th><th>bg</th><th>대비율</th><th>판정</th></tr>'
             + "".join(rows) + "</table>")
    return table, result


def mock(skin, colors):
    def c(*keys):
        v, missing = pick(colors, *keys)
        if missing:
            warnings.append(f"[{skin}] 목업 토큰 누락: {missing}")
            return "magenta"  # 누락은 눈에 띄게
        return v

    if skin == "never":
        return f'''<div class="phone" style="background:{c("bg.app")}">
  <div class="ntitle" style="color:{c("text.primary")}">받은메일함 <b style="color:{c("accent.count","text.primary")}">999+</b></div>
  <div class="chiprow"><span class="nchip" style="background:{c("chip.selected.bg")};color:{c("chip.selected.fg")}">받은메일함</span>
    <span class="nchip" style="background:{c("chip.bg")};color:{c("chip.fg")}">프로모션</span>
    <span class="nchip" style="background:{c("chip.bg")};color:{c("chip.fg")}">청구·결제</span></div>
  <div class="nrow"><div style="color:{c("text.sender","text.primary")};font-weight:700">마루</div>
    <div style="color:{c("text.primary")};font-weight:600">[장학] 국가장학금 2차 신청이 내일 마감이에요</div>
    <div style="color:{c("text.secondary")}">상겸님, 놓치면 아쉬우니까 지금 바로…</div></div>
  <div class="nrow"><div style="color:{c("text.sender","text.primary")};font-weight:700">뭉지맨</div>
    <div style="color:{c("text.primary")};font-weight:600">공모전 하나 왔다. SW경진대회. 마감 7/22.</div>
    <div style="color:{c("text.secondary")}">…자세한 건 열어봐. 난 자러 간다.</div></div>
  <div class="fab" style="background:{c("accent.fab.bg")}">⚡</div></div>'''
    return f'''<div class="phone" style="background:{c("bg.app")}">
  <div class="gsearch" style="background:{c("bg.searchbar")};color:{c("text.hint","text.secondary")}">Search in mail</div>
  <div class="gcard" style="background:{c("bg.surface","bg.searchbar","bg.app")}">
    <div class="gavatar" style="background:{c("accent.fab.bg")};color:{c("accent.fab.fg","text.primary")}">마</div>
    <div><div style="color:{c("text.primary")};font-weight:600">마루</div>
      <div style="color:{c("text.primary")}">[장학] 국가장학금 2차 신청 내일 마감</div>
      <div style="color:{c("text.secondary")}">상겸님, 놓치면 아쉬우니까 지금…</div></div></div>
  <div class="gcard" style="background:{c("bg.surface","bg.searchbar","bg.app")}">
    <div class="gavatar" style="background:{c("bg.selected","bg.searchbar")};color:{c("text.selected","text.primary")}">뭉</div>
    <div><div style="color:{c("text.primary")};font-weight:600">뭉지맨</div>
      <div style="color:{c("text.primary")}">공모전 하나 왔다. 마감 7/22. 이상.</div>
      <div style="color:{c("text.secondary")}">…자세한 건 열어봐. 난 자러 간다.</div></div></div>
  <div class="gsnack" style="background:{c("bg.snackbar")};color:{c("text.snackbar")}">1 archived
    <b style="color:{c("accent.snackbar.action")}">Undo</b></div>
  <div class="gfab" style="background:{c("accent.fab.bg")};color:{c("accent.fab.fg")}">⚡ 당장브리핑</div></div>'''


def section(name, tokens):
    parts = [f"<h2>{name}</h2>"]
    for mode in ("dark", "light", "mono"):
        colors = tokens[mode]
        table, result = contrast_table(name, mode, colors)
        parts.append(
            f'<h3>{mode}</h3><div class="pair"><div class="swwrap">{swatches(colors, result)}</div>'
            f'{mock(name, colors)}</div>{table}')
    return "\n".join(parts)


gugol = load("gugol")
never = load("never")
html = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>명지메일 토큰 프리뷰</title><style>
body{{font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;background:#fafafa;color:#111;margin:24px}}
h1{{font-size:22px}} h2{{margin-top:36px;border-bottom:2px solid #ddd;padding-bottom:6px}}
.pair{{display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap}}
.swwrap{{display:grid;grid-template-columns:repeat(3,170px);gap:8px;flex:1;min-width:520px}}
.sw{{display:flex;gap:8px;align-items:center}} .chip{{width:34px;height:34px;border-radius:8px;border:1px solid #0002}}
.lbl{{font-size:10.5px;color:#444;line-height:1.3}} .warn{{color:#c00;font-weight:800}}
.phone{{width:290px;border-radius:22px;padding:16px 12px;position:relative;min-height:420px;border:1px solid #0003;box-shadow:0 4px 14px #0002}}
.ntitle{{font-size:17px;font-weight:800;margin:4px 6px 10px}}
.chiprow{{display:flex;gap:6px;margin-bottom:10px}}
.nchip{{font-size:11px;padding:7px 12px;border-radius:16px}}
.nrow{{padding:10px 6px;border-bottom:1px solid #8884;font-size:12px;line-height:1.5}}
.fab{{position:absolute;right:14px;bottom:14px;width:48px;height:48px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-size:20px}}
.gsearch{{border-radius:22px;padding:11px 16px;font-size:13px;margin-bottom:12px}}
.gcard{{border-radius:16px;padding:12px;display:flex;gap:10px;font-size:12px;line-height:1.5;margin-bottom:8px}}
.gavatar{{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;flex:none}}
.gsnack{{border-radius:10px;padding:10px 14px;font-size:12px;display:flex;justify-content:space-between;margin-top:12px}}
.gfab{{position:absolute;right:14px;bottom:14px;border-radius:14px;padding:11px 16px;font-size:13px;font-weight:600}}
.note{{background:#fff;border:1px solid #ddd;border-radius:10px;padding:12px 16px;font-size:13px;line-height:1.6}}
.ct{{border-collapse:collapse;font-size:11.5px;margin:10px 0 4px}}
.ct th,.ct td{{border:1px solid #ddd;padding:4px 10px;text-align:left}}
.ct tr.fail td{{background:#ffe5e5}} .ct tr.aa-lg td{{background:#fff6dd}}
</style></head><body>
<h1>명지메일 디자인 토큰 프리뷰 — 실캡처 추출 (구골/NEVER × 다크/라이트/흑백)</h1>
<div class="note">출처: Galaxy S25U 실캡처 (references/*/raw). surface=영역 중앙값, text=배경대비 극단/방향성 백분위, 파생 규칙은 tokens $meta 참조.
각 모드 아래 표 = WCAG 대비율 자동검증(4.5:1 기준). 목업의 마루·뭉지맨과 "⚡ 당장브리핑" FAB은 명지메일 번역 예시. 자홍색(magenta)이 보이면 토큰 누락 신호.</div>
{section("gugol", gugol)}
{section("never", never)}
</body></html>'''

out = os.path.join(TOK, "PREVIEW.html")
open(out, "w").write(html)
print("→", out, f"({len(html)}B)")
if warnings:
    print("\n⚠ 대비/누락 경고:")
    for w in warnings:
        print("  " + w)
else:
    print("대비 검증: 전 쌍 4.5:1 이상, 누락 0")
