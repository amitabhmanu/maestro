"""Run queued crawl jobs: fetch each page and hand it to the maestro's extractor.

Extractors are maestro-specific and live in `maestros/<slug>/extractors/<name>.py`. Each module
defines `extract(ctx: ExtractContext) -> None` and uses `ctx` to write claims and queue follow-up jobs.
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from psycopg.types.json import Jsonb

from maestro.crawl.fetcher import Disallowed, Fetcher, Page
from maestro.store import Store

log = logging.getLogger(__name__)


@dataclass
class ExtractContext:
    store: Store
    job: dict[str, Any]
    page: Page
    raw_page_id: int
    options: dict[str, str] = field(default_factory=dict)
    follow: bool = True  # False during a replay: follow-up jobs are not queued

    def enqueue(self, url: str, extractor: str, purpose: str, **kw: Any) -> dict[str, Any]:
        if not self.follow:
            return {}
        return self.store.enqueue_crawl(url, extractor, purpose, **kw)


CORE_EXTRACTORS = Path(__file__).resolve().parent.parent / "extractors"


def load_extractor(pack_dir: Path, name: str) -> ModuleType:
    """The maestro's own extractor `maestros/<slug>/extractors/<name>.py`, else a core one
    (`maestro/extractors/<name>.py`, for sources any maestro can use)."""
    path = Path(pack_dir) / "extractors" / f"{name}.py"
    if not path.is_file():
        path = CORE_EXTRACTORS / f"{name}.py"
    if not path.is_file():
        raise FileNotFoundError(f"no extractor {name!r} in {Path(pack_dir) / 'extractors'} or {CORE_EXTRACTORS}")
    spec = importlib.util.spec_from_file_location(f"maestro_extractor_{path.parent.parent.name}_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    if not hasattr(module, "extract"):
        raise AttributeError(f"extractor {path} has no extract(ctx) function")
    return module


def import_page(
    store: Store,
    extractor: str,
    url: str,
    file: Path,
    cache_dir: Path,
    options: dict[str, str] | None = None,
) -> int:
    """Run an extractor over a page saved by hand (for sites that may not be crawled).

    The file is copied into the raw cache and recorded against `url`, the page it was saved from.
    Returns the raw_page id.
    """
    module = load_extractor(store.pack.path.parent, extractor)
    body = Path(file).read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{digest}.html"
    if not path.exists():
        path.write_bytes(body)
    page = Page(url=url, status=200, body=body, sha256=digest, path=path)
    raw_id = store.record_raw_page(url, 200, digest, str(path))
    store.conn.execute("INSERT INTO import_log (raw_page_id, extractor, options) VALUES (%s, %s, %s)",
                       (raw_id, extractor, Jsonb(dict(options or {}))))
    job = {"id": None, "url": url, "extractor": extractor, "purpose": "import"}
    module.extract(ExtractContext(store, job, page, raw_id, dict(options or {})))
    return raw_id


@dataclass
class RunStats:
    done: int = 0
    failed: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"done": self.done, "failed": self.failed, "skipped": self.skipped}


def run_jobs(store: Store, fetcher: Fetcher, *, max_jobs: int, options: dict[str, str] | None = None) -> RunStats:
    pack_dir = store.pack.path.parent
    extractors: dict[str, ModuleType] = {}
    stats = RunStats()
    for _ in range(max_jobs):
        job = store.next_job()
        if job is None:
            break
        try:
            module = extractors.get(job["extractor"]) or load_extractor(pack_dir, job["extractor"])
            extractors[job["extractor"]] = module
            page = fetcher.fetch(job["url"])
            raw_id = store.record_raw_page(page.url, page.status, page.sha256, str(page.path))
            if page.status != 200:
                store.finish_job(job["id"], status="failed", raw_page_id=raw_id, error=f"HTTP {page.status}")
                stats.failed += 1
                continue
            module.extract(ExtractContext(store, job, page, raw_id, dict(options or {})))
            store.finish_job(job["id"], status="done", raw_page_id=raw_id)
            stats.done += 1
        except Disallowed:
            store.finish_job(job["id"], status="skipped", error="disallowed by robots.txt")
            stats.skipped += 1
        except Exception as exc:  # keep the run going; the job records why it failed
            log.exception("job %s failed", job["id"])
            store.finish_job(job["id"], status="failed", error=f"{type(exc).__name__}: {exc}")
            stats.failed += 1
    return stats


def replay(source: Store, target: Store, *, root: Path | None = None) -> RunStats:
    """Re-run extractors over every page `source` has already fetched or imported, writing into `target`.

    Use it after an extractor or core fix: the raw cache is re-read, nothing is fetched. Only the latest
    fetch per (url, extractor) is replayed; every distinct imported file is (one page may be pasted in
    parts). Imports keep the options they were loaded with, and follow-up jobs are not queued. `root` resolves relative cache paths (default: the working directory).
    """
    rows = source.conn.execute(
        """SELECT DISTINCT ON (url, extractor, part) * FROM (
               SELECT r.id AS raw_page_id, r.url, r.http_status, r.sha256, r.path, j.extractor,
                      '{}'::jsonb AS options, j.purpose, '' AS part
               FROM job j JOIN raw_page r ON r.id = j.raw_page_id WHERE j.status = 'done'
               UNION ALL
               SELECT r.id, r.url, r.http_status, r.sha256, r.path, i.extractor, i.options, 'import', r.sha256
               FROM import_log i JOIN raw_page r ON r.id = i.raw_page_id
           ) pages ORDER BY url, extractor, part, raw_page_id DESC"""
    ).fetchall()
    rows.sort(key=lambda r: r["raw_page_id"])
    pack_dir = target.pack.path.parent
    extractors: dict[str, ModuleType] = {}
    stats = RunStats()
    for r in rows:
        try:
            module = extractors.get(r["extractor"]) or load_extractor(pack_dir, r["extractor"])
            extractors[r["extractor"]] = module
            path = Path(r["path"])
            if root is not None and not path.is_absolute():
                path = root / path
            page = Page(url=r["url"], status=r["http_status"] or 200, body=path.read_bytes(), sha256=r["sha256"], path=path)
            raw_id = target.record_raw_page(r["url"], page.status, r["sha256"], r["path"])
            if r["purpose"] == "import":
                target.conn.execute("INSERT INTO import_log (raw_page_id, extractor, options) VALUES (%s, %s, %s)",
                                    (raw_id, r["extractor"], Jsonb(r["options"])))
            else:  # keep the target replayable in turn
                target.conn.execute(
                    """INSERT INTO job (url, extractor, purpose, status, attempts, fetched_at, raw_page_id)
                       VALUES (%s, %s, %s, 'done', 1, now(), %s)""", (r["url"], r["extractor"], r["purpose"], raw_id))
            job = {"id": None, "url": r["url"], "extractor": r["extractor"], "purpose": r["purpose"]}
            module.extract(ExtractContext(target, job, page, raw_id, dict(r["options"]), follow=False))
            stats.done += 1
        except Exception:
            log.exception("replay of %s failed", r["url"])
            stats.failed += 1
    return stats
