"""Raw page store: every fetched or hand-saved page, kept by its SHA-256 under the private data folder.

Pages live at `<data dir>/<slug>/raw/<sha256>.html` (data dir: MAESTRO_DATA_DIR, default ./data). Because
the name is the content hash, a page is found by its hash wherever the store was built, and the same
page saved twice is kept once. `raw_page.path` keeps the path recorded at fetch time; readers look the
page up by hash first and fall back to that path.

`maestro-raw` reports which pages are missing and refills them: from a folder of saved pages (matched
by hash), or by fetching again pages the crawler got the first time (robots.txt and rate limits apply;
pages that changed since are kept as new raw_page rows, the old hash stays missing).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maestro import config

log = logging.getLogger(__name__)


class RawStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    @classmethod
    def for_slug(cls, slug: str, cache_dir: str | Path | None = None) -> "RawStore":
        return cls(Path(cache_dir) if cache_dir else config.raw_dir(slug))

    def path_for(self, sha256: str) -> Path:
        return self.root / f"{sha256}.html"

    def put(self, body: bytes) -> tuple[str, Path]:
        """Keep `body`; returns its hash and where it lives. Writes atomically, never twice."""
        digest = hashlib.sha256(body).hexdigest()
        path = self.path_for(digest)
        if not path.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
        return digest, path

    def locate(self, sha256: str, recorded: str | None = None, base: Path | None = None) -> Path | None:
        """Where the page with this hash is, or None. Tries the store, then the recorded path."""
        candidates = [self.path_for(sha256)]
        if recorded:
            p = Path(recorded)
            if not p.is_absolute() and base is not None:
                candidates.append(base / p)
            candidates.append(p)
        for p in candidates:
            if p.is_file():
                return p
        return None

    def read(self, sha256: str, recorded: str | None = None, base: Path | None = None) -> bytes:
        path = self.locate(sha256, recorded, base)
        if path is None:
            raise FileNotFoundError(f"raw page {sha256} is not in {self.root}" + (f" or at {recorded}" if recorded else ""))
        return path.read_bytes()


@dataclass
class RawStatus:
    rows: int = 0
    present: int = 0
    missing: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self, sample: int = 10) -> dict[str, Any]:
        crawled = sum(1 for m in self.missing if m["origin"] == "crawl")
        return {"rows": self.rows, "present": self.present, "missing": len(self.missing),
                "missing_crawled": crawled, "missing_imported": len(self.missing) - crawled,
                "missing_other": sum(1 for m in self.missing if m["origin"] == "other"),
                "sample": [{k: m[k] for k in ("id", "url", "origin")} for m in self.missing[:sample]]}


def _origin_sql() -> str:
    return """SELECT r.id, r.url, r.sha256, r.path,
                     CASE WHEN EXISTS (SELECT 1 FROM import_log i WHERE i.raw_page_id = r.id) THEN 'import'
                          WHEN EXISTS (SELECT 1 FROM job j WHERE j.raw_page_id = r.id) THEN 'crawl'
                          ELSE 'other' END AS origin
              FROM raw_page r ORDER BY r.id"""


def status(store, raw: RawStore, base: Path | None = None) -> RawStatus:
    """Which raw_page rows have their page on disk."""
    st = RawStatus()
    for r in store.conn.execute(_origin_sql()).fetchall():
        st.rows += 1
        if raw.locate(r["sha256"], r["path"], base):
            st.present += 1
        else:
            st.missing.append(r)
    return st


def fill_from_dir(store, raw: RawStore, folder: Path, base: Path | None = None) -> dict[str, int]:
    """Copy into the store every file under `folder` whose hash matches a missing raw page."""
    missing = {m["sha256"] for m in status(store, raw, base).missing}
    found = scanned = 0
    for f in sorted(Path(folder).rglob("*")):
        if not f.is_file():
            continue
        scanned += 1
        body = f.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        if digest in missing:
            raw.put(body)
            missing.discard(digest)
            found += 1
    return {"scanned": scanned, "restored": found, "still_missing": len(missing)}


def refetch(store, raw: RawStore, fetcher, *, limit: int | None = None, base: Path | None = None) -> dict[str, int]:
    """Fetch again the crawled pages whose files are missing.

    A page that comes back identical restores the old file. One that changed is kept under its new hash
    as a new raw_page row (the original row and its claims stay as they are), and is counted as changed.
    """
    from maestro.crawl.fetcher import Disallowed

    out = {"restored": 0, "changed": 0, "disallowed": 0, "failed": 0}
    todo = [m for m in status(store, raw, base).missing if m["origin"] == "crawl"]
    seen: set[str] = set()
    for m in todo[:limit] if limit else todo:
        if m["url"] in seen:
            continue
        seen.add(m["url"])
        try:
            page = fetcher.fetch(m["url"])
        except Disallowed:
            out["disallowed"] += 1
            continue
        except Exception as exc:  # network errors: report and move on
            log.warning("refetch %s failed: %s", m["url"], exc)
            out["failed"] += 1
            continue
        if page.status != 200:
            out["failed"] += 1
        elif page.sha256 == m["sha256"]:
            out["restored"] += 1
        else:
            store.record_raw_page(page.url, page.status, page.sha256, str(page.path))
            out["changed"] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    """`maestro-raw`: check and refill the raw page store.

    maestro-raw --pack maestros/rafi status
    maestro-raw --pack maestros/rafi fill DIR     restore missing pages from saved files (matched by hash)
    maestro-raw --pack maestros/rafi refetch      fetch missing crawled pages again (polite, rate-limited)
    """
    from maestro.crawl.fetcher import Fetcher, api_hosts
    from maestro.pack import load_pack
    from maestro.store import Store

    p = argparse.ArgumentParser(description=main.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pack", required=True)
    p.add_argument("--dsn", help="default MAESTRO_DSN_<SLUG> or MAESTRO_DSN (also read from .env)")
    p.add_argument("--cache-dir", help="raw page folder (default <MAESTRO_DATA_DIR>/<slug>/raw)")
    p.add_argument("--limit", type=int, help="refetch: at most this many pages")
    p.add_argument("action", choices=["status", "fill", "refetch"])
    p.add_argument("folder", nargs="?", type=Path, help="fill: folder of saved pages")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pack = load_pack(args.pack)
    dsn = config.dsn_for(pack.slug, args.dsn)
    if not dsn:
        p.error(f"--dsn, {config.dsn_env_name(pack.slug)} or MAESTRO_DSN is required")
    store = Store.connect(dsn, pack)
    store.migrate()
    raw = RawStore.for_slug(pack.slug, args.cache_dir)
    base = Path.cwd()
    if args.action == "status":
        result = status(store, raw, base).as_dict()
    elif args.action == "fill":
        if not args.folder:
            p.error("fill needs a folder")
        result = fill_from_dir(store, raw, args.folder, base)
    else:
        fetcher = Fetcher(raw.root, rps=pack.data["budget"]["per_domain_rps"], api_hosts=api_hosts(pack.data))
        result = refetch(store, raw, fetcher, limit=args.limit, base=base)
    print(json.dumps({"maestro": pack.slug, "raw_dir": str(raw.root), args.action: result}, indent=2))
    store.close()
    return 0
