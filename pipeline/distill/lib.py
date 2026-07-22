"""공통 유틸: uiautomator XML 파싱 + PNG 영역 샘플링.

원칙: 스크린샷이 시각 기준 원본, XML은 bounds 보조.
- surface(배경) 색 = 영역 픽셀의 중앙값 (텍스트는 소수라 중앙값이 배경을 대표)
- text 색 = 배경 대비 극단 백분위 (다크 배경→밝은 백분위, 라이트 배경→어두운 백분위)
"""
import re
from statistics import median
from PIL import Image

DENSITY = 600  # adb shell wm density 실측
PX_PER_DP = DENSITY / 160  # 3.75


def px2dp(px: float) -> float:
    return round(px / PX_PER_DP, 1)


def parse_nodes(xml_path):
    """uiautomator dump XML → [{text, desc, rid, cls, bounds:(l,t,r,b)}]"""
    src = open(xml_path, encoding="utf-8", errors="replace").read()
    nodes = []
    for m in re.finditer(r"<node[^>]*>", src):
        tag = m.group(0)

        def attr(name):
            a = re.search(name + r'="([^"]*)"', tag)
            return a.group(1) if a else ""

        b = re.search(r'bounds="\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]"', tag)
        if not b:
            continue
        nodes.append(
            dict(
                text=attr("text"),
                desc=attr("content-desc"),
                rid=attr("resource-id"),
                cls=attr("class"),
                bounds=tuple(int(b.group(i)) for i in range(1, 5)),
            )
        )
    return nodes


def find_node(nodes, text=None, desc_has=None, rid_has=None, y_range=None, text_re=None):
    for n in nodes:
        if text is not None and n["text"] != text:
            continue
        if text_re is not None and not re.search(text_re, n["text"]):
            continue
        if desc_has is not None and desc_has not in n["desc"]:
            continue
        if rid_has is not None and rid_has not in n["rid"]:
            continue
        if y_range is not None:
            t = n["bounds"][1]
            if not (y_range[0] <= t <= y_range[1]):
                continue
        return n
    return None


def _pixels(img, box, step=3):
    l, t, r, b = box
    l, t = max(l, 0), max(t, 0)
    r, b = min(r, img.width), min(b, img.height)
    px = img.load()
    out = []
    for y in range(t, b, step):
        for x in range(l, r, step):
            p = px[x, y]
            out.append(p[:3])
    return out


def surface_color(img, box, step=3):
    """영역 중앙값 색 (배경/서피스)."""
    pts = _pixels(img, box, step)
    if not pts:
        return None
    return tuple(int(median([p[c] for p in pts])) for c in range(3))


def text_color(img, box, step=2):
    """텍스트 색: 배경(중앙값) 대비 거리 상위 10% 픽셀의 중앙값."""
    pts = _pixels(img, box, step)
    if not pts:
        return None
    bg = tuple(median([p[c] for p in pts]) for c in range(3))
    scored = sorted(pts, key=lambda p: sum((p[c] - bg[c]) ** 2 for c in range(3)))
    top = scored[int(len(scored) * 0.92):]  # 상위 8% 극단
    if not top:
        return None
    return tuple(int(median([p[c] for p in top])) for c in range(3))


def text_color_directional(img, box, darker_text: bool, shrink=0.2, pct=0.06, band=None):
    """휘도 방향 지정 텍스트 색: 라이트 배경→최저 휘도 쪽, 다크 배경→최고 휘도 쪽.
    영역을 shrink 비율만큼 안쪽으로 줄여 인접 서피스 오염을 차단.
    band=(lo,hi) 지정 시 극단 pct 대신 해당 백분위 구간의 중앙값 사용 —
    글리프 안티앨리어스 코어(과추출)가 아닌 지각 색을 원할 때."""
    l, t, r, b = box
    dw, dh = int((r - l) * shrink), int((b - t) * shrink)
    pts = _pixels(img, (l + dw, t + dh, r - dw, b - dh), step=1)
    if not pts:
        return None
    lum = sorted(pts, key=lambda p: 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2])
    if not darker_text:
        lum = lum[::-1]  # 밝은 쪽이 앞으로
    if band:
        lo, hi = int(len(lum) * band[0]), max(int(len(lum) * band[1]), 1)
        sel = lum[lo:hi]
    else:
        sel = lum[: max(1, int(len(lum) * pct))]
    if not sel:
        return None
    return tuple(int(median([p[c] for p in sel])) for c in range(3))


def contrast_ratio(hex1, hex2):
    """WCAG 2.x 대비율."""
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def rel_lum(h):
        r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

    l1, l2 = rel_lum(hex1), rel_lum(hex2)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def to_hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*rgb) if rgb else None


def desaturate(rgb):
    """mono 파생: 상대 휘도 유지 그레이스케일."""
    if rgb is None:
        return None
    y = int(0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2])
    return (y, y, y)


def expand(box, dx=0, dy=0):
    l, t, r, b = box
    return (l - dx, t - dy, r + dx, b + dy)
