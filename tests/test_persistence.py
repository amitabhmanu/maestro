"""Persistent stores: .env and DSN lookup, raw page store, migrations, locking, doctor and backup."""

import shutil
from pathlib import Path

import psycopg
import pytest

from conftest import ROOT
from maestro import config
from maestro.crawl.runner import import_page, replay
from maestro.ops import backup, doctor
from maestro.pack import load_pack
from maestro.rawstore import RawStore, fill_from_dir, refetch, status
from maestro.store import Store, StoreError, migrations
from test_crawl import FakeWeb
from maestro.crawl.fetcher import Fetcher


# ---- config ----------------------------------------------------------------------------------------

def test_env_file_and_dsn_lookup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAESTRO_NO_DOTENV")
    for k in ("MAESTRO_DSN", "MAESTRO_DSN_EDDY_ARNOLD", "MAESTRO_DSN_RAFI", "MAESTRO_DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / ".env").write_text(
        "# local stores\nMAESTRO_DSN_EDDY_ARNOLD='postgresql://maestro@localhost/arnold'\n"
        "export MAESTRO_DSN=postgresql://fallback\nMAESTRO_DATA_DIR=store-data\n", "utf-8")
    monkeypatch.setenv("MAESTRO_DSN_RAFI", "postgresql://from-env/rafi")
    assert config.dsn_env_name("eddy-arnold") == "MAESTRO_DSN_EDDY_ARNOLD"
    assert config.dsn_for("eddy-arnold") == "postgresql://maestro@localhost/arnold"
    assert config.dsn_for("rafi") == "postgresql://from-env/rafi"          # environment wins over .env
    assert config.dsn_for("jean-wiener") == "postgresql://fallback"         # then MAESTRO_DSN in .env
    # A MAESTRO_DSN set in the environment (as tests and one-off runs do) beats every .env setting.
    monkeypatch.setenv("MAESTRO_DSN", "postgresql://scratch")
    assert config.dsn_for("eddy-arnold") == "postgresql://scratch"
    assert config.dsn_for("rafi") == "postgresql://from-env/rafi"
    monkeypatch.delenv("MAESTRO_DSN")
    monkeypatch.setenv("MAESTRO_NO_DOTENV", "1")
    assert config.dsn_for("eddy-arnold") is None
    assert config.dsn_for("rafi", "postgresql://explicit") == "postgresql://explicit"
    monkeypatch.delenv("MAESTRO_NO_DOTENV")
    monkeypatch.delenv("MAESTRO_DATA_DIR", raising=False)
    assert config.raw_dir("rafi") == Path("store-data") / "rafi" / "raw"


# ---- raw store -------------------------------------------------------------------------------------

def test_raw_store_keeps_pages_by_hash(tmp_path):
    raw = RawStore(tmp_path / "raw")
    sha, path = raw.put(b"<html>one</html>")
    assert path == tmp_path / "raw" / f"{sha}.html" and path.read_bytes() == b"<html>one</html>"
    assert raw.put(b"<html>one</html>") == (sha, path)
    elsewhere = tmp_path / "old" / "x.html"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"old")
    assert raw.locate("0" * 64, str(elsewhere)) == elsewhere                 # falls back to the recorded path
    assert raw.locate("0" * 64, "nowhere.html") is None
    with pytest.raises(FileNotFoundError):
        raw.read("0" * 64)


def _pack_with_extractor(tmp_path):
    """A copy of a pilot pack with a tiny extractor that records the page's text as a claim."""
    folder = tmp_path / "pack" / "jean-wiener"
    shutil.copytree(ROOT / "maestros" / "jean-wiener", folder, ignore=shutil.ignore_patterns("runs", "data"))
    (folder / "extractors").mkdir(exist_ok=True)
    (folder / "extractors" / "echo.py").write_text(
        "def extract(ctx):\n"
        "    eid = ctx.store.find_or_create_by_xref('work', 'user', ctx.page.url, ctx.page.url)\n"
        "    ctx.store.upsert_claim(eid, 'page_text', value=ctx.page.text, source_id='user', raw_page_id=ctx.raw_page_id)\n",
        "utf-8")
    return load_pack(folder)


def test_missing_pages_are_found_refilled_and_refetched(tmp_path, store_for):
    pack = _pack_with_extractor(tmp_path)
    store = store_for("jean-wiener", pack=pack)
    raw = RawStore(tmp_path / "raw")
    saved = tmp_path / "saved.html"
    saved.write_bytes(b"<html>saved by hand</html>")
    imported = import_page(store, "echo", "https://example.org/saved", saved, raw.root)

    web = FakeWeb({"https://example.org/robots.txt": b"", "https://example.org/a": b"<html>A</html>"})
    fetcher = Fetcher(raw.root, 100, opener=web.open, clock=web.clock, sleep=web.sleep)
    page = fetcher.fetch("https://example.org/a")
    rid = store.record_raw_page(page.url, 200, page.sha256, str(page.path))
    store.conn.execute("INSERT INTO job (url, extractor, purpose, status, raw_page_id) VALUES (%s, 'echo', 'spine', 'done', %s)",
                       (page.url, rid))
    assert status(store, raw).as_dict()["missing"] == 0

    for f in raw.root.glob("*.html"):
        f.unlink()
    st = status(store, raw).as_dict()
    assert (st["rows"], st["missing"], st["missing_crawled"], st["missing_imported"]) == (2, 2, 1, 1)

    assert fill_from_dir(store, raw, tmp_path, tmp_path)["restored"] == 1    # saved.html matches by hash
    assert [m["id"] for m in status(store, raw).missing] == [rid]

    assert refetch(store, raw, fetcher) == {"restored": 1, "changed": 0, "disallowed": 0, "failed": 0}
    assert status(store, raw).as_dict()["missing"] == 0

    # The page changed online since: the new copy is kept as its own row and replay falls back to it.
    raw.path_for(page.sha256).unlink()
    web.pages["https://example.org/a"] = b"<html>A, revised</html>"
    assert refetch(store, raw, fetcher)["changed"] == 1
    st = status(store, raw).as_dict()
    assert (st["missing"], st["replaced"]) == (0, 1)       # the old copy is gone, a newer one stands in
    assert refetch(store, raw, fetcher)["changed"] == 0    # and it is not fetched again
    target = store_for("jean-wiener", pack=pack)
    stats = replay(store, target, raw=raw)
    assert (stats.done, stats.failed) == (2, 0)
    texts = {r["value"] for r in target.conn.execute("SELECT value FROM claim WHERE field = 'page_text'")}
    assert texts == {"<html>saved by hand</html>", "<html>A, revised</html>"}
    assert imported


