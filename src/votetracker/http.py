"""HTTP client with retries, timeouts, and per-host pacing.

Every source goes through this one client so we stay polite to government
servers and inside API rate limits.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

# Minimum seconds between requests to the same host.
DEFAULT_HOST_INTERVALS = {
    "api.congress.gov": 0.8,  # 5,000 requests/hour limit
    "clerk.house.gov": 0.25,
    "www.senate.gov": 0.25,
    "raw.githubusercontent.com": 0.1,
}


@dataclass
class Response:
    url: str
    status: int
    content: bytes
    headers: dict

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        import json

        return json.loads(self.content)


class HttpClient:
    def __init__(self, user_agent: str, timeout: float = 30.0, host_intervals: dict | None = None):
        self.timeout = timeout
        self.host_intervals = dict(DEFAULT_HOST_INTERVALS)
        if host_intervals:
            self.host_intervals.update(host_intervals)
        self._last_hit: dict[str, float] = {}
        self._lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "*/*"})
        retry = Retry(
            total=5,
            connect=2,
            read=2,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "HEAD"),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.request_count = 0

    def _pace(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        interval = self.host_intervals.get(host, 0.0)
        if interval <= 0:
            return
        with self._lock:
            last = self._last_hit.get(host, 0.0)
            wait = last + interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_hit[host] = time.monotonic()

    def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> Response:
        self._pace(url)
        self.request_count += 1
        try:
            r = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            log.warning("GET %s failed: %s", url, exc)
            return Response(url=url, status=0, content=b"", headers={})
        return Response(url=r.url, status=r.status_code, content=r.content, headers=dict(r.headers))


class FakeHttpClient:
    """Test double: maps URL (without query string) to (status, body)."""

    def __init__(self, routes: dict[str, tuple[int, bytes | str]]):
        self.routes = routes
        self.calls: list[str] = []
        self.request_count = 0

    def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> Response:
        self.calls.append(url)
        self.request_count += 1
        status, body = self.routes.get(url, (404, b"not found"))
        if isinstance(body, str):
            body = body.encode("utf-8")
        return Response(url=url, status=status, content=body, headers={})
