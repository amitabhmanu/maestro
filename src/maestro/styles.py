"""A canon's style taxonomy (for Hindi film canons, the raga) in the explorer snapshot.

A pack names its taxonomy (`taxonomies.style`, e.g. raga) and the table of names that goes with it
(`lexicon.styles`, a TSV such as maestros/hindi-films/lexicon/ragas.tsv: name, other names, thaat, time,
season, light, mood). Sources word a raga their own way ("Yaman / Kalyani (raga)", "Desh or Des",
"Sohni / Hamsanandi & Bahar"): `names()` reads the table's names out of such a label.

`add_style(snap, pack_dir)` gives each snapshot unit `styles`, one entry per raga:
    {"n": name, "src": [sources that say so], "as": [labels as the sources wrote them], "st": status}
(from the unit's `raga` labels and `raga_src`, label -> sources, which the snapshot keeps), where status is `confirmed` (the user said so: a `user` claim), `sourced` (a site says so) or
`inferred` (a model, or the original song of a reused one; never counted as a new song). A unit whose
sources name ragas that share nothing is `style_disputed`. The snapshot gets `style`: the taxonomy,
the table's entries for the names in use, and how many units have one. It reads only the snapshot,
so `maestro-explore --relink` can add it to a saved snapshot without the database.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from maestro.resolve import fold

TIMES = {  # traditional prahars (Bhatkhande's time theory), in the order of a day
    "d1": "Early morning, 6-9 am", "d2": "Late morning, 9-12", "d3": "Afternoon, 12-3 pm",
    "d4": "Late afternoon to sunset, 3-6 pm", "n1": "Evening, 6-9 pm", "n2": "Night, 9-12",
    "n3": "Midnight, 12-3 am", "n4": "Before dawn, 3-6 am",
}
STATUS_ORDER = {"confirmed": 0, "sourced": 1, "inferred": 2}
INFERRED_SOURCES = {"audio", "reuse"}


def _key(s: str) -> str:
    return fold(s).replace(" ", "")


def load_table(path: Path | None) -> dict[str, dict[str, Any]]:
    """Name -> {thaat, time, season, light, mood, aliases}; '#' lines are comments."""
    table: dict[str, dict[str, Any]] = {}
    if not path or not path.exists():
        return table
    with path.open(encoding="utf-8") as f:
        for row in csv.reader((ln for ln in f if ln.strip() and not ln.startswith("#")), delimiter="\t"):
            row += [""] * (7 - len(row))
            name, aliases, thaat, time, season, light, mood = (x.strip() for x in row[:7])
            table[name] = {"aliases": [a.strip() for a in aliases.split("|") if a.strip()], "thaat": thaat or None,
                           "time": time or None, "season": season or None, "light": light.lower() == "yes",
                           "mood": mood or None}
    return table


class Names:
    """Reads a table's names out of a source's label."""

    def __init__(self, table: dict[str, dict[str, Any]]):
        self.table = table
        self.index: dict[str, str] = {}
        for name, e in table.items():
            for n in (name, *e["aliases"]):
                self.index.setdefault(_key(n), name)

    def _one(self, part: str) -> str | None:
        part = re.sub(r"\[[^\]]*\]", "", part)
        part = re.sub(r"\((?:raga|raag|hindustani|carnatic|hindustani raga|eastern folk version)\)", "", part, flags=re.I)
        part = re.sub(r"^\s*(?:raa?g[a]?)\s+", "", part, flags=re.I).strip(" \"'“”.,")
        if not part:
            return None
        if _key(part) in self.index:
            return self.index[_key(part)]
        bare = re.sub(r"\([^)]*\)", " ", part).strip()
        if _key(bare) in self.index:
            return self.index[_key(bare)]
        for paren in re.findall(r"\(([^)]*)\)", part):   # "(Alaiya) Bilawal", "Gour Sarang (Goud Sarang)"
            if _key(paren) in self.index:
                return self.index[_key(paren)]
        words = fold(bare).split()                        # "Bhageshri (Carnatic) Bageshri (Hindustani)"
        for w in words:
            if _key(w) in self.index and len(w) >= 4:
                return self.index[_key(w)]
        return None

    def names(self, label: str) -> list[str]:
        """The table's names in a label, in order. Several ragas ("A & B", "A + B") give several names;
        alternatives ("Yaman / Kalyani", "Desh or Des") give the first that the table knows. A label the
        table does not know is kept as written (cleaned), so nothing a source said is lost."""
        out: list[str] = []
        for part in re.split(r"\s*(?:&|\+|,|\band\b)\s*", label):
            if not part.strip():
                continue
            alts = [a for a in re.split(r"\s*(?:/|\bor\b)\s*", part) if a.strip()]
            hit = next((n for n in map(self._one, alts) if n), None)
            if hit is None:
                hit = re.sub(r"\s*\[[^\]]*\]|\s*\((?:raga|raag)\)", "", alts[0] if alts else part).strip(" \"'“”")
            if hit and hit not in out:
                out.append(hit)
        return out


def table_path(pack_data: dict[str, Any], pack_dir: Path | None) -> Path | None:
    rel = (pack_data.get("lexicon") or {}).get("styles")
    if isinstance(rel, list):
        rel = rel[0] if rel else None
    return (pack_dir / rel) if rel and pack_dir else None


def unit_styles(labels: dict[str, list[str]], names: Names) -> tuple[list[dict[str, Any]], bool]:
    """labels: a label as a source wrote it -> the sources that wrote it. Returns the unit's `styles`
    and whether its sources disagree outright."""
    by: dict[str, dict[str, Any]] = {}
    per_source: dict[str, set[str]] = {}
    for label, sources in labels.items():
        for n in names.names(label):
            e = by.setdefault(n, {"n": n, "src": [], "as": []})
            for s in sources:
                if s not in e["src"]:
                    e["src"].append(s)
                per_source.setdefault(s, set()).add(n)
            if label not in e["as"] and _key(label) != _key(n):
                e["as"].append(label)
    for e in by.values():
        e["st"] = ("confirmed" if "user" in e["src"] else
                   "sourced" if any(s not in INFERRED_SOURCES for s in e["src"]) else "inferred")
        if not e["as"]:
            del e["as"]
    out = sorted(by.values(), key=lambda e: (STATUS_ORDER[e["st"]], -len(e["src"]), e["n"]))
    if any(e["st"] == "confirmed" for e in out):       # the user's word settles it: the rest stays as evidence
        return out, False
    sets = [v for s, v in per_source.items() if s not in INFERRED_SOURCES]
    disputed = len(sets) > 1 and not set.intersection(*sets)
    return out, disputed


def add_style(snap: dict[str, Any], pack_data: dict[str, Any], pack_dir: Path | None) -> dict[str, Any]:
    """Add `styles` to each unit and `style` to the snapshot (see the module doc)."""
    taxonomy = (pack_data.get("taxonomies") or {}).get("style")
    table = load_table(table_path(pack_data, pack_dir))
    if not taxonomy or not table:
        snap.pop("style", None)
        return snap
    names = Names(table)
    used: set[str] = set()
    counts = {"units": 0, "disputed": 0, "by_status": {}}
    for u in snap.get("units", []):
        labels: dict[str, list[str]] = {}
        src_map = u.get("raga_src") or {}  # kept, so a later --relink still knows the sources
        for label in u.get("raga", []):
            labels[label] = src_map.get(label) or ["unrecorded"]
        u.pop("styles", None)
        u.pop("style_disputed", None)
        if not labels:
            continue
        styles, disputed = unit_styles(labels, names)
        if not styles:
            continue
        u["styles"] = styles
        if disputed:
            u["style_disputed"] = True
            counts["disputed"] += 1
        counts["units"] += 1
        best = styles[0]["st"]
        counts["by_status"][best] = counts["by_status"].get(best, 0) + 1
        used.update(e["n"] for e in styles)
    snap["style"] = {
        "taxonomy": taxonomy, "times": TIMES,
        "table": {n: {k: v for k, v in table[n].items() if v not in (None, False, [])}
                  for n in sorted(used) if n in table},
        "unknown": sorted(n for n in used if n not in table),
        **counts,
    }
    return snap
