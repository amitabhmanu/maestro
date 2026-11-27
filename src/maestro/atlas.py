"""The atlas lexicons: places, genres and waves shared by every canon and register.

Three TSV tables (default folder `maestros/atlas/lexicon/`; a pack may name its own under
`lexicon.places`, `lexicon.genres`, `lexicon.waves`):

- places.tsv: id, name, kind (world, region, country, state, city, historical), parent, wikidata, lat, lon,
  aliases (| separated other and old names, e.g. Bombay|बंबई), valid_from, valid_to, roles, notes.
  Countries use ISO 3166 alpha-3 ids in lower case, so they join the map shapes.
- genres.tsv: id, name, parent, kind (music, film, theme), wikidata, maps_from (| separated source:label
  pairs, such as hindigeetmala:Ghazals or hfgk:सामाजिक), notes. Each kind is a tree.
- waves.tsv: id, name, industry, kind (era, movement), start, end (blank = ongoing), definition, rule,
  sources (| separated URLs), wikidata, notes. Eras of one industry tile its timeline without overlap.

`load()` reads them, `check()` lists every problem (an empty list means the tables are sound), and
`Places.find()` / `Genres.lookup()` read a place or a genre out of a source's label. `maestro-atlas`
runs the check, reports how much of a register snapshot the tables cover, and writes the JSON the
review page reads. Nothing here is specific to one maestro.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maestro.resolve import fold

DEFAULT_DIR = Path("maestros/atlas/lexicon")
PLACE_KINDS = {"world", "region", "country", "state", "city", "historical"}
PLACE_ROLES = {"film_centre", "music_centre", "birthplace"}
GENRE_KINDS = {"music", "film", "theme"}
WAVE_KINDS = {"era", "movement"}
RULE_TERMS = {"year", "curated", "genre", "producer", "studio", "released_before", "release", "films", "wikidata"}
QID = re.compile(r"^Q\d+$")


def _rows(path: Path) -> list[dict[str, str]]:
    """A TSV with '#' comment lines and a header row, as dicts (cells stripped)."""
    with path.open(encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip() and not ln.startswith("#")]
    reader = csv.reader(lines, delimiter="\t", quoting=csv.QUOTE_NONE)
    header = [h.strip() for h in next(reader)]
    out = []
    for n, row in enumerate(reader, start=2):
        row += [""] * (len(header) - len(row))
        d = {h: (row[i].strip() if i < len(row) else "") for i, h in enumerate(header)}
        d["_line"] = str(n)
        if len(row) > len(header):
            d["_extra"] = "\t".join(row[len(header):])
        out.append(d)
    return out


def _split(s: str, sep: str = "|") -> list[str]:
    return [x.strip() for x in s.split(sep) if x.strip()]


def _year(s: str) -> int | None:
    return int(s) if s.strip().isdigit() else None


def _key(s: str) -> str:
    return fold(s).replace(" ", "")


@dataclass
class Places:
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    index: dict[str, str] = field(default_factory=dict)  # folded name or alias -> id

    def find(self, label: str) -> list[str]:
        """Place ids named in a label such as 'Filmistan Limited, Bombay' or 'Bombay/Madras'.

        Reads the last comma part (where banners put the city), split on '/', and drops
        bracketed notes. When that part names only a country ('..., Bombay/Los Angeles, America'),
        the part before it is read too and its cities win. Returns [] when nothing matches."""
        parts = label.split(",")
        tails = parts[:0:-1] or parts  # comma parts after the company name, last first
        found: list[str] = []
        for tail in tails[:2]:
            out = []
            for part in re.split(r"[/&]", tail):
                part = re.sub(r"\(.*?\)", "", part).strip()
                pid = self.index.get(_key(part)) if part else None
                if pid and pid not in out:
                    out.append(pid)
            if any(self.rows[p]["kind"] != "country" for p in out):
                return out
            found = found or out
        return found

    def ancestors(self, pid: str) -> list[str]:
        out, seen = [], set()
        cur = self.rows.get(pid, {}).get("parent")
        while cur and cur not in seen:
            seen.add(cur)
            out.append(cur)
            cur = self.rows.get(cur, {}).get("parent")
        return out


@dataclass
class Genres:
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    index: dict[tuple[str, str], str] = field(default_factory=dict)  # (source, folded label) -> id
    by_qid: dict[tuple[str, str], str] = field(default_factory=dict)  # (kind, Wikidata QID) -> id

    def lookup(self, source: str, label: str) -> str | None:
        return self.index.get((source, _key(label)))

    def lookup_all(self, source: str, label: str) -> list[str]:
        """Every node a label names; compound labels ('Social and Stunt') give one per part, and a two-word
        label no table lists ('Crime comedy') one per word when each word is a label."""
        hit = self.index.get((source, _key(label)))
        if hit:
            return [hit]
        out: list[str] = []
        for part in re.split(r"\s+and\s+|\s*,\s*", label):
            h = self.index.get((source, _key(part))) or self.index.get((source, _key(part + "-oriented")))
            if h and h not in out:
                out.append(h)
        words = label.split()
        if not out and len(words) == 2:
            hits = [self.index.get((source, _key(w))) for w in words]
            if all(hits):
                out = list(dict.fromkeys(hits))
        return out


@dataclass
class Atlas:
    places: Places
    genres: Genres
    waves: dict[str, dict[str, Any]]
    wave_films: list[dict[str, Any]] = field(default_factory=list)
    studios: dict[str, dict[str, Any]] = field(default_factory=dict)
    studio_index: dict[str, str] = field(default_factory=dict)  # folded name or alias -> studio id
    _listed: dict[str, set[str]] | None = None

    def studio(self, name: str) -> str | None:
        """The studio a production company or Notes label names ('MGM', 'Metro-Goldwyn-Mayer Pictures')."""
        return self.studio_index.get(_key(name)) or self.studio_index.get(_key(re.sub(r"\s*\(.*?\)", "", name)))

    def listed(self) -> dict[str, set[str]]:
        """Confirmed films of each curated wave, keyed by title|year and by Wikidata id."""
        if self._listed is None:
            self._listed = {}
            for r in self.wave_films:
                if r.get("status", "confirmed") in ("confirmed", ""):
                    keys = self._listed.setdefault(r["wave"], set())
                    keys.add(_title_key(r.get("title", ""), r.get("year")))
                    if r.get("wikidata"):
                        keys.add(r["wikidata"])
        return self._listed

    def waves_for(self, industry: str, year: int | None, kind: str = "era") -> list[str]:
        """Waves whose year rule alone places a film of that industry and year (eras, by default)."""
        if year is None:
            return []
        return [w["id"] for w in self.waves.values()
                if w["industry"] == industry and w["kind"] == kind and w["rule"].strip() == "year"
                and w["start"] <= year <= (w["end"] or 9999)]


def load(folder: Path = DEFAULT_DIR) -> Atlas:
    places = Places()
    for r in _rows(folder / "places.tsv"):
        r["aliases"] = _split(r.get("aliases", ""))
        r["roles"] = _split(r.get("roles", ""))
        places.rows[r["id"]] = r
    for pid, r in places.rows.items():
        for name in [r["name"], *r["aliases"]]:
            places.index.setdefault(_key(name), pid)
    genres = Genres()
    for r in _rows(folder / "genres.tsv"):
        r["maps_from"] = _split(r.get("maps_from", ""))
        genres.rows[r["id"]] = r
        for m in r["maps_from"]:
            src, _, label = m.partition(":")
            genres.index.setdefault((src.strip(), _key(label)), r["id"])
        if r.get("wikidata"):
            genres.by_qid.setdefault((r["kind"], r["wikidata"]), r["id"])
    waves = {}
    for r in _rows(folder / "waves.tsv"):
        r["start"], r["end"] = _year(r.get("start", "")), _year(r.get("end", ""))
        r["sources"] = _split(r.get("sources", ""))
        waves[r["id"]] = r
    wave_films = _rows(folder / "wave_films.tsv") if (folder / "wave_films.tsv").exists() else []
    atlas = Atlas(places, genres, waves, wave_films)
    for r in _rows(folder / "studios.tsv") if (folder / "studios.tsv").exists() else []:
        r["aliases"] = _split(r.get("aliases", ""))
        r["sources"] = _split(r.get("sources", ""))
        atlas.studios[r["id"]] = r
        for name in [r["name"], *r["aliases"]]:
            atlas.studio_index.setdefault(_key(name), r["id"])
    return atlas


def parse_rule(rule: str) -> list[list[tuple[str, str]]]:
    """'curated; year + genre=musical' -> [[('curated','')], [('year',''), ('genre','musical')]]."""
    alts = []
    for alt in _split(rule, ";"):
        terms = []
        for t in alt.split(" + "):
            name, _, value = t.strip().partition("=")
            terms.append((name.strip(), value.strip()))
        alts.append(terms)
    return alts


def _tree_problems(rows: dict[str, dict[str, Any]], what: str) -> list[str]:
    out = []
    for rid, r in rows.items():
        seen, cur = {rid}, r.get("parent")
        while cur:
            if cur not in rows:
                out.append(f"{what} {rid}: parent {cur} is not in the table")
                break
            if cur in seen:
                out.append(f"{what} {rid}: parent chain loops")
                break
            seen.add(cur)
            cur = rows[cur].get("parent")
    return out


def check(atlas: Atlas, folder: Path = DEFAULT_DIR) -> list[str]:
    """Every problem in the tables; [] when they are sound."""
    out: list[str] = []
    seen_alias: dict[str, str] = {}
    for sid, r in atlas.studios.items():
        if r.get("place") and r["place"] not in atlas.places.rows:
            out.append(f"studio {sid}: place {r['place']} is not in places.tsv")
        if r.get("wikidata") and not re.fullmatch(r"Q\d+", r["wikidata"]):
            out.append(f"studio {sid}: wikidata {r['wikidata']!r} is not a QID")
        if not any(u.startswith("https://") for u in r["sources"]):
            out.append(f"studio {sid}: no https source")
        for name in [r["name"], *r["aliases"]]:
            other = seen_alias.setdefault(_key(name), sid)
            if other != sid:
                out.append(f"studio name {name!r} names both {other} and {sid}")
    for name in ("places", "genres", "waves"):
        ids = [r["id"] for r in _rows(folder / f"{name}.tsv")]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            out.append(f"{name}: duplicate ids {', '.join(dup)}")
        for r in _rows(folder / f"{name}.tsv"):
            if "_extra" in r:
                out.append(f"{name} line {r['_line']}: more cells than the header")
            if not r["id"] or not r.get("name"):
                out.append(f"{name} line {r['_line']}: id and name are required")
    P = atlas.places.rows
    out += _tree_problems(P, "place")
    for pid, r in P.items():
        if r["kind"] not in PLACE_KINDS:
            out.append(f"place {pid}: kind {r['kind']!r} is not one of {sorted(PLACE_KINDS)}")
        if r["kind"] != "world" and not r["parent"]:
            out.append(f"place {pid}: needs a parent")
        if r["wikidata"] and not QID.match(r["wikidata"]):
            out.append(f"place {pid}: wikidata {r['wikidata']!r} is not a QID")
        if r["kind"] in {"city", "country", "state"} and not (r["lat"] and r["lon"]):
            out.append(f"place {pid}: {r['kind']} needs coordinates")
        for k in ("lat", "lon"):
            if r[k]:
                try:
                    v = float(r[k])
                    if abs(v) > (90 if k == "lat" else 180):
                        out.append(f"place {pid}: {k} out of range")
                except ValueError:
                    out.append(f"place {pid}: {k} {r[k]!r} is not a number")
        bad = set(r["roles"]) - PLACE_ROLES
        if bad:
            out.append(f"place {pid}: unknown roles {sorted(bad)}")
        if r["kind"] == "country" and not re.fullmatch(r"[a-z]{3}", pid):
            out.append(f"place {pid}: country ids are ISO 3166 alpha-3 in lower case")
    names: dict[str, str] = {}
    for pid, r in P.items():
        for n in [r["name"], *r["aliases"]]:
            k = _key(n)
            if k in names and names[k] != pid:
                out.append(f"place name {n!r} is used by both {names[k]} and {pid}")
            names.setdefault(k, pid)
    G = atlas.genres.rows
    out += _tree_problems(G, "genre")
    for gid, r in G.items():
        if r["kind"] not in GENRE_KINDS:
            out.append(f"genre {gid}: kind {r['kind']!r} is not one of {sorted(GENRE_KINDS)}")
        if r["parent"] and r["parent"] in G and G[r["parent"]]["kind"] != r["kind"]:
            out.append(f"genre {gid}: its parent {r['parent']} is of another kind")
        if r["wikidata"] and not QID.match(r["wikidata"]):
            out.append(f"genre {gid}: wikidata {r['wikidata']!r} is not a QID")
        for m in r["maps_from"]:
            if ":" not in m:
                out.append(f"genre {gid}: maps_from {m!r} needs source:label")
    seen_map: dict[tuple[str, str], str] = {}
    for gid, r in G.items():
        for m in r["maps_from"]:
            src, _, label = m.partition(":")
            k = (src.strip(), _key(label))
            if k in seen_map and seen_map[k] != gid:
                out.append(f"genre label {m!r} maps to both {seen_map[k]} and {gid}")
            seen_map.setdefault(k, gid)
    W = atlas.waves
    for wid, r in W.items():
        if r["kind"] not in WAVE_KINDS:
            out.append(f"wave {wid}: kind {r['kind']!r} is not one of {sorted(WAVE_KINDS)}")
        if r["start"] is None:
            out.append(f"wave {wid}: start year is required")
        elif r["end"] is not None and r["end"] < r["start"]:
            out.append(f"wave {wid}: ends before it starts")
        if not r["sources"]:
            out.append(f"wave {wid}: every wave needs at least one source")
        for s in r["sources"]:
            if not s.startswith("https://"):
                out.append(f"wave {wid}: source {s!r} is not an https URL")
        if not r.get("definition"):
            out.append(f"wave {wid}: needs a definition")
        if r["wikidata"] and not QID.match(r["wikidata"]):
            out.append(f"wave {wid}: wikidata {r['wikidata']!r} is not a QID")
        if not r.get("rule"):
            out.append(f"wave {wid}: needs a rule")
        for alt in parse_rule(r.get("rule", "")):
            for name, value in alt:
                base = name.split(":")[0]
                if base not in RULE_TERMS:
                    out.append(f"wave {wid}: unknown rule term {name!r}")
                if base == "genre" and value and f"film-{value}" not in G:
                    out.append(f"wave {wid}: rule genre {value!r} is not a film genre (film-{value})")
    eras: dict[str, list[dict[str, Any]]] = {}
    for r in W.values():
        if r["kind"] == "era" and r["start"] is not None:
            eras.setdefault(r["industry"], []).append(r)
    for ind, rs in eras.items():
        rs.sort(key=lambda r: r["start"])
        for a, b in zip(rs, rs[1:]):
            if a["end"] is None or a["end"] >= b["start"]:
                out.append(f"waves: eras {a['id']} and {b['id']} of {ind} overlap")
            elif b["start"] > a["end"] + 1:
                out.append(f"waves: gap between eras {a['id']} and {b['id']} of {ind}")
    return out


def coverage(atlas: Atlas, register: dict[str, Any] | None = None,
             canons: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """How much of a register snapshot (films' banners and subjects) and canon snapshots (song
    categories) the tables cover, with the labels left over, most frequent first."""
    rep: dict[str, Any] = {}
    if register:
        films = register.get("films", [])
        placed = 0
        unplaced: dict[str, int] = {}
        subj_hit = subj_tot = 0
        unmapped: dict[str, int] = {}
        for f in films:
            ids = [p for b in (f.get("banner") or []) for p in atlas.places.find(b)]
            if ids:
                placed += 1
            else:
                for b in f.get("banner") or []:
                    tail = b.rsplit(",", 1)[-1].strip() if "," in b else ""
                    if tail:
                        unplaced[tail] = unplaced.get(tail, 0) + 1
            for g in f.get("genre") or []:
                subj_tot += 1
                if atlas.genres.lookup("hfgk", g):
                    subj_hit += 1
                else:
                    unmapped[g] = unmapped.get(g, 0) + 1
        rep["register"] = {
            "films": len(films), "with_banner": sum(1 for f in films if f.get("banner")), "placed": placed,
            "unplaced_labels": sorted(unplaced.items(), key=lambda x: -x[1])[:40],
            "subjects": subj_tot, "subjects_mapped": subj_hit,
            "unmapped_subjects": sorted(unmapped.items(), key=lambda x: -x[1])[:40],
            "eras": _era_counts(atlas, films, "bollywood"),
        }
    if canons:
        hit = tot = 0
        left: dict[str, int] = {}
        for snap in canons:
            for u in snap.get("units", []):
                for c in u.get("category") or []:
                    tot += 1
                    if atlas.genres.lookup("hindigeetmala", c):
                        hit += 1
                    else:
                        left[c] = left.get(c, 0) + 1
        rep["categories"] = {"labels": tot, "mapped": hit,
                             "unmapped": sorted(left.items(), key=lambda x: -x[1])}
    return rep


def _era_counts(atlas: Atlas, films: list[dict[str, Any]], industry: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in films:
        for w in atlas.waves_for(industry, f.get("year")):
            out[w] = out.get(w, 0) + 1
    return out


# ---- tagging films -------------------------------------------------------------

STREAMING = {"netflix", "amazonprimevideo", "primevideo", "amazonstudios", "disney+", "disneyplus", "disney+hotstar",
             "hotstar", "jiohotstar", "zee5", "jiocinema", "sonyliv", "appletv+", "appletvplus", "hbomax", "max",
             "hulu", "paramount+", "peacock", "mxplayer", "voot", "altbalaji", "erosnow"}


def _skey(s: str) -> str:
    return re.sub(r"[^a-z0-9+]", "", fold(s))


def _title_key(title: str, year: Any) -> str:
    return f"{_key(title)}|{year or ''}"


def _film_facts(f: dict[str, Any]) -> dict[str, Any]:
    """What tagging reads from a register film or a canon container, whichever spelling it uses."""
    def qids(v):
        return {x.get("qid") for x in (v or []) if isinstance(x, dict) and x.get("qid")}
    return {
        "title": f.get("title") or "", "year": f.get("year"), "wikidata": f.get("wikidata"),
        "banner": f.get("banner") or [], "subject": f.get("genre") or f.get("subject") or [],
        "country": f.get("country") or [], "wd_genre": qids(f.get("wd_genre")), "movement": qids(f.get("movement")),
        "channels": [*(f.get("broadcaster") or []), *(f.get("dist") or [])],
        "prod": [*(f.get("prod") or []), *(f.get("banner") or [])], "released": f.get("released") or "",
    }


def tag_film(atlas: Atlas, f: dict[str, Any], industry: str) -> dict[str, Any]:
    """Place, film genres and waves for one film, each with where it came from:
    {"place": [{"id", "src"}], "genre": [{"id", "src"}], "wave": [{"id", "by"}]}.
    `src` names the field read (banner, country, subject, wikidata); `by` the rule that placed the film
    (year, a Wikidata statement, genre, release, a reviewed list). Nothing here is a guess."""
    x = _film_facts(f)
    places: list[dict[str, str]] = []
    for b in x["banner"]:
        for pid in atlas.places.find(b):
            if all(p["id"] != pid for p in places):
                places.append({"id": pid, "src": "banner"})
    if not places:
        for name in x["prod"]:
            sid = atlas.studio(name)
            pid = atlas.studios[sid].get("place") if sid else None
            if pid and all(p["id"] != pid for p in places):
                places.append({"id": pid, "src": "studio"})
    if not places:
        for c in x["country"]:
            pid = atlas.places.index.get(_key(c))
            if pid and atlas.places.rows[pid]["kind"] == "country" and all(p["id"] != pid for p in places):
                places.append({"id": pid, "src": "country"})
    genres: list[dict[str, str]] = []
    for label in x["subject"]:
        for gid in atlas.genres.lookup_all("hfgk", label) or atlas.genres.lookup_all("wikipedia", label):
            if all(g["id"] != gid for g in genres):
                genres.append({"id": gid, "src": "subject"})
    for q in sorted(x["wd_genre"]):
        gid = atlas.genres.by_qid.get(("film", q))
        if gid and all(g["id"] != gid for g in genres):
            genres.append({"id": gid, "src": "wikidata"})
    gids = {g["id"] for g in genres}
    year = x["year"] if isinstance(x["year"], int) else None
    listed = atlas.listed()
    waves: list[dict[str, str]] = []
    for w in atlas.waves.values():
        if w["industry"] != industry or year is None:
            continue
        if not (w["start"] <= year <= (w["end"] or 9999)):
            continue
        by = None
        for alt in parse_rule(w["rule"]):
            ok, why = True, []
            for name, value in alt:
                base, _, prop = name.partition(":")
                if base == "year":
                    why.append("year")
                elif base == "curated":
                    have = listed.get(w["id"], set())
                    ok = _title_key(x["title"], year) in have or bool(x["wikidata"]) and x["wikidata"] in have
                    why.append("reviewed list")
                elif base == "wikidata":
                    p, _, q = value.partition("=") if not prop else (prop, "", value)
                    have = x["movement"] if p == "P135" else x["wd_genre"] if p == "P136" else set()
                    ok = q in have
                    why.append(f"wikidata {p}")
                elif base == "genre":
                    ok = f"film-{value}" in gids
                    why.append(f"genre {value}")
                elif base == "release":
                    ok = value == "streaming" and any(_skey(c) in STREAMING for c in x["channels"])
                    why.append("released on streaming")
                elif base == "released_before":
                    ok = bool(x["released"]) and x["released"] < value
                    why.append(f"released before {value}")
                elif base == "studio" and atlas.studio(value):
                    ok = any(atlas.studio(p) == atlas.studio(value) for p in x["prod"])
                elif base in ("producer", "studio"):
                    ok = any(_key(value) in _key(p) for p in x["prod"])
                    why.append(f"{base} {value}")
                elif base == "films":
                    names = {_title_key(t.rsplit(" ", 1)[0], t.rsplit(" ", 1)[-1]) for t in value.split(",")}
                    ok = _title_key(x["title"], year) in names
                    why.append("named in the source")
                else:
                    ok = False
                if not ok:
                    break
            if ok:
                by = " + ".join(why)
                break
        if by:
            waves.append({"id": w["id"], "by": by})
    return {"place": places, "genre": genres, "wave": waves}


def tag_register(atlas: Atlas, snap: dict[str, Any], industry: str) -> dict[str, Any]:
    """Tag every film of a register snapshot in place (`atlas` on each film) and return counts."""
    counts: dict[str, dict[str, int]] = {"place": {}, "genre": {}, "wave": {}}
    untagged = {"place": 0, "genre": 0}
    for f in snap.get("films", []):
        t = tag_film(atlas, f, industry)
        f["atlas"] = t
        for k in ("place", "genre", "wave"):
            for e in t[k]:
                counts[k][e["id"]] = counts[k].get(e["id"], 0) + 1
        for k in untagged:
            untagged[k] += not t[k]
    snap["atlas"] = {"industry": industry, "counts": counts, "untagged": untagged}
    return snap["atlas"]


def tag_canon(atlas: Atlas, snap: dict[str, Any], industry: str) -> dict[str, Any]:
    """Tag a canon snapshot's containers (its films), so each song reaches place and wave through its film."""
    counts: dict[str, dict[str, int]] = {"place": {}, "genre": {}, "wave": {}}
    for c in (snap.get("containers") or {}).values():
        t = tag_film(atlas, c, industry)
        c["atlas"] = t
        for k in counts:
            for e in t[k]:
                counts[k][e["id"]] = counts[k].get(e["id"], 0) + 1
    snap["atlas"] = {"industry": industry, "counts": counts}
    return snap["atlas"]


