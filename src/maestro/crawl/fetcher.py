"""Polite HTTP fetcher with a per-domain rate limit, robots.txt checks and a raw page cache."""

from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

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
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.interval = 1.0 / rps
        self.user_agent = user_agent
        self._open = opener
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _wait_turn(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self.interval - (self._clock() - last)
            if gap > 0:
                self._sleep(gap)
        self._last[host] = self._clock()

    def _get(self, url: str) -> tuple[int, bytes]:
        host = urlsplit(url).netloc
        self._wait_turn(host)
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with self._open(req, timeout=60) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read() if exc.fp else b""

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
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
        digest = hashlib.sha256(body).hexdigest()
        path = self.cache_dir / f"{digest}.html"
        if not path.exists():
            path.write_bytes(body)
        return Page(url=url, status=status, body=body, sha256=digest, path=path)
