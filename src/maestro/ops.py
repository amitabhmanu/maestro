"""Operating the persistent canon stores: `maestro-migrate`, `maestro-doctor` and `maestro-backup`.

Each takes `--pack maestros/<slug>` (repeatable) or `--all` (every maestro whose MAESTRO_DSN_<SLUG> is
set in the environment or `.env`).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from maestro import config
from maestro.pack import load_pack


def _targets(p: argparse.ArgumentParser, args: argparse.Namespace) -> list[tuple[Any, str]]:
    config.load_env()
    if args.all:
        out = []
        for d in sorted(Path("maestros").iterdir()):
            if (d / "maestro.yaml").is_file():
                pack = load_pack(d)
                dsn = os.environ.get(config.dsn_env_name(pack.slug))
                if dsn:
                    out.append((pack, dsn))
        if not out:
            p.error("--all found no MAESTRO_DSN_<SLUG> settings (environment or .env)")
        return out
    if not args.pack:
        p.error("give --pack maestros/<slug> (repeatable) or --all")
    out = []
    for path in args.pack:
        pack = load_pack(path)
        dsn = config.dsn_for(pack.slug, args.dsn if len(args.pack) == 1 else None)
        if not dsn:
            p.error(f"no store for {pack.slug}: set {config.dsn_env_name(pack.slug)} or pass --dsn")
        out.append((pack, dsn))
    return out


def _parser(doc: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pack", action="append", help="maestro folder (repeatable)")
    p.add_argument("--all", action="store_true", help="every maestro with a MAESTRO_DSN_<SLUG>")
    p.add_argument("--dsn", help="with a single --pack: its store (default from the environment or .env)")
    return p


def pg_tool(name: str) -> str | None:
    """Find a PostgreSQL client program: $MAESTRO_PG_BIN, PATH, then the usual Windows install folder."""
    exe = name + (".exe" if os.name == "nt" else "")
    if os.environ.get("MAESTRO_PG_BIN"):
        cand = Path(os.environ["MAESTRO_PG_BIN"]) / exe
        if cand.is_file():
            return str(cand)
    found = shutil.which(name)
    if found:
        return found
    hits = sorted(glob.glob(f"C:/Program Files/PostgreSQL/*/bin/{exe}"),
                  key=lambda s: int(Path(s).parts[-3]) if Path(s).parts[-3].isdigit() else 0)
    return hits[-1] if hits else None


# ---- maestro-migrate ---------------------------------------------------------------------------------

def migrate_main(argv: list[str] | None = None) -> int:
    """`maestro-migrate`: bring each store's schema up to date (baseline plus numbered migrations)."""
    from maestro.store import Store

    p = _parser(migrate_main.__doc__)
    args = p.parse_args(argv)
    for pack, dsn in _targets(p, args):
        store = Store.connect(dsn, pack)
        applied = store.migrate()
        store.sync_sources()
        print(f"{pack.slug}: schema version {store.schema_version()}"
              + (f" (applied {', '.join(map(str, applied))})" if applied else " (up to date)"))
        store.close()
    return 0


# ---- maestro-doctor ----------------------------------------------------------------------------------

def doctor(store, raw_root: Path | None = None) -> dict[str, Any]:
    """Health of one store: server, encoding, collation, pgvector, schema version, raw pages."""
    from maestro.rawstore import RawStore, status
    from maestro.store import migrations

    c = store.conn
    info = c.execute("""SELECT current_setting('server_version') AS server, current_database() AS db,
                               pg_encoding_to_char(encoding) AS encoding, datcollate AS collate
                        FROM pg_database WHERE datname = current_database()""").fetchone()
    vec = c.execute("""SELECT (SELECT default_version FROM pg_available_extensions WHERE name = 'vector') AS available,
                              (SELECT extversion FROM pg_extension WHERE extname = 'vector') AS installed""").fetchone()
    have = {r["version"] for r in c.execute("SELECT version FROM schema_version").fetchall()}
    pending = [v for v, _ in migrations() if v not in have]
    counts = c.execute("""SELECT (SELECT count(*) FROM claim) AS claims, (SELECT count(*) FROM entity) AS entities,
                                 (SELECT count(*) FROM review_item WHERE status = 'open') AS open_reviews""").fetchone()
    raw = RawStore(raw_root) if raw_root else RawStore.for_slug(store.pack.slug)
    rs = status(store, raw, Path.cwd()).as_dict(sample=3)
    problems = []
    if info["encoding"] != "UTF8":
        problems.append(f"encoding is {info['encoding']}, not UTF8")
    if info["collate"] not in ("C", "POSIX"):
        problems.append(f"collation is {info['collate']}; stores are built with LC_COLLATE C (sorting may differ)")
    if not vec["installed"]:
        problems.append("pgvector is not installed in this database" + ("" if vec["available"] else " (nor available on the server)"))
    if pending:
        problems.append(f"migrations not applied: {pending} (run maestro-migrate)")
    if rs["missing"]:
        problems.append(f"{rs['missing']} of {rs['rows']} raw pages are missing from {raw.root} (see maestro-raw)")
    return {"maestro": store.pack.slug, **info, "pgvector": vec, "schema_version": max(have) if have else 0,
            "pending_migrations": pending, **counts, "raw_dir": str(raw.root), "raw_pages": rs,
            "pg_dump": pg_tool("pg_dump"), "problems": problems}


