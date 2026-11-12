import io
import urllib.error

import pytest

from conftest import ROOT
from maestro.crawl.fetcher import Disallowed, Fetcher
from maestro.crawl.runner import run_jobs
from maestro.pack import load_pack


class Resp(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeWeb:
    def __init__(self, pages):
        self.pages, self.calls, self.now, self.slept = pages, [], 0.0, []

    def open(self, req, timeout):
        self.calls.append((req.full_url, self.now))
        if req.full_url not in self.pages:
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b"<html>404</html>"))
        return Resp(self.pages[req.full_url])

    def clock(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def fetcher(tmp_path, web, rps=0.5):
    return Fetcher(tmp_path / "raw", rps, opener=web.open, clock=web.clock, sleep=web.sleep)


def test_rate_limit_robots_and_cache(tmp_path):
    web = FakeWeb({"https://ex.org/a": b"A", "https://ex.org/b": b"B"})
    f = fetcher(tmp_path, web)
    pa, pb = f.fetch("https://ex.org/a"), f.fetch("https://ex.org/b")
    assert [u for u, _ in web.calls] == ["https://ex.org/robots.txt", "https://ex.org/a", "https://ex.org/b"]
    times = [t for _, t in web.calls]
    assert all(b - a >= 2.0 for a, b in zip(times, times[1:]))  # 0.5 requests per second
    assert pa.path.read_bytes() == b"A" and pb.text == "B"


def test_robots_disallow(tmp_path):
    web = FakeWeb({"https://ex.org/robots.txt": b"User-agent: *\nDisallow: /private\n", "https://ex.org/ok": b"ok"})
    f = fetcher(tmp_path, web, rps=100)
    assert f.fetch("https://ex.org/ok").text == "ok"
    with pytest.raises(Disallowed):
        f.fetch("https://ex.org/private/x")


def test_api_hosts_skip_robots_and_keep_their_rate(tmp_path):
    from maestro.crawl.fetcher import api_hosts

    web = FakeWeb({"https://api.ex.org/robots.txt": b"User-agent: *\nDisallow: /ws\n",
                   "https://api.ex.org/ws/1": b"1", "https://api.ex.org/ws/2": b"2"})
    hosts = api_hosts({"budget": {"per_domain_rps": 0.5},
                       "sources": [{"access": "api", "url": "https://api.ex.org/ws/", "api_rps": 1},
                                   {"access": "crawlable", "url": "https://other.org/"}]})
    assert hosts == {"api.ex.org": 1}
    f = Fetcher(tmp_path / "raw", 0.5, opener=web.open, clock=web.clock, sleep=web.sleep, api_hosts=hosts)
    assert f.fetch("https://api.ex.org/ws/1").text == "1" and f.fetch("https://api.ex.org/ws/2").text == "2"
    assert [u for u, _ in web.calls] == ["https://api.ex.org/ws/1", "https://api.ex.org/ws/2"]
    assert web.calls[1][1] - web.calls[0][1] >= 1.0


def test_runner_runs_jobs_and_records_failures(tmp_path, store_for):
    # A throwaway copy of the Rafi pack with a tiny extractor.
    pack_dir = tmp_path / "rafi"
    (pack_dir / "extractors").mkdir(parents=True)
    (pack_dir / "maestro.yaml").write_text((ROOT / "maestros/rafi/maestro.yaml").read_text("utf-8"), "utf-8")
    (pack_dir / "extractors" / "toy.py").write_text(
        "def extract(ctx):\n"
        "    if ctx.page.text == 'boom':\n"
        "        raise ValueError('bad page')\n"
        "    if ctx.page.url.endswith('/seed'):\n"
        "        ctx.enqueue('https://ex.org/child', 'toy', 'enrich')\n"
        "    e = ctx.store.create_entity('song', ctx.page.text)\n", "utf-8")
    s = store_for("rafi", pack=load_pack(pack_dir))
    web = FakeWeb({"https://ex.org/seed": b"seed", "https://ex.org/child": b"child", "https://ex.org/bad": b"boom"})
    s.enqueue_crawl("https://ex.org/seed", "toy", "spine", priority=10)
    s.enqueue_crawl("https://ex.org/bad", "toy", "spine")
    s.enqueue_crawl("https://ex.org/missing", "toy", "spine")
    stats = run_jobs(s, fetcher(tmp_path, web, rps=100), max_jobs=10)
    assert stats.as_dict() == {"done": 2, "failed": 2, "skipped": 0}
    jobs = {r["url"]: (r["status"], r["error"]) for r in s.query_graph("SELECT url, status, error FROM job")}
    assert jobs["https://ex.org/child"] == ("done", None)
    assert jobs["https://ex.org/bad"] == ("failed", "ValueError: bad page")
    assert jobs["https://ex.org/missing"] == ("failed", "HTTP 404")
    labels = sorted(r["label"] for r in s.query_graph("SELECT label FROM entity WHERE type = 'song'"))
    assert labels == ["child", "seed"]


def test_busy_server_is_retried_with_backoff(tmp_path):
    web = FakeWeb({"https://ex.org/robots.txt": b"", "https://ex.org/a": b"A"})
    busy = [503, 429]
    real_open = web.open

    def open_(req, timeout):
        if req.full_url.endswith("/a") and busy:
            web.calls.append((req.full_url, web.now))
            raise urllib.error.HTTPError(req.full_url, busy.pop(0), "Busy", {}, io.BytesIO(b""))
        return real_open(req, timeout)

    f = Fetcher(tmp_path / "raw", 100, opener=open_, clock=web.clock, sleep=web.sleep)
    page = f.fetch("https://ex.org/a")
    assert page.status == 200 and page.text == "A"
    assert [u for u, _ in web.calls].count("https://ex.org/a") == 3
    assert 5.0 in web.slept and 10.0 in web.slept
