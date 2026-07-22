"""방송 생성기 v0 — 서킷 3(방송)의 실체 (P1b).

docs/BROADCAST_FORMAT.md §2의 라인 포맷을 구현한다:
    헤더  v0|d1|{생성일 epoch일 base36}
    행    {채널코드}|{글번호코드}|{날짜코드}|{태그코드}|{제목}

- 채널·태그 코드의 의미 사전은 전송로에 싣지 않는다(전송로 무의미화 — OPTIMALITY 보조정리 6).
  사전은 dict_d1.json으로 별도 산출되어 앱에 내장된다.
- K2Web 채널(전체의 ~88%)은 artclSeq만 base36으로 보내고 폰이 URL 템플릿에 꽂아 조립한다.
  비K2Web 채널은 template=null — 행 코드는 식별용(crc32 base36)이고 앱은 채널 대표
  board_url로 폴백한다(원문 직링크 대신 게시판 랜딩 — v0의 문서화된 절충).

사용:  python3 -m pipeline.broadcast.generate [--input 경로] [--out 디렉터리] [--verify]
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import zlib
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = REPO / "packages/shared/data/notices_v2.json"
DEFAULT_OUT = REPO / "pipeline/broadcast/out"

DICT_VERSION = "d1"
FORMAT_VERSION = "v0"

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


def build(input_path: Path, out_dir: Path) -> dict:
    rows = json.loads(input_path.read_text(encoding="utf-8"))

    # 채널 사전: 정렬 키 → 2자 base36 코드(결정적). 템플릿은 채널 내 K2 URL에서 유도.
    channel_keys = sorted({r["channel_key"] for r in rows})
    if len(channel_keys) > 36 * 36:
        raise SystemExit("채널 수가 2자 base36 공간을 초과")
    channels: dict[str, dict] = {}
    code_of: dict[str, str] = {}
    for i, key in enumerate(channel_keys):
        code = b36(i, 2)
        code_of[key] = code
        ch_rows = [r for r in rows if r["channel_key"] == key]
        # 엄격 규칙: 채널 내 K2 URL 프리픽스가 단일할 때만 템플릿 부여.
        # 한 channel_key가 여러 fnct(게시판)를 묶는 채널이 실재(--verify가 검출) —
        # 잘못된 직링크 조립보다 게시판 랜딩 폴백이 정직하다.
        prefixes = {m.group(1) for r in ch_rows if (m := K2_URL.match(r.get("url") or ""))}
        template = f"{prefixes.pop()}/{{artcl}}/artclView.do" if len(prefixes) == 1 else None
        url = ch_rows[0].get("url") or ""
        board_url = url.split("?")[0] if not template and url else None
        channels[code] = {"key": key, "template": template, "board_url": board_url}

    lines = [f"{FORMAT_VERSION}|{DICT_VERSION}|{b36(epoch_days(datetime.now(timezone.utc).date().isoformat()))}"]
    skipped = 0
    linked = 0
    ordered = sorted(rows, key=lambda r: row_date(r) or "", reverse=True)
    for r in ordered:
        d = row_date(r)
        if d is None:
            skipped += 1
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

    text = "\n".join(lines) + "\n"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.txt").write_text(text, encoding="utf-8")
    raw = text.encode("utf-8")
    gz = gzip.compress(raw, mtime=0)  # mtime 고정 — 동일 입력 = 동일 산출(재현성)
    (out_dir / "latest.txt.gz").write_bytes(gz)
    dictionary = {"version": DICT_VERSION, "channels": channels, "tags": {v: k for k, v in TAG_CODES.items()}}
    (out_dir / f"dict_{DICT_VERSION}.json").write_text(
        json.dumps(dictionary, ensure_ascii=False, indent=1), encoding="utf-8",
    )
    return {
        "rows": len(lines) - 1, "skipped_no_date": skipped, "k2_linked": linked,
        "channels": len(channels),
        "raw_bytes": len(raw), "gzip_bytes": len(gz),
        "bytes_per_row": round(len(raw) / max(1, len(lines) - 1), 1),
    }


def verify(out_dir: Path, input_path: Path) -> None:
    """왕복 검증: 파싱 복원 + K2 URL 재조립이 원본과 일치하는지 표본 검사."""
    text = (out_dir / "latest.txt").read_text(encoding="utf-8").rstrip("\n").split("\n")
    header = text[0].split("|")
    assert header[0] == FORMAT_VERSION and header[1] == DICT_VERSION, "헤더 불일치"
    dictionary = json.loads((out_dir / f"dict_{DICT_VERSION}.json").read_text(encoding="utf-8"))
    rows = json.loads(input_path.read_text(encoding="utf-8"))
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
    print(f"verify OK — 재조립 일치 {checked}건, 행 {len(text) - 1}건")


def main() -> None:
    ap = argparse.ArgumentParser(description="방송 파일 v0 생성")
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    stats = build(args.input, args.out)
    print(json.dumps(stats, ensure_ascii=False))
    if args.verify:
        verify(args.out, args.input)


if __name__ == "__main__":
    main()
