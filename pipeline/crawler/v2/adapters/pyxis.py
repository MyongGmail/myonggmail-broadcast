"""S: 명지대 도서관(아이네크 Pyxis SPA) JSON API 어댑터 — 채널 key "lib".

lib.mju.ac.kr 는 Angular SPA라 HTML 크롤이 불가능하지만 공개 JSON API가 있다
(2026-07-18 실증, 인증 불필요):

  목록 : https://{host}/pyxis-api/1/bulletin-boards/{board_id}/bulletins?offset=N&max=M
         → data.totalCount / data.list[] (id, title, dateCreated, isPrivate,
           bulletinCategory.name, attachments[] 까지 포함 — 본문만 없음)
  상세 : https://{host}/pyxis-api/1/bulletins/{board_id}/{id}
         → data.content (HTML 본문)
  첨부 : 목록·상세 공통 originalImageUrl("/attachments/BULLETIN/{uuid}")
         → https://{host}/pyxis-api/attachments/BULLETIN/{uuid}

사용자용 URL은 앱 번들(main.*.js) 라우트 실증으로 확인:
  gotoBulletin → "/guide/bulletin/notice/{id}",
  RouterModule.forRoot 옵션에 useHash 없음 = HTML5 경로 라우팅이고
  서버가 임의 경로에 SPA를 캐치올 서빙하므로 해시(#) 없이 직링크 가능.
  → https://{host}/guide/bulletin/notice/{id}

dedup_key = "pyxis:{board_id}:{id}"  (id는 게시글 전역 PK — 재수집 시 불변).
"""

from __future__ import annotations

import html as _html

from ..core.model import make_notice
from .base import HAVE_BS4, soup, strip_tags, take_detail

DEFAULT_HOST = "lib.mju.ac.kr"
DEFAULT_BOARD_ID = 1
DEFAULT_PAGE_SIZE = 20
DEFAULT_WEB_PATH = "notice"  # /guide/bulletin/{web_path}/{id}


def _api_list_url(host, board_id, offset, per):
    return "https://%s/pyxis-api/1/bulletin-boards/%s/bulletins?offset=%d&max=%d" % (
        host,
        board_id,
        offset,
        per,
    )


def _api_detail_url(host, board_id, bulletin_id):
    return "https://%s/pyxis-api/1/bulletins/%s/%s" % (host, board_id, bulletin_id)


def _web_url(host, web_path, bulletin_id):
    return "https://%s/guide/bulletin/%s/%s" % (host, web_path, bulletin_id)


def _html_to_text(content_html):
    """상세 content(HTML) → 평문. bs4 우선, 정규식+unescape 폴백."""
    if not content_html:
        return None
    if HAVE_BS4:
        doc = soup(content_html)
        if doc is not None:
            return doc.get_text("\n")
    return _html.unescape(strip_tags(content_html))


def _attachments(raw, host):
    """목록/상세 공통 attachments[] → [{"name","url"}]."""
    out = []
    for a in raw or []:
        if not isinstance(a, dict):
            continue
        name = (a.get("logicalName") or a.get("physicalName") or "").strip()
        path = a.get("originalImageUrl") or ""
        if not name and not path:
            continue
        url = "https://%s/pyxis-api%s" % (host, path) if path.startswith("/") else path
        out.append({"name": name or path, "url": url})
    return out


def _fetch_page(client, host, board_id, offset, per, log):
    """목록 1페이지 → (items, total_count). 실패는 호출부에서 처리."""
    payload = client.get_json(_api_list_url(host, board_id, offset, per))
    if not isinstance(payload, dict) or not payload.get("success"):
        raise ValueError("Pyxis 응답 실패: %s" % str(payload)[:200])
    data = payload.get("data") or {}
    items = data.get("list") or []
    total = int(data.get("totalCount") or len(items))
    return items, total


def _fetch_body(client, host, board_id, bulletin_id, log):
    """상세 API → (body_text, attachments|None)."""
    try:
        payload = client.get_json(_api_detail_url(host, board_id, bulletin_id))
    except Exception as exc:
        log(f"pyxis 상세 실패 {board_id}/{bulletin_id}: {exc}")
        return None, None
    if not isinstance(payload, dict) or not payload.get("success"):
        log(f"pyxis 상세 응답 이상 {board_id}/{bulletin_id}: {str(payload)[:120]}")
        return None, None
    data = payload.get("data") or {}
    return _html_to_text(data.get("content")), _attachments(data.get("attachments"), host)


def collect(ctx):
    ch = ctx["channel"]
    p = ch.get("params") or {}
    host = p.get("host", DEFAULT_HOST)
    board_id = int(p.get("board_id", DEFAULT_BOARD_ID))
    per = int(p.get("max", DEFAULT_PAGE_SIZE))
    web_path = p.get("web_path", DEFAULT_WEB_PATH)
    client = ctx["client"]
    log = ctx["log"]
    school_id = ctx["school"]["id"]
    known = ctx.get("known_keys") or set()

    # ---------- 목록 수집 (incremental: 1페이지 / backfill: totalCount까지 페이지네이션)
    try:
        raw_items, total = _fetch_page(client, host, board_id, 0, per, log)
    except Exception as exc:
        log(f"{ch['key']}: 목록 1페이지 실패 — 채널 중단: {exc}")
        return []

    if ctx["mode"] != "incremental" and total > len(raw_items):
        max_page = (total + per - 1) // per  # 전체 페이지 수
        limit = ctx.get("pages") or max_page
        for page in range(1, min(max_page, limit)):
            try:
                items, _ = _fetch_page(client, host, board_id, page * per, per, log)
            except Exception as exc:
                log(f"{ch['key']}: offset={page * per} 페이지 실패: {exc}")
                break
            if not items:
                break
            raw_items += items

    # ---------- 레코드 변환
    notices = []
    seen = set()
    for item in raw_items:
        try:
            bulletin_id = item["id"]
            # API가 제목을 HTML 이스케이프해서 준다 — 여기서 풀지 않으면 &lt;가 그대로 저장된다
            title = _html.unescape(item.get("title") or "")
        except (TypeError, KeyError) as exc:
            log(f"{ch['key']}: 항목 구조 이상 — {exc}: {str(item)[:120]}")
            continue
        dedup_key = "pyxis:%s:%s" % (board_id, bulletin_id)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        attachments = _attachments(item.get("attachments"), host)
        body = None
        if (
            dedup_key not in known
            and not item.get("isPrivate")
            and take_detail(ctx)
        ):
            body, detail_atts = _fetch_body(client, host, board_id, bulletin_id, log)
            if detail_atts:
                attachments = detail_atts

        category = (item.get("bulletinCategory") or {}).get("name")
        try:
            notices.append(
                make_notice(
                    school_id,
                    ch["key"],
                    dedup_key,
                    title,
                    _web_url(host, web_path, bulletin_id),
                    date=item.get("dateCreated"),
                    body_text=body,
                    category_hint=ch.get("category_hint"),
                    operator=ch.get("operator"),
                    attachments=attachments,
                    extra={
                        "board_id": board_id,
                        "pyxis_id": bulletin_id,
                        "pyxis_category": category,  # 인문/자연/공통 등
                        "api_url": _api_detail_url(host, board_id, bulletin_id),
                    },
                )
            )
        except ValueError as exc:
            log(f"{ch['key']}: {exc}")
    return notices
