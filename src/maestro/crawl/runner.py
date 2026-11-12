"""Run queued crawl jobs: fetch each page and hand it to the maestro's extractor.

Extractors are maestro-specific and live in `maestros/<slug>/extractors/<name>.py`. Each module
defines `extract(ctx: ExtractContext) -> None` and uses `ctx` to write claims and queue follow-up jobs.
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

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

    def enqueue(self, url: str, extractor: str, purpose: str, **kw: Any) -> dict[str, Any]:
        return self.store.enqueue_crawl(url, extractor, purpose, **kw)


def load_extractor(pack_dir: Path, name: str) -> ModuleType:
    path = Path(pack_dir) / "extractors" / f"{name}.py"
    if not path.is_file():
        raise FileNotFoundError(f"no extractor {name!r} at {path}")
    spec = importlib.util.spec_from_file_location(f"maestro_extractor_{path.parent.parent.name}_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    if not hasattr(module, "extract"):
        raise AttributeError(f"extractor {path} has no extract(ctx) function")
    return module


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
