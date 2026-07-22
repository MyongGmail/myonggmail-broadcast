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
    return TAG_RE.sub(" ", html or "")


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