def doctor_main(argv: list[str] | None = None) -> int:
    """`maestro-doctor`: check each store (connection, encoding, pgvector, schema, raw pages, pg_dump).
    Exits 1 if anything needs attention."""
    from maestro.store import Store

    p = _parser(doctor_main.__doc__)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    results = []
    for pack, dsn in _targets(p, args):
        try:
            store = Store.connect(dsn, pack)
        except Exception as exc:  # report, keep checking the others
            results.append({"maestro": pack.slug, "problems": [f"cannot connect: {exc}"]})
            continue
        results.append(doctor(store))
        store.close()
    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        for r in results:
            if "server" in r:
                print(f"{r['maestro']}: {r['db']} on PostgreSQL {r['server']}, schema v{r['schema_version']}, "
                      f"{r['claims']:,} claims, {r['entities']:,} entities, {r['open_reviews']} open reviews, "
                      f"raw pages {r['raw_pages']['present']}/{r['raw_pages']['rows']}")
            for prob in r["problems"]:
                print(f"  ! {prob}")
            if not r["problems"]:
                print("  ok")
    return 1 if any(r["problems"] for r in results) else 0


# ---- maestro-backup ----------------------------------------------------------------------------------

def backup(slug: str, dsn: str, out: Path, *, keep: int = 14, raw_root: Path | None = None,
           run=subprocess.run, stamp: str | None = None) -> dict[str, Any]:
    """Dump one store (pg_dump custom format) and copy new raw pages into `out/<slug>/`.

    Keeps the newest `keep` dumps. Raw pages are copied only when not already there (their names are
    their hashes, so a copy never changes)."""
    pg_dump = pg_tool("pg_dump")
    if not pg_dump:
        raise RuntimeError("pg_dump not found: put PostgreSQL's bin folder on PATH or set MAESTRO_PG_BIN")
    folder = out / slug
    folder.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    target = folder / f"{slug}-{stamp}.dump"
    tmp = target.with_suffix(".partial")
    run([pg_dump, "--format=custom", "--no-owner", "--no-privileges", f"--file={tmp}", f"--dbname={dsn}"], check=True)
    tmp.replace(target)
    dumps = sorted(folder.glob(f"{slug}-*.dump"))
    for old in dumps[:-keep] if keep > 0 else []:
        old.unlink()
    raw_src = raw_root or config.raw_dir(slug)
    copied = 0
    if raw_src.is_dir():
        dest = folder / "raw"
        dest.mkdir(exist_ok=True)
        for f in raw_src.glob("*.html"):
            if not (dest / f.name).exists():
                shutil.copy2(f, dest / f.name)
                copied += 1
    return {"maestro": slug, "dump": str(target), "bytes": target.stat().st_size,
            "dumps_kept": min(len(dumps), keep) if keep > 0 else len(dumps), "raw_pages_copied": copied}


def backup_main(argv: list[str] | None = None) -> int:
    """`maestro-backup`: dump each store and copy its raw pages to a backup folder.

    maestro-backup --all                          to <MAESTRO_DATA_DIR>/backups (or $MAESTRO_BACKUP_DIR)
    maestro-backup --all --out D:/maestro-backups --keep 30
    Restore one: pg_restore --no-owner --no-privileges -d <empty database> <dump>
    """
    p = _parser(backup_main.__doc__)
    p.add_argument("--out", type=Path, help="backup folder (default $MAESTRO_BACKUP_DIR or <data dir>/backups)")
    p.add_argument("--keep", type=int, default=14, help="dumps to keep per maestro (default 14)")
    args = p.parse_args(argv)
    targets = _targets(p, args)
    out = args.out or Path(os.environ.get("MAESTRO_BACKUP_DIR") or config.data_dir() / "backups")
    failed = 0
    for pack, dsn in targets:
        try:
            print(json.dumps(backup(pack.slug, dsn, out, keep=args.keep)))
        except (subprocess.CalledProcessError, RuntimeError, OSError) as exc:
            failed += 1
            print(f"{pack.slug}: backup failed: {exc}", file=sys.stderr)
    return 1 if failed else 0
