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

from maestro import config
from maestro.crawl.fetcher import Fetcher, api_headers, api_hosts
from maestro.crawl.runner import import_page, replay, run_jobs
from maestro.pack import load_pack
from maestro.store import Store


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pack", required=True, help="maestro folder, e.g. maestros/rafi")
    p.add_argument("--dsn", help="Postgres DSN (default $MAESTRO_DSN_<SLUG>, then $MAESTRO_DSN; .env is read)")
    p.add_argument("--seed", action="append", default=[], help="URL to queue before running (repeatable)")
    p.add_argument("--extractor", help="extractor for --seed URLs")
    p.add_argument("--purpose", default="spine", help="purpose for --seed jobs")
    p.add_argument("--opt", action="append", default=[], help="extractor option key=value (repeatable)")
    p.add_argument("--max-jobs", type=int, default=100)
    p.add_argument("--cache-dir", default=None, help="raw page folder (default <MAESTRO_DATA_DIR>/<slug>/raw)")
    args = p.parse_args(argv)
    if args.seed and not args.extractor:
        p.error("--seed needs --extractor")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pack = load_pack(args.pack)
    args.dsn = config.dsn_for(pack.slug, args.dsn)
    if not args.dsn:
        p.error(f"--dsn (or {config.dsn_env_name(pack.slug)} or MAESTRO_DSN) is required")
    store = Store.connect(args.dsn, pack)
    store.migrate()
    store.sync_sources()
    for url in args.seed:
        store.enqueue_crawl(url, args.extractor, args.purpose, priority=1000)

    cache = Path(args.cache_dir) if args.cache_dir else config.raw_dir(pack.slug)
    fetcher = Fetcher(cache, rps=pack.data["budget"]["per_domain_rps"], api_hosts=api_hosts(pack.data),
                          api_headers=api_headers(pack.data))
    options = dict(o.split("=", 1) for o in args.opt)
    stats = run_jobs(store, fetcher, max_jobs=args.max_jobs, options=options)
    print(json.dumps({"run": stats.as_dict(), "coverage": store.coverage_report()}, indent=2, default=str))
    store.close()
    return 0


def import_main(argv: list[str] | None = None) -> int:
    """`maestro-import`: run an extractor over pages saved by hand (sites whose robots.txt forbids crawling)."""
    p = argparse.ArgumentParser(description=import_main.__doc__)
    p.add_argument("--pack", required=True, help="maestro folder, e.g. maestros/rafi")
    p.add_argument("--dsn", help="Postgres DSN (default $MAESTRO_DSN_<SLUG>, then $MAESTRO_DSN; .env is read)")
    p.add_argument("--extractor", required=True)
    p.add_argument("--url", required=True, help="the URL the page was saved from")
    p.add_argument("--opt", action="append", default=[], help="extractor option key=value (repeatable)")
    p.add_argument("--cache-dir", default=None, help="raw page folder (default <MAESTRO_DATA_DIR>/<slug>/raw)")
    p.add_argument("file", type=Path)
    args = p.parse_args(argv)
    pack = load_pack(args.pack)
    args.dsn = config.dsn_for(pack.slug, args.dsn)
    if not args.dsn:
        p.error(f"--dsn (or {config.dsn_env_name(pack.slug)} or MAESTRO_DSN) is required")
    store = Store.connect(args.dsn, pack)
    store.migrate()
    store.sync_sources()
    cache = Path(args.cache_dir) if args.cache_dir else config.raw_dir(pack.slug)
    raw_id = import_page(store, args.extractor, args.url, args.file, cache, dict(o.split("=", 1) for o in args.opt))
    print(json.dumps({"raw_page_id": raw_id, "coverage": store.coverage_report()}, indent=2, default=str))
    store.close()
    return 0


def replay_main(argv: list[str] | None = None) -> int:
    """`maestro-replay`: rebuild a store from the raw cache of another (after an extractor or core fix)."""
    p = argparse.ArgumentParser(description=replay_main.__doc__)
    p.add_argument("--pack", required=True, help="maestro folder, e.g. maestros/rafi")
    p.add_argument("--from-dsn", required=True, help="store whose fetched and imported pages are replayed")
    p.add_argument("--dsn", required=True, help="target store: a new empty database (never the live store, "
                                                "unless --extractor limits the replay to one source's pages)")
    p.add_argument("--extractor", action="append", help="replay only this extractor's pages (repeatable)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pack = load_pack(args.pack)
    args.dsn = config.dsn_for(pack.slug, args.dsn)
    if not args.dsn:
        p.error(f"--dsn (or {config.dsn_env_name(pack.slug)} or MAESTRO_DSN) is required")
    source, target = Store.connect(args.from_dsn, pack), Store.connect(args.dsn, pack)
    target.migrate()
    target.sync_sources()
    source.migrate()  # older stores may lack import_log
    stats = replay(source, target, only=set(args.extractor or []) or None)
    print(json.dumps({"replay": stats.as_dict(), "coverage": target.coverage_report()}, indent=2, default=str))
    source.close()
    target.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