def review_json(atlas: Atlas) -> dict[str, Any]:
    """Everything the R1 review page shows, as plain JSON."""
    def clean(r: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in r.items() if not k.startswith("_")}
    return {
        "places": [clean(r) for r in atlas.places.rows.values()],
        "genres": [clean(r) for r in atlas.genres.rows.values()],
        "waves": [clean(r) for r in atlas.waves.values()],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="maestro-atlas", description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["check", "coverage", "review-json", "tag"])
    ap.add_argument("--industry", default="bollywood", help="industry whose waves apply (tag)")
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="folder holding places.tsv, genres.tsv, waves.tsv")
    ap.add_argument("--register", type=Path, help="a register snapshot (canon/films/<slug>.json) for coverage")
    ap.add_argument("--canon", type=Path, action="append", default=[], help="canon snapshots for coverage")
    ap.add_argument("--out", type=Path, help="write JSON here instead of stdout")
    a = ap.parse_args(argv)
    atlas = load(a.dir)
    if a.command == "check":
        problems = check(atlas, a.dir)
        for p in problems:
            print(p)
        print(f"{len(atlas.places.rows)} places, {len(atlas.genres.rows)} genres, {len(atlas.waves)} waves: "
              f"{len(problems)} problems", file=sys.stderr)
        return 1 if problems else 0
    if a.command == "tag":
        out = {}
        for path, fn in [*([(a.register, tag_register)] if a.register else []), *((p, tag_canon) for p in a.canon)]:
            snap = json.loads(path.read_text(encoding="utf-8"))
            out[str(path)] = fn(atlas, snap, a.industry)
            path.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    if a.command == "coverage":
        reg = json.loads(a.register.read_text(encoding="utf-8")) if a.register else None
        canons = [json.loads(p.read_text(encoding="utf-8")) for p in a.canon]
        data: Any = coverage(atlas, reg, canons)
    else:
        data = review_json(atlas)
    text = json.dumps(data, ensure_ascii=False, indent=1)
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
