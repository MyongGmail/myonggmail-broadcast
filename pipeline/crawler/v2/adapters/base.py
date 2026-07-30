"""어댑터 계약 + 공용 파싱 유틸.

어댑터 = collect(ctx) -> list[notice dict] 를 제공하는 모듈.

    def collect(ctx) -> list[dict]

ctx (dict):
    school       : 학교 설정 (mju.json 의 school 블록)
    channel      : 채널 설정 행 (key/name/params/category_hint/operator ...)
    client       : core.http.PoliteClient
    mode         : "incremental" | "backfill"
    pages        : 목록 페이지 상한 (0 = 제한 없음, backfill에서만 의미)
    with_detail  : 상세 본문 수집 여부
    detail_budget: 이번 실행에서 남은 상세 요청 수 (dict {"n": int}) — 어댑터가 차감
    known_keys   : 이미 저장된 dedup_key 집합 (incremental 조기 중단·상세 스킵용)
    log          : callable(str)

반환 레코드는 반드시 core.model.make_notice()로 생성한다.
개별 항목 파싱 실패는 raise 하지 말고 ctx["log"]로 남기고 건너뛴다.
"""

from __future__ import annotations

import html as html_mod
import re

try:
    from bs4 import BeautifulSoup

    HAVE_BS4 = True
except ImportError:
    HAVE_BS4 = False

TAG_RE = re.compile(r"<[^>]+>")


def soup(html):
    if not HAVE_BS4:
        return None
    return BeautifulSoup(html, "html.parser")


def strip_tags(html):
    """태그만 지운다 — **엔티티는 그대로 남는다**(`&lt;` → `&lt;`).

    ⚠️ 이걸 단독으로 쓰면 제목에 `&lt;`·`&amp;`가 그대로 실린다. bs4의 `get_text()`는
    엔티티를 풀어 주므로 bs4 경로와 폴백 경로의 출력이 **말없이 달라진다** — bs4가 설치돼
    있으면 안 보이고 없으면 파손된다. 2026-07-28 인구조사가 정확히 그 상태로 돌았다
    (제목 442건 파손). 평문이 필요하면 `clean_text()`를 쓴다.
    """
    return TAG_RE.sub(" ", html or "")


def clean_text(raw):
    """HTML 조각 → 평문. 태그 제거 → **엔티티 복원** → 공백 정리.

    순서가 중요하다. 엔티티를 먼저 풀면 본문의 `&lt;script&gt;` 같은 문자열이 진짜 태그가
    되어 TAG_RE에 지워진다. 그리고 길이 제한은 **이 함수 뒤에** 걸어야 한다 — 앞에 걸면
    `&gt;`가 `&g`로 잘려 복원 불가능한 쓰레기가 남는다(코퍼스에 실제 사례 존재).
    """
    return re.sub(r"\s+", " ", html_mod.unescape(strip_tags(raw))).strip()


def clean_body(raw):
    """본문용 — 태그 제거 + 엔티티 복원까지만 하고 **공백은 접지 않는다**.

    제목과 다른 이유: bs4 경로가 `get_text("\\n")`으로 문단 구분을 개행에 담는다.
    여기서 `\\s+`를 접으면 폴백 경로만 문단이 사라져, 또 한 번 두 경로의 출력이 갈린다.
    """
    return html_mod.unescape(strip_tags(raw))


def take_detail(ctx):
    """상세 수집 예산을 1 차감. 예산 소진이면 False."""
    if not ctx.get("with_detail"):
        return False
    budget = ctx.get("detail_budget")
    if budget is None:
        return True
    if budget["n"] <= 0:
        return False
    budget["n"] -= 1
    return True
