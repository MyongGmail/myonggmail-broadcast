"""방송 생성기 v0 — 서킷 3(방송)의 실체 (P1b).

docs/BROADCAST_FORMAT.md §2의 라인 포맷을 구현한다:
    헤더  v0|d1|{생성일 epoch일 base36}
    행    {채널코드}|{글번호코드}|{날짜코드}|{태그코드}|{제목}

- 채널·태그 코드의 의미 사전은 전송로에 싣지 않는다(전송로 무의미화 — OPTIMALITY 보조정리 6).
  사전은 dict_d1.json으로 별도 산출되어 앱에 내장된다.
- K2Web 채널(전체의 ~88%)은 artclSeq만 base36으로 보내고 폰이 URL 템플릿에 꽂아 조립한다.
  비K2Web 채널은 template=null — 행 코드는 식별용(crc32 base36)이고 앱은 채널 대표
  board_url로 폴백한다(원문 직링크 대신 게시판 랜딩 — v0의 문서화된 절충).

- 방송은 **창(window)이지 아카이브가 아니다**: --since-days로 최근 N일만 싣는다. 잘라낸 과거는
  앱 번들 코퍼스(packages/shared/data/notices_v2.json, 2014~ 3,001행)가 계속 들고 있고
  buildInbox가 언제나 병합하므로, 창을 좁혀도 단말에서 과거가 사라지지 않는다.

사용:  python3 -m pipeline.broadcast.generate [--input 경로] [--fresh 경로] [--out 디렉터리]
                                             [--since-days N] [--verify]
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import zlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = REPO / "packages/shared/data/notices_v2.json"
DEFAULT_FRESH = REPO / "pipeline/snapshots/parsed/notices_v2.json"
DEFAULT_OUT = REPO / "pipeline/broadcast/out"

FORMAT_VERSION = "v0"

# ── 수집원 층위(source_tier) — 2026-07-28 신설 ──────────────────────────────
#
# 학교 밖으로 수집원을 넓히면서 생긴 구분이다. robots.txt는 기계적 허용만 말할 뿐
# **ToS·DB권**을 말하지 않는데, 상용 애그리게이터의 목록을 전원에게 방송하는 것과
# 학교 자기 공지를 방송하는 것은 법적 성격이 다르다.
#
# 해법: 층위별로 **방송 파일을 분리**하고 앱이 켠 층위만 받아 간다.
#   - 비용 헌법 유지: 층위당 공용 1벌, 사용자별 사전생성 0
#   - ToS 완화: 상용 층위는 명시 선택자에게만 전송되고, 문제 시 파일 1개만 내리면 끝
#
# ★ 사전(dict)도 층위마다 독립이다. 채널 코드는 sorted(channel_keys)의 **인덱스**라
#   한 사전에 채널을 추가하면 뒤 코드가 전부 밀린다(=배포된 앱이 다른 게시물을 가리킨다).
#   층위를 쪼개 두면 공공 채널을 늘려도 school 사전은 바이트 동일 — 현장 APK가 계속 산다.
TIER_DICTS = {"school": "d1", "public": "p1", "commercial": "c1"}
DEFAULT_TIER = "school"
# school은 기존 경로(out/latest.txt)를 그대로 쓴다 — 배포된 앱의 URL이 안 바뀌게.
TIER_SUBDIR = {"school": "", "public": "public", "commercial": "commercial"}

DICT_VERSION = TIER_DICTS[DEFAULT_TIER]  # 하위호환 별칭(기존 참조부)

# 방송 창 기본값 — 최근 2년(2026-07-27 실측: 21,190행 중 8,872행 = 41.9% 잔존).
#
# 왜 하한이 필요한가: 코퍼스가 2006-11-30까지 소급해 있고 매주 backfill이 단조 증가시킨다.
# 방송은 "새 공지가 전원에게 닿는 통로"이지 20년 아카이브 배포가 아니다 — 존치 근거가
# 학생 데이터 요금 배려(BROADCAST_FORMAT §2)인데 그 취지와 정면으로 어긋난다.
#
# 왜 하필 2년: ① 연례 공지(장학 신청·계절학기)를 두 주기 덮어 "작년엔 언제였나"가 성립한다
# ② 90일(4.9%)·365일(20.9%)은 재등장 비교가 끊긴다 ③ 잘라낸 과거는 앱 번들이 들고 있다.
DEFAULT_SINCE_DAYS = 730

# 창 적용 후 이 행수 미만이면 중단 — 잘못된 --since-days가 방송을 조용히 비우는 사고 방지.
# 정상 운영에서는 절대 걸리지 않는다(90일 창조차 1,031행). --allow-small로 명시 해제.
MIN_ROWS = 500

# 태그 코드네임 — 카테고리 키의 고정 2자 사전(BROADCAST_FORMAT §2 예시 'mi'·'ca'와 일치)
TAG_CODES = {
    "academics": "ac", "scholarship": "sc", "career": "ca", "contest": "co",
    "events": "ev", "life": "li", "jobs_on_campus": "jo", "admin": "ad",
    "procurement": "pr", "mixed": "mi",
}
# 크롤러 category_hint 별칭(앱 mail.ts HINT_ALIASES와 동일 규칙)
HINT_ALIASES = {"activities": "contest"}

K2_URL = re.compile(r"^(https://[^/]+/bbs/[^/]+/\d+)/(\d+)/artclView\.do")

B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def b36(n: int, width: int = 0) -> str:
    if n == 0:
        s = "0"
    else:
        digits = []
        while n:
            n, r = divmod(n, 36)
            digits.append(B36[r])
        s = "".join(reversed(digits))
    return s.rjust(width, "0")


def b36_decode(s: str) -> int:
    return int(s, 36)


def epoch_days(iso: str) -> int:
    return (date.fromisoformat(iso[:10]) - date(1970, 1, 1)).days


def row_date(row: dict) -> str | None:
    # date null 31건(mjuecon·ddingdong)은 scraped_at 폴백 — 앱과 동일 규칙
    return row.get("date") or (row.get("scraped_at") or "")[:10] or None


def load_rows(input_path: Path, fresh_path: Path | None) -> list[dict]:
    """기저(번들 코퍼스) ∪ 신선 오버레이(크롤 병합 산출) — dedup_key 기준, 오버레이 우선.

    공개 레포(C3)에는 snapshots/가 없어 오버레이는 Actions 캐시에 누적된 최근 스캔분뿐이다 —
    기저를 버리고 교체하면 방송이 코퍼스를 잃는다. 그래서 교체가 아니라 병합.
    캐시 증발 시 오버레이가 작아질 뿐 기저 이하로 줄지 않고, 일요일 backfill이 매주 재적재한다.
    """
    base = json.loads(input_path.read_text(encoding="utf-8"))
    merged = {r["dedup_key"]: r for r in base}
    if fresh_path is not None and fresh_path.exists() and fresh_path.resolve() != input_path.resolve():
        fresh = json.loads(fresh_path.read_text(encoding="utf-8"))
        new_n = sum(1 for r in fresh if r["dedup_key"] not in merged)
        merged.update({r["dedup_key"]: r for r in fresh})
        fresh_note = f"신선 {len(fresh)}행(신규 {new_n})"
    else:
        fresh_note = "신선 없음(스킵)"
    rows = list(merged.values())
    max_scraped = max((r.get("scraped_at") or "" for r in rows), default="")
    print(f"input: 기저 {len(base)}행 + {fresh_note} → 병합 {len(rows)}행, max_scraped={max_scraped or '?'}")
    return rows


def tier_of_channel() -> dict[str, str]:
    """channel_key → source_tier. 레지스트리에 없거나 미지정이면 school(=기존 동작)."""
    out: dict[str, str] = {}
    cfg_dir = REPO / "pipeline" / "config" / "schools"
    for path in sorted(cfg_dir.glob("mju*.json")):
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for ch in cfg.get("channels", []):
            tier = ch.get("source_tier", DEFAULT_TIER)
            if tier not in TIER_DICTS:
                raise SystemExit(f"{path.name}: 알 수 없는 source_tier '{tier}' (채널 {ch.get('key')})")
            out[ch["key"]] = tier
    return out


def channel_fingerprint(channel_keys: list[str]) -> str:
    """채널 집합의 지문 — 사전 버전에 박아 **코드 밀림 사고**를 안전 실패로 바꾼다.

    채널 코드는 sorted(channel_keys)의 인덱스라, 채널이 하나만 늘어도 뒤 코드가 전부 밀린다.
    그런데 앱(broadcast.ts:27)은 사전 '버전 문자열'만 대조하므로, 버전이 고정 'd1'이면
    **밀린 코드를 그대로 받아들여 엉뚱한 게시물 URL을 조립한다.**

    실제로 2026-07-28 인구조사가 채널을 113→115로 늘리면서 115개 코드 중 **81개**가 밀렸다.
    지문이 없었다면 현장 APK가 조용히 오작동했을 것이다.

    지문을 넣으면 채널 집합이 바뀌는 순간 버전이 달라지고, 앱은 방송을 통째로 폐기하고
    번들 코퍼스로 폴백한다 — broadcast.ts가 이미 문서화한 방침("부분 오해보다 번들 폴백이 정직").

    ★ 입력은 **레지스트리 채널 키**다(2026-07-28 교정). 코퍼스에 행이 있는 채널로 지문을
      계산하면 지문 자체가 데이터에 의존해서, 창·부분 코퍼스·콜드 캐시가 지문을 흔든다 —
      즉 가드가 스스로 오발한다. 안전 실패라도 전 단말 방송 폐기라 대가가 같다.
      설정에서 계산하면 지문이 바뀌는 계기가 "사람이 채널을 등록하는 커밋" 하나로 줄어든다.
      build()의 사전 조립 주석 참조.
    """
    import hashlib

    h = hashlib.sha256("\n".join(channel_keys).encode("utf-8")).hexdigest()
    return h[:6]


def build(
    rows: list[dict],
    out_dir: Path,
    since_days: int = DEFAULT_SINCE_DAYS,
    allow_small: bool = False,
    tier: str = DEFAULT_TIER,
    registry: dict[str, str] | None = None,
) -> dict:
    """registry(channel_key→tier)를 주입하면 그것을 쓰고, 없으면 레지스트리 파일을 읽는다.

    주입 가능해야 하는 이유: 사전이 설정 파생이 된 순간 build()의 출력이 **레포의 실제
    설정 파일**에 의존한다. 주입구가 없으면 테스트가 자기 입력을 통제하지 못하고(합성 채널이
    전부 미등록으로 걸러진다), 다학교로 갈 때 mju 하드코딩이 그대로 남는다.
    """
    # ⚠️ 채널 사전은 **레지스트리(설정)에서** 만든다 — 코퍼스 행이 아니라.
    #
    # 채널 코드는 sorted(channel_keys)의 인덱스다. 그 입력을 "행이 있는 채널"로 두면 사전이
    # **데이터에 의존**하게 되어, 코퍼스가 흔들릴 때마다 코드가 밀린다:
    #   · 창 때문에 어떤 채널의 행이 전멸 → 그 채널이 빠지고 뒤 코드가 한 칸씩 밀린다
    #   · 공개 레포 Actions 캐시가 비어 부분 코퍼스로 돌면 채널 집합이 통째로 달라진다
    #
    # 지문이 그 사고를 안전 실패로 바꾸긴 한다(channel_fingerprint 참조). 그러나 안전 실패도
    # **방송 전량 폐기**다 — 콜드 캐시 한 번에 전 단말이 번들 폴백으로 떨어지고, 에러도
    # 배너도 없어서 "새 공지가 없는 앱"과 화면상 구분되지 않는다.
    #
    # 레지스트리 파생이면 그 두 경로가 원천 봉쇄된다. 채널 집합이 git에 커밋된 설정이라
    # 코퍼스가 어떻든 불변이고, 집합이 바뀌는 유일한 계기는 **사람이 채널을 등록하는 커밋**이다.
    # 그때 지문이 바뀌는 건 사고가 아니라 의도다 — 그 커밋과 APK 재빌드가 한 쌍이다.
    registry = tier_of_channel() if registry is None else registry
    channel_keys = sorted(k for k, t in registry.items() if t == tier)
    if not channel_keys:
        raise SystemExit(
            f"레지스트리에 층위 '{tier}' 채널이 0개다 — pipeline/config/schools/mju*.json을 읽었는가?"
        )
    if len(channel_keys) > 36 * 36:
        raise SystemExit("채널 수가 2자 base36 공간을 초과")
    # 사전 버전 = 층위 접두 + 채널 집합 지문. 파일명은 층위 접두로 고정(앱 import 경로 안정),
    # 대조되는 version 문자열만 지문을 포함한다.
    dict_slot = TIER_DICTS[tier]
    dict_version = f"{dict_slot}.{channel_fingerprint(channel_keys)}"

    # 미등록 채널의 행은 실을 코드가 없다. 조용히 버리지 않고 세어서 로그와 stats로 올린다.
    # 방송을 통째로 멈추지는 않는다 — 한 채널의 등록 누락에 비해 전원 배포 중단은 과하다.
    known = set(channel_keys)
    unregistered = sorted({r["channel_key"] for r in rows if r["channel_key"] not in known})
    if unregistered:
        dropped = sum(1 for r in rows if r["channel_key"] not in known)
        print(
            f"[{tier}] ⚠️ 레지스트리 미등록 채널 {len(unregistered)}종 / {dropped}행 제외: "
            f"{unregistered[:5]}{'…' if len(unregistered) > 5 else ''} — mju*.json에 등록하면 "
            "다음 방송부터 실린다(등록은 지문을 바꾸므로 APK도 함께 굽는다)"
        )
        rows = [r for r in rows if r["channel_key"] in known]

    # 채널별 행 색인 — 사전 조립이 채널당 전량 스캔이면 146×21,734회 비교가 된다.
    rows_by_channel: dict[str, list[dict]] = {}
    for r in rows:
        rows_by_channel.setdefault(r["channel_key"], []).append(r)

    channels: dict[str, dict] = {}
    code_of: dict[str, str] = {}
    for i, key in enumerate(channel_keys):
        code = b36(i, 2)
        code_of[key] = code
        ch_rows = rows_by_channel.get(key, [])
        # 엄격 규칙: 채널 내 K2 URL 프리픽스가 단일할 때만 템플릿 부여.
        # 한 channel_key가 여러 fnct(게시판)를 묶는 채널이 실재(--verify가 검출) —
        # 잘못된 직링크 조립보다 게시판 랜딩 폴백이 정직하다.
        prefixes = {m.group(1) for r in ch_rows if (m := K2_URL.match(r.get("url") or ""))}
        template = f"{prefixes.pop()}/{{artcl}}/artclView.do" if len(prefixes) == 1 else None
        # 등록만 되고 아직 글이 0건인 채널(현재 31개 — "발견 시점 빈 게시판")은 관측할 URL이
        # 없다. 둘 다 None이면 앱이 게시판 랜딩으로 강등한다. 코드 자리는 유지되므로
        # **첫 글이 올라오는 날에도 사전이 흔들리지 않는다** — 이게 레지스트리 파생의 실익이다.
        url = (ch_rows[0].get("url") or "") if ch_rows else ""
        board_url = url.split("?")[0] if not template and url else None
        channels[code] = {"key": key, "template": template, "board_url": board_url}

    today = datetime.now(timezone.utc).date()
    # ISO 날짜는 사전순 = 시간순이라 문자열 비교로 충분하다(파싱 실패 경로를 만들지 않는다).
    cutoff = (today - timedelta(days=since_days)).isoformat() if since_days > 0 else ""

    lines = [f"{FORMAT_VERSION}|{dict_version}|{b36(epoch_days(today.isoformat()))}"]
    skipped = 0
    aged_out = 0
    linked = 0
    ordered = sorted(rows, key=lambda r: row_date(r) or "", reverse=True)
    for r in ordered:
        d = row_date(r)
        if d is None:
            skipped += 1
            continue
        if cutoff and d < cutoff:
            aged_out += 1
            continue
        m = K2_URL.match(r.get("url") or "")
        if m and channels[code_of[r["channel_key"]]]["template"]:
            artcl = b36(int(m.group(2)))
            linked += 1
        else:
            artcl = b36(zlib.crc32((r.get("url") or r["dedup_key"]).encode()))
        hint = r.get("category_hint") or "mixed"
        tag = TAG_CODES.get(HINT_ALIASES.get(hint, hint), TAG_CODES["mixed"])
        title = (r.get("title") or "").replace("\n", " ").replace("|", "·").strip()
        lines.append(f"{code_of[r['channel_key']]}|{artcl}|{b36(epoch_days(d), 3)}|{tag}|{title}")

    emitted = len(lines) - 1
    if emitted < MIN_ROWS and not allow_small:
        raise SystemExit(
            f"방송 행 {emitted}건 < 하한 {MIN_ROWS}건 — --since-days({since_days})가 너무 좁거나 "
            f"입력이 비었다. 의도한 것이면 --allow-small."
        )

    text = "\n".join(lines) + "\n"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.txt").write_text(text, encoding="utf-8")
    raw = text.encode("utf-8")
    gz = gzip.compress(raw, mtime=0)  # mtime 고정 — 동일 입력 = 동일 산출(재현성)
    (out_dir / "latest.txt.gz").write_bytes(gz)
    dictionary = {"version": dict_version, "channels": channels, "tags": {v: k for k, v in TAG_CODES.items()}}
    (out_dir / f"dict_{dict_slot}.json").write_text(
        json.dumps(dictionary, ensure_ascii=False, indent=1), encoding="utf-8",
    )
    return {
        "rows": emitted, "skipped_no_date": skipped,
        "since_days": since_days, "cutoff": cutoff or None, "aged_out": aged_out,
        "k2_linked": linked, "channels": len(channels),
        "dict_version": dict_version, "unregistered_channels": len(unregistered),
        "raw_bytes": len(raw), "gzip_bytes": len(gz),
        "bytes_per_row": round(len(raw) / max(1, emitted), 1),
    }


def verify(out_dir: Path, rows: list[dict], tier: str = DEFAULT_TIER) -> None:
    """왕복 검증: 파싱 복원 + K2 URL 재조립이 원본(병합 입력)과 일치하는지 표본 검사."""
    text = (out_dir / "latest.txt").read_text(encoding="utf-8").rstrip("\n").split("\n")
    header = text[0].split("|")
    assert header[0] == FORMAT_VERSION and header[1].startswith(TIER_DICTS[tier] + "."), \
        f"헤더 사전 버전 불일치: {header[1]}"
    dictionary = json.loads((out_dir / f"dict_{TIER_DICTS[tier]}.json").read_text(encoding="utf-8"))
    by_key: dict[tuple, dict] = {}
    for r in rows:
        m = K2_URL.match(r.get("url") or "")
        if m and row_date(r):
            by_key[(r["channel_key"], int(m.group(2)))] = r
    checked = 0
    for line in text[1:]:
        ch_code, artcl, d, tag, title = line.split("|", 4)
        ch = dictionary["channels"][ch_code]
        assert tag in dictionary["tags"], f"미등록 태그 코드: {tag}"
        if ch["template"]:
            url = ch["template"].replace("{artcl}", str(b36_decode(artcl)))
            src = by_key.get((ch["key"], b36_decode(artcl)))
            if src is not None:
                assert url == src["url"], f"URL 재조립 불일치: {url} != {src['url']}"
                assert title == src["title"].replace("\n", " ").replace("|", "·").strip()
                checked += 1
    assert checked > 0, "재조립 검증 표본 0건"
    print(f"verify OK [{tier}] — 재조립 일치 {checked}건, 행 {len(text) - 1}건")


def main() -> None:
    ap = argparse.ArgumentParser(description="방송 파일 v0 생성")
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--fresh", type=Path, default=DEFAULT_FRESH,
                    help="크롤 병합 산출 오버레이 — 부재 시 기저만으로 생성")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS,
                    help=f"방송 창(일). 0=하한 없음(전량). 기본 {DEFAULT_SINCE_DAYS}일 "
                         "— 잘라낸 과거는 앱 번들 코퍼스가 계속 들고 있다")
    ap.add_argument("--allow-small", action="store_true",
                    help=f"창 적용 후 {MIN_ROWS}행 미만이어도 진행(사고 방지 가드 해제)")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--tier", choices=[*TIER_DICTS, "all"], default="all",
                    help="생성할 수집원 층위. all=채널이 존재하는 층위 전부(기본)")
    args = ap.parse_args()
    rows = load_rows(args.input, args.fresh)

    tier_map = tier_of_channel()
    wanted = list(TIER_DICTS) if args.tier == "all" else [args.tier]
    made = 0
    for tier in wanted:
        # 레지스트리에 없는 채널(구 스냅샷 등)은 school로 본다 — 기존 동작 보존
        tier_rows = [r for r in rows if tier_map.get(r["channel_key"], DEFAULT_TIER) == tier]
        out_dir = args.out / TIER_SUBDIR[tier] if TIER_SUBDIR[tier] else args.out
        if not tier_rows:
            print(f"[{tier}] 채널 0 — 생성 스킵")
            continue
        # 하한 가드는 school에만 건다. 신설 층위는 처음엔 작을 수밖에 없고,
        # 거기에 500행 하한을 걸면 확장이 시작부터 막힌다.
        allow_small = args.allow_small or tier != DEFAULT_TIER
        # 이미 읽은 tier_map을 그대로 넘긴다 — build가 같은 파일을 다시 읽으면
        # 두 번의 읽기 사이에 설정이 달라질 여지가 생긴다(층위 분배와 사전이 어긋난다).
        stats = build(tier_rows, out_dir, args.since_days, allow_small, tier, registry=tier_map)
        print(f"[{tier}] " + json.dumps(stats, ensure_ascii=False))
        if args.verify:
            verify(out_dir, tier_rows, tier)
        made += 1
    if made == 0:
        raise SystemExit("생성된 층위 0 — 입력이나 --tier를 확인하라")


if __name__ == "__main__":
    main()
