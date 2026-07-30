"""어댑터 평문 추출 계약 — bs4 유무로 데이터가 갈리지 않게 고정한다.

실행:  python3 -m unittest pipeline.crawler.v2.test_text_contract -v

무엇을 막는가: 어댑터마다 경로가 둘이다.
  · bs4 경로   — `node.get_text()` → 엔티티가 **풀린** 평문
  · 폴백 경로  — `strip_tags(...)` → 엔티티가 **그대로 남은** 평문
둘을 그냥 두면 같은 코드가 환경에 따라 다른 데이터를 만든다. 2026-07-28 인구조사가 bs4 없는
로컬에서 돌아 제목 721건에 `&lt;`·`&amp;`·`&apos;`가 실렸고, 크롤은 **성공으로 끝났다**.

이 계약이 네 어댑터에서 각각 독립적으로 깨졌다는 점이 중요하다 — 규칙을 문서에 적는 것으로는
막히지 않는다. `clean_text()`/`clean_body()`를 경유하게 만들고, 경유하지 않는 새 호출을
여기서 막는다.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ADAPTERS = Path(__file__).resolve().parent / "adapters"
BASE = ADAPTERS / "base.py"


class CleanTextContract(unittest.TestCase):
    def test_clean_text가_엔티티를_푼다(self):
        from .adapters.base import clean_text

        self.assertEqual(clean_text("<b>&lt;공지&gt;</b> A&amp;B"), "<공지> A&B")
        self.assertEqual(clean_text("&apos;따옴표&apos;"), "'따옴표'")
        self.assertEqual(clean_text("&#034;수치참조&#034;"), '"수치참조"')

    def test_태그제거가_엔티티복원보다_먼저다(self):
        """순서를 뒤집으면 본문의 `&lt;script&gt;`가 진짜 태그가 되어 지워진다."""
        from .adapters.base import clean_text

        self.assertEqual(clean_text("코드: &lt;script&gt;alert(1)&lt;/script&gt;"),
                         "코드: <script>alert(1)</script>")

    def test_clean_body는_개행을_접지_않는다(self):
        """bs4 경로가 get_text("\\n")으로 문단을 담는다 — 폴백만 접으면 또 갈린다."""
        from .adapters.base import clean_body, clean_text

        raw = "<p>첫째</p>\n<p>둘째 &amp; 셋째</p>"
        self.assertIn("\n", clean_body(raw))
        self.assertNotIn("\n", clean_text(raw))
        self.assertIn("&", clean_body(raw))  # 엔티티는 풀린다

    def test_strip_tags는_여전히_엔티티를_남긴다(self):
        """의도된 성질이다. 바뀌면 `unescape(strip_tags(...))` 호출부가 이중 복원된다."""
        from .adapters.base import strip_tags

        self.assertIn("&lt;", strip_tags("<b>&lt;</b>"))


class NoBareStripTags(unittest.TestCase):
    def test_어댑터가_strip_tags를_맨손으로_쓰지_않는다(self):
        """새 어댑터가 같은 함정에 다시 빠지는 것을 막는다.

        허용: `unescape(strip_tags(...))` 형태이거나 base.py 자신.
        권장: `clean_text()` / `clean_body()`.
        """
        offenders = []
        for path in sorted(ADAPTERS.glob("*.py")):
            if path.name == "base.py":
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "strip_tags(" not in line or line.lstrip().startswith("#"):
                    continue
                if "unescape" in line:
                    continue
                offenders.append(f"{path.name}:{n}  {line.strip()}")
        self.assertEqual(
            offenders, [],
            "strip_tags를 엔티티 복원 없이 호출한다 — bs4 경로와 출력이 갈린다. "
            "clean_text()/clean_body()를 쓸 것:\n  " + "\n  ".join(offenders),
        )


class Bs4Gate(unittest.TestCase):
    def test_진입점이_bs4_부재를_명시선언_없이는_거부한다(self):
        """무증상 품질 저하는 사람이 못 잡는다 — 크롤이 성공으로 끝나기 때문이다."""
        run = (Path(__file__).resolve().parent / "run.py").read_text(encoding="utf-8")
        self.assertIn("--allow-no-bs4", run, "bs4 부재 명시 해제 플래그가 없다")
        self.assertRegex(run, r"if not HAVE_BS4:", "bs4 부재 게이트가 없다")
        self.assertRegex(
            run, r"raise SystemExit\(f?\"\[중단\]",
            "게이트가 중단하지 않는다 — 경고만으로는 2026-07-28 사고를 못 막는다",
        )


class CorpusClean(unittest.TestCase):
    def test_저장된_코퍼스에_엔티티가_남아_있지_않다(self):
        """근인을 고쳐도 이미 저장된 행은 안 바뀐다 — scripts/repair_entity_titles.py의 결과를 고정한다."""
        import json

        corpus = Path(__file__).resolve().parents[3] / "pipeline/snapshots/parsed/notices_v2.json"
        if not corpus.exists():  # 스냅샷 없는 체크아웃에서는 건너뛴다
            self.skipTest("코퍼스 스냅샷 없음")
        ent = re.compile(r"&(?:lt|gt|amp|quot|apos|nbsp|#\d+|#x[0-9a-fA-F]+);")
        rows = json.loads(corpus.read_text(encoding="utf-8"))
        bad = [
            (r.get("dedup_key"), f)
            for r in rows for f in ("title", "body_text")
            if isinstance(r.get(f), str) and ent.search(r[f])
        ]
        self.assertEqual(
            bad[:5], [],
            f"엔티티가 남은 행 {len(bad)}건 — python3 scripts/repair_entity_titles.py 를 돌릴 것",
        )


if __name__ == "__main__":
    unittest.main()
