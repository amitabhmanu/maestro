# Running the canon stores on your own machine

The canon stores are Postgres databases, one per maestro, plus a folder of raw pages (every page
fetched or saved by hand, kept by its SHA-256). Both are the private data layer: they stay on the
machine and never go into git.

## One-time setup

1. **PostgreSQL 16 or later with pgvector.** On Windows the EDB installer plus the pgvector build is
   enough; `docker compose up -d` works too where Docker is available. Make the server listen on
   localhost only (`ALTER SYSTEM SET listen_addresses = 'localhost'`, then restart the service).
2. **A login and one database per maestro**, as the postgres superuser:

   ```sql
   CREATE ROLE maestro LOGIN CREATEDB PASSWORD '<random>';
   CREATE DATABASE maestro_rafi OWNER maestro ENCODING 'UTF8' TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C';
   \c maestro_rafi
   CREATE EXTENSION IF NOT EXISTS vector;
   -- repeat for each maestro
   ```

3. **Credentials.** Put the password in `pgpass.conf` (`%APPDATA%\postgresql\pgpass.conf` on Windows,
   `~/.pgpass` elsewhere: `localhost:5432:*:maestro:<password>`), copy `.env.example` to `.env`, and
   fill in one `MAESTRO_DSN_<SLUG>` per maestro. Commands then need only `--pack`. A `--dsn`, or a
   `MAESTRO_DSN_<SLUG>` / `MAESTRO_DSN` set in the shell, always wins over `.env`; the test suite
   ignores `.env` and every `MAESTRO_DSN_*`, so `pytest` never touches the live stores.
4. **Install and migrate:** `uv venv && uv pip install -e ".[dev]"`, then `uv run maestro-migrate --all`.
5. **Restore a store from a dump** (into the empty database):
   `pg_restore --no-owner --no-privileges -d maestro_rafi rafi-....dump`, then `uv run maestro-migrate --all`.

## Every session

- `uv run maestro-doctor --all` checks each store: connection, encoding, pgvector, schema version,
  raw pages on disk, and whether `pg_dump` is found. It exits 1 if something needs attention.
- Work as usual (`maestro-run`, `maestro-resolve`, `maestro-review`, `maestro-report`, ...). Commands
  that merge or record decisions take a lock on the canon, so two sessions cannot interleave merges;
  the second one stops with a message instead of waiting.
- `uv run maestro-backup --all` after a working session (or on a schedule, below).

## Raw pages

`uv run maestro-raw --pack maestros/<slug> status` lists raw_page rows whose page is not on disk.

- `maestro-raw ... fill <folder>` restores pages from saved files, matched by hash (for example the
  hand-saved pages in the project's `manual-imports/<slug>/` folder).
- `maestro-raw ... refetch [--limit N]` fetches crawled pages again, politely (robots.txt and the
  pack's rates apply). A page that changed since is kept as a new row; `maestro-replay` uses it when
  the original is gone.

## Backups

`maestro-backup --all [--out FOLDER] [--keep 14]` writes `FOLDER/<slug>/<slug>-<time>.dump`
(pg_dump custom format), keeps the newest `--keep`, and copies new raw pages into `FOLDER/<slug>/raw/`.
Point `--out` (or `MAESTRO_BACKUP_DIR`) at a folder that is synced or on another disk.

Scheduled on Windows (daily at 23:00):

```
schtasks /Create /SC DAILY /ST 23:00 /TN "maestro backup" /TR "cmd /c cd /d C:\path\to\maestro && uv run maestro-backup --all >> data\backups\backup.log 2>&1"
```

Test a restore now and then: restore the newest dump into a scratch database and compare
`maestro-report` with the live store.
