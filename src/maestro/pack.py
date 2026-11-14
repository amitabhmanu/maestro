"""Load and validate a maestro pack (maestros/<slug>/maestro.yaml)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import yaml

PACK_FILENAME = "maestro.yaml"


class PackError(ValueError):
    """The pack is missing, malformed or inconsistent."""


@cache
def _schema() -> dict[str, Any]:
    text = resources.files("maestro").joinpath("maestro-pack.schema.json").read_text("utf-8")
    return json.loads(text)


@dataclass(frozen=True)
class Pack:
    path: Path
    data: dict[str, Any]

    @property
    def slug(self) -> str:
        return self.data["identity"]["slug"]

    @property
    def unit(self) -> str:
        return self.data["canon_model"]["unit"]

    @property
    def container(self) -> str:
        return self.data["canon_model"]["container"]

    @property
    def tiers(self) -> dict[str, dict[str, Any]]:
        return self.data["tiers"]

    def source(self, source_id: str) -> dict[str, Any]:
        for s in self.data["sources"]:
            if s["id"] == source_id:
                return s
        raise PackError(f"unknown source {source_id!r}; add it to the pack's sources first")


def _cross_checks(data: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    roles = data["roles"]
    if roles["primary"] not in roles["in_scope"]:
        problems.append("roles.primary must be listed in roles.in_scope")
    overlap = set(roles["in_scope"]) & set(roles.get("out_of_scope", []))
    if overlap:
        problems.append(f"roles in both in_scope and out_of_scope: {sorted(overlap)}")

    tiers = set(data["tiers"])
    for reg in data.get("registers", []):
        unknown = set(reg["tiers"]) - tiers
        if unknown:
            problems.append(f"register {reg['name']!r} names unknown tiers {sorted(unknown)}")

    for src in data["sources"]:
        for key in ("default_tier", "live_tier"):
            if src.get(key) and src[key] not in tiers:
                problems.append(f"source {src['id']!r} {key} {src[key]!r} is not a tier")
        if src.get("live_tier") and not src.get("default_tier"):
            problems.append(f"source {src['id']!r} has live_tier without default_tier")

    for kind, tier in (data["canon_model"].get("kind_tiers") or {}).items():
        if tier not in tiers:
            problems.append(f"canon_model.kind_tiers {kind!r} names unknown tier {tier!r}")

    ids = [s["id"] for s in data["sources"]]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        problems.append(f"duplicate source ids: {sorted(dupes)}")

    for sid in (data.get("media_links") or {}).get("sources", {}):
        if sid not in ids:
            problems.append(f"media_links.sources names unknown source {sid!r}")

    if data["verification"]["method"] == "voice" and data["roles"]["primary"] != "singer":
        problems.append("verification.method 'voice' only makes sense when the primary role is singer")

    if data["legal"]["purpose"] != "personal" and data["legal"]["lyrics"] == "private_full":
        problems.append("legal.lyrics 'private_full' is only allowed when legal.purpose is 'personal'")
    if data["legal"]["lyrics"] == "none" and data["legal"].get("lyric_fields"):
        problems.append("legal.lyric_fields lists fields that quote lyrics, but legal.lyrics is 'none'")
    return problems


def validate(data: Any) -> list[str]:
    """Return a list of human-readable problems; empty means the pack is valid."""
    validator = jsonschema.Draft202012Validator(_schema())
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    problems = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors]
    if not problems:
        problems = _cross_checks(data)
    return problems


def load_pack(path: str | Path) -> Pack:
    """Load a pack from a maestro folder or a maestro.yaml path, raising PackError if invalid."""
    p = Path(path)
    if p.is_dir():
        p = p / PACK_FILENAME
    if not p.is_file():
        raise PackError(f"no pack at {p}")
    try:
        data = yaml.safe_load(p.read_text("utf-8"))
    except yaml.YAMLError as exc:
        raise PackError(f"{p}: not valid YAML: {exc}") from exc
    problems = validate(data)
    if problems:
        raise PackError(f"{p} is not a valid maestro pack:\n  - " + "\n  - ".join(problems))
    if data["identity"]["slug"] != p.parent.name:
        raise PackError(f"{p}: identity.slug {data['identity']['slug']!r} must match folder name {p.parent.name!r}")
    return Pack(path=p, data=data)