# ---- migrations and locking ------------------------------------------------------------------------

def test_migrations_apply_once(store_for):
    store = store_for("rafi")
    latest = max(v for v, _ in migrations())
    assert latest >= 3
    assert store.schema_version() == latest
    assert store.migrate() == []
    store.conn.execute("DELETE FROM schema_version WHERE version = %s", (latest,))
    assert store.migrate() == [latest]


def _dsn(pg_server, store):
    """The store's DSN with the server's credentials (conn.info.dsn leaves the password out)."""
    return pg_server.rsplit("/", 1)[0] + "/" + store.conn.info.dbname


def test_only_one_session_may_write_decisions(store_for, pg_server):
    store = store_for("rafi")
    dsn = _dsn(pg_server, store)
    other = Store.connect(dsn, store.pack)
    try:
        with store.exclusive():
            with pytest.raises(StoreError, match="already running"):
                with other.exclusive():
                    pass
        with other.exclusive():  # free again once the first session lets go
            pass
    finally:
        other.close()


# ---- doctor and backup -----------------------------------------------------------------------------

def test_doctor_reports_state_and_problems(tmp_path, store_for):
    store = store_for("rafi")
    store.record_raw_page("https://example.org/x", 200, "f" * 64, "data/rafi/raw/" + "f" * 64 + ".html")
    report = doctor(store, tmp_path / "raw")
    assert report["schema_version"] == max(v for v, _ in migrations()) and report["pending_migrations"] == []
    assert report["raw_pages"]["missing"] == 1
    assert any("raw pages are missing" in p for p in report["problems"])


def test_backup_dumps_prunes_and_copies_raw_pages(tmp_path, monkeypatch):
    monkeypatch.setattr("maestro.ops.pg_tool", lambda name: "/usr/bin/pg_dump")
    calls = []

    def fake_run(cmd, check):
        calls.append(cmd)
        Path(next(a for a in cmd if a.startswith("--file=")).split("=", 1)[1]).write_bytes(b"PGDMP")

    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / ("a" * 64 + ".html")).write_bytes(b"a")
    out = tmp_path / "backups"
    for i in range(3):
        r = backup("rafi", "postgresql://maestro@localhost/maestro_rafi", out, keep=2, raw_root=raw,
                   run=fake_run, stamp=f"2026111{i}-000000")
    assert sorted(p.name for p in (out / "rafi").glob("*.dump")) == ["rafi-20261111-000000.dump", "rafi-20261112-000000.dump"]
    assert r["raw_pages_copied"] == 0 and (out / "rafi" / "raw" / ("a" * 64 + ".html")).exists()
    assert "--dbname=postgresql://maestro@localhost/maestro_rafi" in calls[0]


def test_real_pg_dump_round_trip(tmp_path, store_for, pg_server):
    """When pg_dump is installed, a backup restores into an empty database with the same claims."""
    from maestro.ops import pg_tool

    if not pg_tool("pg_dump") or not pg_tool("pg_restore"):
        pytest.skip("pg_dump/pg_restore not installed")
    import re
    import subprocess

    store = store_for("rafi")
    version = subprocess.run([pg_tool("pg_restore"), "--version"], capture_output=True, text=True).stdout
    client = int(re.search(r"\)\s*(\d+)", version).group(1))
    server = int(store.conn.execute("SHOW server_version_num").fetchone()["server_version_num"]) // 10000
    if client > server:
        pytest.skip(f"pg_restore {client} writes settings a PostgreSQL {server} server does not know")
    eid = store.create_entity("song", "Test song")
    store.upsert_claim(eid, "title", value="Test song", source_id="user")
    r = backup("rafi", _dsn(pg_server, store), tmp_path, raw_root=tmp_path / "none")
    with psycopg.connect(pg_server, autocommit=True) as admin:
        admin.execute('DROP DATABASE IF EXISTS "t_restore"')
        admin.execute("CREATE DATABASE t_restore ENCODING 'UTF8' TEMPLATE template0")
    dsn = pg_server.rsplit("/", 1)[0] + "/t_restore"
    subprocess.run([pg_tool("pg_restore"), "--no-owner", "--no-privileges", f"--dbname={dsn}", r["dump"]], check=True)
    restored = Store.connect(dsn, store.pack)
    try:
        n = restored.conn.execute("SELECT count(*) AS n FROM claim WHERE field = 'title'").fetchone()["n"]
        assert n == 1 and restored.schema_version() == store.schema_version()
    finally:
        restored.close()
        with psycopg.connect(pg_server, autocommit=True) as admin:
            admin.execute('DROP DATABASE IF EXISTS "t_restore"')
