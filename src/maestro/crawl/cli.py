"""`maestro-crawl`: seed and run crawl jobs for one maestro.

Example (Rafi dry run, films 1944-1950):
  maestro-crawl --pack maestros/rafi --dsn $MAESTRO_DSN \\
      --seed "https://hamraaz.org/filmssite/public/person.php?id=4357" --extractor hamraaz \\
      --opt years=1944-1950 --max-jobs 500
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from maestro.crawl.fetcher import Fetcher
from maestro.crawl.runner import import_page, run_jobs
from maestro.pack import load_pack
from maestro.store import Store


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pack", required=True, help="maestro folder, e.g. maestros/rafi")
    p.add_argument("--dsn", default=os.environ.get("MAESTRO_DSN"), help="Postgres DSN (default $MAESTRO_DSN)")
    p.add_argument("--seed", action="append", default=[], help="URL to queue before running (repeatable)")
    p.add_argument("--extractor", help="extractor for --seed URLs")
    p.add_argument("--purpose", default="spine", help="purpose for --seed jobs")
    p.add_argument("--opt", action="append", default=[], help="extractor option key=value (repeatable)")
    p.add_argument("--max-jobs", type=int, default=100)
    p.add_argument("--cache-dir", default=None, help="raw page cache (default data/<slug>/raw)")
    args = p.parse_args(argv)
    if not args.dsn:
        p.error("--dsn or MAESTRO_DSN is required")
    if args.seed and not args.extractor:
        p.error("--seed needs --extractor")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pack = load_pack(args.pack)
    store = Store.connect(args.dsn, pack)
    store.migrate()
    store.sync_sources()
    for url in args.seed:
        store.enqueue_crawl(url, args.extractor, args.purpose, priority=1000)

    cache = Path(args.cache_dir or Path("data") / pack.slug / "raw")
    fetcher = Fetcher(cache, rps=pack.data["budget"]["per_domain_rps"])
    options = dict(o.split("=", 1) for o in args.opt)
    stats = run_jobs(store, fetcher, max_jobs=args.max_jobs, options=options)
    print(json.dumps({"run": stats.as_dict(), "coverage": store.coverage_report()}, indent=2, default=str))
    store.close()
    return 0


def import_main(argv: list[str] | None = None) -> int:
    """`maestro-import`: run an extractor over pages saved by hand (sites whose robots.txt forbids crawling)."""
    p = argparse.ArgumentParser(description=import_main.__doc__)
    p.add_argument("--pack", required=True, help="maestro folder, e.g. maestros/rafi")
    p.add_argument("--dsn", default=os.environ.get("MAESTRO_DSN"), help="Postgres DSN (default $MAESTRO_DSN)")
    p.add_argument("--extractor", required=True)
    p.add_argument("--url", required=True, help="the URL the page was saved from")
    p.add_argument("--opt", action="append", default=[], help="extractor option key=value (repeatable)")
    p.add_argument("--cache-dir", default=None, help="raw page cache (default data/<slug>/raw)")
    p.add_argument("file", type=Path)
    args = p.parse_args(argv)
    if not args.dsn:
        p.error("--dsn or MAESTRO_DSN is required")
    pack = load_pack(args.pack)
    store = Store.connect(args.dsn, pack)
    store.migrate()
    store.sync_sources()
    cache = Path(args.cache_dir or Path("data") / pack.slug / "raw")
    raw_id = import_page(store, args.extractor, args.url, args.file, cache, dict(o.split("=", 1) for o in args.opt))
    print(json.dumps({"raw_page_id": raw_id, "coverage": store.coverage_report()}, indent=2, default=str))
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
