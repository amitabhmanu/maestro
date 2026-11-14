"""Local configuration: the `.env` file, per-maestro connection strings and the private data folder.

A machine that keeps the canon stores (for now, Amitabh's PC) holds a git-ignored `.env` next to the
repo with one connection string per maestro:

    MAESTRO_DSN_RAFI=postgresql://maestro@localhost:5432/maestro_rafi
    MAESTRO_DSN_EDDY_ARNOLD=...
    MAESTRO_DATA_DIR=C:/Users/me/maestro-data      (optional; default ./data)

Commands then need only `--pack`. Anything set in the process environment wins over the file:
`--dsn`, then MAESTRO_DSN_<SLUG> and MAESTRO_DSN from the environment, then the same two from `.env`.
Setting MAESTRO_NO_DOTENV=1 (the test suite does) ignores the file altogether.
"""

from __future__ import annotations

import os
from pathlib import Path

_loaded: set[Path] = set()


def read_env_file(path: str | Path = ".env") -> dict[str, str]:
    """The KEY=VALUE pairs in `path` ({} if absent or MAESTRO_NO_DOTENV is set). Blank lines and `#`
    comments are skipped; values may be quoted."""
    path = Path(path)
    values: dict[str, str] = {}
    if os.environ.get("MAESTRO_NO_DOTENV") or not path.is_file():
        return values
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_env(path: str | Path = ".env") -> dict[str, str]:
    """Copy `.env` settings other than connection strings into os.environ, without overriding what is
    already set (connection strings are looked up by `dsn_for`, which ranks the environment first)."""
    values = read_env_file(path)
    for key, value in values.items():
        if not key.startswith("MAESTRO_DSN"):
            os.environ.setdefault(key, value)
    _loaded.add(Path(path).resolve())
    return values


def dsn_env_name(slug: str) -> str:
    """`rafi` -> MAESTRO_DSN_RAFI, `eddy-arnold` -> MAESTRO_DSN_EDDY_ARNOLD."""
    return "MAESTRO_DSN_" + "".join(c if c.isalnum() else "_" for c in slug).upper()


def dsn_for(slug: str | None, explicit: str | None = None) -> str | None:
    """The connection string for a maestro: --dsn; else MAESTRO_DSN_<SLUG>, then MAESTRO_DSN, from the
    process environment; else the same two from `.env`."""
    if explicit:
        return explicit
    file = read_env_file()
    for source in (os.environ, file):
        if slug and source.get(dsn_env_name(slug)):
            return source[dsn_env_name(slug)]
        if source.get("MAESTRO_DSN"):
            return source["MAESTRO_DSN"]
    return None


def setting(name: str) -> str | None:
    """A setting such as an API token: the process environment first, then `.env`. Never logged."""
    return os.environ.get(name) or read_env_file().get(name) or None


def data_dir() -> Path:
    """Root of the private data layer (raw pages, backups). MAESTRO_DATA_DIR, default ./data."""
    load_env()
    return Path(os.environ.get("MAESTRO_DATA_DIR") or "data")


def raw_dir(slug: str) -> Path:
    """Where a maestro's raw pages are kept: <data dir>/<slug>/raw."""
    return data_dir() / slug / "raw"
