"""Test fixtures. Store tests use MAESTRO_TEST_DSN if set, else a throwaway local Postgres cluster."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PILOTS = ["rafi", "eddy-arnold", "jean-wiener"]


def _pg_bin() -> Path | None:
    found = shutil.which("pg_ctl")
    if found:
        return Path(found).parent
    candidates = sorted(Path("/usr/lib/postgresql").glob("*/bin/pg_ctl"), reverse=True)
    return candidates[0].parent if candidates else None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _no_live_stores(monkeypatch, tmp_path_factory):
    """Tests never see a machine's real stores: no .env, no MAESTRO_DSN_<SLUG>, no data folder."""
    monkeypatch.setenv("MAESTRO_NO_DOTENV", "1")
    for key in list(os.environ):
        if key.startswith("MAESTRO_DSN") or key in ("MAESTRO_DATA_DIR", "MAESTRO_BACKUP_DIR"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("MAESTRO_DATA_DIR", str(tmp_path_factory.mktemp("maestro-data")))


@pytest.fixture(scope="session")
def pg_server():
    """Yields a DSN to an empty server database ('postgres')."""
    if os.environ.get("MAESTRO_TEST_DSN"):
        yield os.environ["MAESTRO_TEST_DSN"]
        return
    bindir = _pg_bin()
    if bindir is None:
        pytest.skip("no Postgres available: install it or set MAESTRO_TEST_DSN")
    tmp = Path(tempfile.mkdtemp(prefix="maestro-pg-"))
    data, port = tmp / "data", _free_port()
    env = {**os.environ}
    run_as: list[str] = []
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        # initdb refuses to run as root; run the cluster as an unprivileged user.
        shutil.chown(tmp, user="nobody")
        run_as = ["runuser", "-u", "nobody", "--"]
    subprocess.run([*run_as, str(bindir / "initdb"), "-D", str(data), "-A", "trust", "-U", "postgres", "-E", "UTF8", "--locale=C"],
                   check=True, capture_output=True, env=env)
    subprocess.run([*run_as, str(bindir / "pg_ctl"), "-D", str(data), "-l", str(tmp / "log"), "-w",
                    "-o", f"-p {port} -k {tmp} -c listen_addresses=127.0.0.1", "start"],
                   check=True, capture_output=True, env=env)
    try:
        yield f"postgresql://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run([*run_as, str(bindir / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
                       capture_output=True, env=env)
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def store_for(pg_server):
    """Factory: a migrated Store for a pilot pack, in a fresh database."""
    import psycopg

    from maestro.pack import load_pack
    from maestro.store import Store

    made = []

    def make(slug: str = "rafi", pack=None):
        dbname = f"t_{len(made)}_{os.getpid()}_{slug.replace('-', '_')}"
        with psycopg.connect(pg_server, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
            admin.execute(f'CREATE DATABASE "{dbname}" ENCODING \'UTF8\' TEMPLATE template0')
        dsn = pg_server.rsplit("/", 1)[0] + f"/{dbname}"
        store = Store.connect(dsn, pack or load_pack(ROOT / "maestros" / slug))
        store.migrate()
        store.sync_sources()
        made.append((store, dbname))
        return store

    yield make
    with psycopg.connect(pg_server, autocommit=True) as admin:
        for store, dbname in made:
            store.close()
            admin.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
