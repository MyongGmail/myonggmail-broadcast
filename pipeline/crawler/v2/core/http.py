"""정중한 HTTP 클라이언트 — 호스트 화이트리스트 + robots.txt 준수 + 호스트별 요청 간격."""

from __future__ import annotations

import time
import urllib.robotparser
from urllib.parse import urlparse

try:
    import requests

    HAVE_REQUESTS = True
except ImportError:  # GH Actions에는 requirements.txt로 설치됨 — 폴백은 로컬 안전망
    import urllib.request

    HAVE_REQUESTS = False


class DisallowedByRobots(RuntimeError):
    pass


class HostNotAllowed(RuntimeError):
    pass


class PoliteClient:
    """호스트별 최소 지연·robots.txt 캐시·화이트리스트를 강제하는 GET 전용 클라이언트."""

    def __init__(self, allowed_hosts, user_agent, delay_sec=1.2, timeout_sec=20):
        self.allowed_hosts = set(allowed_hosts)
        self.user_agent = user_agent
        self.delay_sec = delay_sec
        self.timeout_sec = timeout_sec
        self._last_at = {}  # host -> monotonic ts
        self._robots = {}  # host -> RobotFileParser | None(fetch 실패 → 허용 취급)
        self.stats = {"requests": 0, "robots_blocked": 0}
        if HAVE_REQUESTS:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": user_agent, "Accept-Language": "ko"})

    # -------------------------------------------------- robots
    def _robots_for(self, host):
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                raw = self._raw_get(f"https://{host}/robots.txt")
                rp.parse(raw.splitlines())
                self._robots[host] = rp
            except Exception:
                self._robots[host] = None  # robots 없음/오류 → 허용으로 취급하되 기록
        return self._robots[host]

    def _check(self, url):
        host = urlparse(url).netloc
        if host not in self.allowed_hosts:
            raise HostNotAllowed(f"허용되지 않은 호스트: {host} ({url})")
        rp = self._robots_for(host)
        if rp is not None and not rp.can_fetch(self.user_agent, url):
            self.stats["robots_blocked"] += 1
            raise DisallowedByRobots(f"robots.txt 차단: {url}")

    # -------------------------------------------------- fetch
    def _raw_get(self, url):
        if HAVE_REQUESTS:
            resp = self._session.get(url, timeout=self.timeout_sec)
            resp.raise_for_status()
            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
        req = urllib.request.Request(
            url, headers={"User-Agent": self.user_agent, "Accept-Language": "ko"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def get(self, url):
        """robots·화이트리스트 검사 + 호스트별 지연 후 GET → 본문 텍스트."""
        self._check(url)
        host = urlparse(url).netloc
        wait = self.delay_sec - (time.monotonic() - self._last_at.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._last_at[host] = time.monotonic()
        self.stats["requests"] += 1
        return self._raw_get(url)

    def get_json(self, url):
        import json

        return json.loads(self.get(url))
