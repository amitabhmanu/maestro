"""Polite HTTP fetcher with a per-domain rate limit, robots.txt checks and a raw page cache."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from maestro.rawstore import RawStore

USER_AGENT = "maestro-crawler/0.1 (+https://github.com/amitabhmanu/maestro)"


class Disallowed(Exception):
    """robots.txt forbids this URL for our user agent."""


@dataclass
class Page:
    url: str
    status: int
    body: bytes
    sha256: str
    path: Path

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def _api_sources(pack_data: dict):
    for src in pack_data.get("sources", []):
        if src.get("access") == "api" and src.get("url"):
            yield urlsplit(src["url"]).netloc, src


def _token(src: dict) -> str | None:
    from maestro import config

    auth = src.get("api_auth")
    return config.setting(auth["env"]) if auth else None


def api_hosts(pack_data: dict) -> dict[str, float]:
    """Hosts of the pack's `access: api` sources, with their request rate (`api_rps`, default the crawl
    rate; `api_auth.rps` when the source's token is set)."""
    out = {}
    for host, src in _api_sources(pack_data):
        rps = src.get("api_rps", pack_data["budget"]["per_domain_rps"])
        if _token(src) and src["api_auth"].get("rps"):
            rps = src["api_auth"]["rps"]
        out[host] = rps
    return out


def api_headers(pack_data: dict) -> dict[str, dict[str, str]]:
    """Request headers per API host: the token named by a source's `api_auth.env` (from the environment
    or `.env`), formatted by `api_auth.format`. Hosts without a token get none."""
    out = {}
    for host, src in _api_sources(pack_data):
        token = _token(src)
        if token:
            auth = src["api_auth"]
            out[host] = {auth.get("header", "Authorization"): auth.get("format", "Bearer {token}").format(token=token)}
    return out


class Fetcher:
    """Fetches pages no faster than `rps` per domain and stores every body in `cache_dir`.

    The cache is the private data layer (git-ignored): extractors can be re-run over it without
    hitting the site again.
    """

    def __init__(
        self,
        cache_dir: Path,
        rps: float,
        *,
        user_agent: str = USER_AGENT,
        opener: Callable[..., object] = urllib.request.urlopen,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        api_hosts: dict[str, float] | None = None,
        api_headers: dict[str, dict[str, str]] | None = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.interval = 1.0 / rps
        # Hosts of public APIs the pack lists (`access: api`): their robots.txt is written for web
        # crawlers, so it is not consulted; the API's own rate limit (`api_rps`) applies instead.
        self.api_intervals = {h: 1.0 / r for h, r in (api_hosts or {}).items()}
        self.user_agent = user_agent
        self.api_headers = api_headers or {}  # tokens: sent with requests, never stored with the page
        self._open = opener
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _wait_turn(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self.api_intervals.get(host, self.interval) - (self._clock() - last)
            if gap > 0:
                self._sleep(gap)
        self._last[host] = self._clock()

    RETRY_STATUS = (429, 503)  # the server asks us to slow down (MusicBrainz answers 503 when busy)
    RETRIES = 3

    def _get(self, url: str) -> tuple[int, bytes]:
        host = urlsplit(url).netloc
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, **self.api_headers.get(host, {})})
        for attempt in range(self.RETRIES + 1):
            self._wait_turn(host)
            try:
                with self._open(req, timeout=60) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code not in self.RETRY_STATUS or attempt == self.RETRIES:
                    return exc.code, exc.read() if exc.fp else b""
                after = (exc.headers or {}).get("Retry-After", "")
                self._sleep(float(after) if after.isdigit() else 5.0 * 2 ** attempt)
        raise AssertionError("unreachable")

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.netloc in self.api_intervals:
            return True
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self._robots:
            status, body = self._get(root + "/robots.txt")
            if status == 200 and not body.lstrip().lower().startswith(b"<"):
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(body.decode("utf-8", errors="replace").splitlines())
                self._robots[root] = rp
            else:
                self._robots[root] = None  # no robots.txt: everything allowed
        rp = self._robots[root]
        return rp is None or rp.can_fetch(self.user_agent, url)

    def fetch(self, url: str) -> Page:
        if not self.allowed(url):
            raise Disallowed(url)
        status, body = self._get(url)
        digest, path = RawStore(self.cache_dir).put(body)
        return Page(url=url, status=status, body=body, sha256=digest, path=path)
