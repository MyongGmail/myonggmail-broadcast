"""방송 생성기 계약 검증 — 조용한 오작동을 만드는 지점을 고정한다.

실행:  python3 -m unittest pipeline.broadcast.test_generate -v

왜 이것들을 보는가: 방송의 실패 양상은 "안 나온다"가 아니라 **"틀린 걸 맞다고 준다"** 다.
  ① 채널 집합이 바뀌면 코드가 밀리는데 사전 버전이 그대로면 앱이 엉뚱한 URL을 조립한다
     (2026-07-28 인구조사에서 실제 발생: 채널 113→115, 코드 115개 중 81개 밀림).
  ② 사전이 **코퍼스**에서 나오면 지문 자체가 데이터에 흔들려, 콜드 캐시 한 번에 전 단말이
     방송을 통째로 폐기한다. 사전은 레지스트리(설정)에서 나와야 한다.
  ③ 층위를 쪼갠 뒤 한 층위에 채널을 추가했는데 다른 층위 코드가 흔들리면 분리가 무의미하다.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .generate import DEFAULT_TIER, TIER_DICTS, build, channel_fingerprint


def reg(school_keys, **other_tiers) -> dict[str, str]:
    """레지스트리(channel_key→tier)를 만든다. reg(["a"], public=["p"]) 형태."""
    out = {k: DEFAULT_TIER for k in school_keys}
    for tier, keys in other_tiers.items():
        out.update({k: tier for k in keys})
    return out


def rows_for(channel_keys, n=3):
    """채널당 n행의 최소 입력 — K2 URL 형식이라야 template이 붙는다."""
    out = []
    for ck in channel_keys:
        for i in range(n):
            seq = 1000 + i
            out.append({
                "channel_key": ck,
                "dedup_key": f"{ck}:{seq}",
                "title": f"{ck} 공지 {i}",
                "url": f"https://www.mju.ac.kr/bbs/{ck}/1/{seq}/artclView.do",
                "date": "2026-07-01",
                "category_hint": "mixed",
                "scraped_at": "2026-07-01T00:00:00Z",
            })
    return out


def build_dict(rows, registry, tier=DEFAULT_TIER) -> dict:
    """build()를 임시 디렉터리에 돌리고 산출된 사전을 읽어 돌려준다."""
    with tempfile.TemporaryDirectory() as d:
        build(rows, Path(d), since_days=0, allow_small=True, tier=tier, registry=registry)
        return json.loads((Path(d) / f"dict_{TIER_DICTS[tier]}.json").read_text())


class FingerprintContract(unittest.TestCase):
    def test_같은_채널집합은_같은_지문(self):
        a = channel_fingerprint(["x", "y", "z"])
        self.assertEqual(a, channel_fingerprint(["x", "y", "z"]))

    def test_채널이_하나만_늘어도_지문이_바뀐다(self):
        """이게 깨지면 코드 밀림이 조용히 통과한다 — 이 저장소에서 실제로 일어났던 사고."""
        before = channel_fingerprint(["a", "b", "c"])
        after = channel_fingerprint(["a", "b", "c", "d"])
        self.assertNotEqual(before, after)

    def test_레지스트리에_채널이_늘면_코드가_밀리고_지문도_바뀐다(self):
        """지문이 왜 필요한지의 근거 — 추가된 채널이 앞쪽에 끼면 뒤가 전부 밀린다.

        이건 사고가 아니라 **의도된 변경**이다(사람이 채널을 등록하는 커밋). 그래서 그 커밋은
        APK 재빌드와 한 쌍이고, 버전이 달라져 구 APK가 안전하게 폐기하는 것이 정상 동작이다.
        """
        rows = rows_for(["a-one", "b-two", "c-three"])
        v1 = build_dict(rows, reg(["b-two", "c-three"]))
        v2 = build_dict(rows, reg(["a-one", "b-two", "c-three"]))
        # 코드 "00"이 가리키는 채널이 달라졌다 = 밀림 발생
        self.assertEqual(v1["channels"]["00"]["key"], "b-two")
        self.assertEqual(v2["channels"]["00"]["key"], "a-one")
        # 그리고 버전이 달라 앱이 이 방송을 폐기한다(안전 실패)
        self.assertNotEqual(v1["version"], v2["version"])


class RegistryDerivedDict(unittest.TestCase):
    """B3 — 사전은 코퍼스가 아니라 레지스트리에서 나온다."""

    def test_코퍼스가_비어도_사전이_불변이다(self):
        """콜드 캐시 회귀. 공개 레포 Actions 캐시가 비면 부분 코퍼스로 돌게 되는데,
        사전이 '행이 있는 채널'에서 나오면 그때 지문이 바뀌어 **전 단말이 방송을 폐기**한다.
        에러도 배너도 없어서 '새 공지가 없는 앱'과 화면상 구분되지 않는다."""
        registry = reg(["a-one", "b-two", "c-three"])
        full = build_dict(rows_for(["a-one", "b-two", "c-three"]), registry)
        partial = build_dict(rows_for(["b-two"]), registry)  # 두 채널의 행이 통째로 없다
        self.assertEqual(full["version"], partial["version"],
                         "코퍼스가 줄었다고 지문이 바뀌었다 — 콜드 캐시 한 번에 방송이 죽는다")
        self.assertEqual(
            [c["key"] for c in full["channels"].values()],
            [c["key"] for c in partial["channels"].values()],
            "코퍼스가 줄었다고 코드 배정이 달라졌다 — 앱이 엉뚱한 게시물을 가리킨다",
        )

    def test_행이_0건인_등록채널도_코드를_받는다(self):
        """'발견 시점 빈 게시판'(현재 31개)이 첫 글을 받는 날 사전이 흔들리면 안 된다.
        코드 자리를 미리 잡아 두는 것이 레지스트리 파생의 실익이다."""
        registry = reg(["empty-board", "has-rows"])
        d = build_dict(rows_for(["has-rows"]), registry)
        keys = {c["key"] for c in d["channels"].values()}
        self.assertIn("empty-board", keys, "행 0건 채널이 사전에서 빠졌다")
        empty = next(c for c in d["channels"].values() if c["key"] == "empty-board")
        # 관측할 URL이 없으므로 둘 다 None — 앱이 게시판 랜딩으로 강등한다(정직한 폴백)
        self.assertIsNone(empty["template"])
        self.assertIsNone(empty["board_url"])

    def test_미등록_채널의_행은_실리지_않는다(self):
        """코드 공간이 설정에서 오므로 미등록 채널은 실을 자리가 없다.
        조용히 버리지 않고 stats로 세어 올린다(CI 로그에 JSON으로 찍힌다)."""
        with tempfile.TemporaryDirectory() as d:
            stats = build(
                rows_for(["known", "ghost"]), Path(d),
                since_days=0, allow_small=True, registry=reg(["known"]),
            )
        self.assertEqual(stats["unregistered_channels"], 1)
        self.assertEqual(stats["rows"], 3, "미등록 채널의 행이 방송에 실렸다")

    def test_레지스트리가_비면_생성을_거부한다(self):
        """설정을 못 읽었는데 빈 사전으로 방송하면 전 단말이 폐기한다 — 조용히 진행 금지."""
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                build(rows_for(["a"]), Path(d), since_days=0, allow_small=True, registry={})


class TierIsolation(unittest.TestCase):
    def test_층위별_사전_슬롯이_다르다(self):
        self.assertEqual(len({*TIER_DICTS.values()}), len(TIER_DICTS))

    def test_public_채널_추가가_school_사전을_흔들지_않는다(self):
        """층위 분리의 존재 이유 — 공공 채널을 늘려도 school 사전은 바이트 동일이어야 한다.

        ⚠️ 이전 판은 같은 school 입력으로 build를 **두 번** 돌려 비교해서 층위 분배를
        전혀 실행하지 않았다(분배 코드를 통째로 지워도 통과했다). 이제 레지스트리에
        public 채널을 실제로 넣고, school 사전이 그것을 무시하는지를 본다.
        """
        rows = rows_for(["s-one", "s-two"])
        base = reg(["s-one", "s-two"])
        grown = reg(["s-one", "s-two"], public=["p-a", "p-b", "p-c"])
        a = build_dict(rows, base, tier="school")
        b = build_dict(rows, grown, tier="school")
        self.assertEqual(a["version"], b["version"],
                         "public 채널을 늘렸더니 school 지문이 바뀌었다 — 층위 분리가 무의미하다")
        self.assertEqual(a["channels"], b["channels"])

    def test_public은_자기_슬롯에_산출된다(self):
        grown = reg(["s-one"], public=["p-a", "p-b"])
        p = build_dict(rows_for(["p-a", "p-b"]), grown, tier="public")
        self.assertTrue(p["version"].startswith("p1."))
        self.assertEqual({c["key"] for c in p["channels"].values()}, {"p-a", "p-b"},
                         "public 사전에 school 채널이 섞였다")


if __name__ == "__main__":
    unittest.main()
