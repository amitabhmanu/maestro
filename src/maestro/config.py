"""Local configuration: the `.env` file, per-maestro connection strings and the private data folder.

A machine that keeps the canon stores (for now, Amitabh's PC) holds a git-ignored `.env` next to the
repo with one connection string per maestro:

    MAESTRO_DSN_RAFI=postgresql://maestro@localhost:5432/maestro_rafi
    MAESTRO_DSN_EDDY_ARNOLD=...
    MAESTRO_DATA_DIR=C:/Users/me/maestro-data      (optional; default ./data)

Commands then need only `--pack`. Variables already set in the environment win over the file.
"""

from __future__ import annotations

import os
from pathlib import Path

_loaded: set[Path] = set()


def load_env(path: str | Path = ".env") -> dict[str, str]:
    """Read KEY=VALUE lines from `path` into os.environ without overriding what is already set.

    Returns the values the file holds. Blank lines and `#` comments are skipped; values may be quoted.
    """
    path = Path(path)
    values: dict[str, str] = {}
    if not path.is_file():
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
        os.environ.setdefault(key, value)
    _loaded.add(path.resolve())
    return values


def dsn_env_name(slug: str) -> str:
    """`rafi` -> MAESTRO_DSN_RAFI, `eddy-arnold` -> MAESTRO_DSN_EDDY_ARNOLD."""
    return "MAESTRO_DSN_" + "".join(c if c.isalnum() else "_" for c in slug).upper()


def dsn_for(slug: str | None, explicit: str | None = None) -> str | None:
    """The connection string for a maestro: --dsn, else MAESTRO_DSN_<SLUG>, else MAESTRO_DSN."""
    if explicit:
        return explicit
    load_env()
    if slug and os.environ.get(dsn_env_name(slug)):
        return os.environ[dsn_env_name(slug)]
    return os.environ.get("MAESTRO_DSN")


def data_dir() -> Path:
    """Root of the private data layer (raw pages, backups). MAESTRO_DATA_DIR, default ./data."""
    load_env()
    return Path(os.environ.get("MAESTRO_DATA_DIR") or "data")


def raw_dir(slug: str) -> Path:
    """Where a maestro's raw pages are kept: <data dir>/<slug>/raw."""
    return data_dir() / slug / "raw"
