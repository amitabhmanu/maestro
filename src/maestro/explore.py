"""Canon explorer (design doc, the canon finder): a snapshot of one maestro's canon for browsing.

`snapshot(store)` reads the finished canon (units after merges, with every credit, title variant,
container and source link) plus the completeness report, and returns one JSON-ready dict. The
explorer page (`explorer/index.html`) loads one snapshot per maestro and does all searching and
filtering in the browser, so a snapshot outlives the database it came from: canon databases live on
throwaway machines, a snapshot file can be kept, served or published.

Private claims stay out unless `include_private` is set, so a default snapshot is safe to ship.

`maestro-explore` writes the explorer site (page plus one snapshot per maestro) and can serve it.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from maestro.completeness import completeness_report, load_units
from maestro.lyrics import Policy as LyricsPolicy
from maestro.lyrics import beyond_first_line, first_line
from maestro.popularity import resolve_reasons, score_canon, unit_summary
from maestro.pack import load_pack
from maestro.resolve import Groups
from maestro.store import Store

FORMAT = 1

# Unit facts copied into the snapshot as lists of distinct values (claims on every merged record count).
UNIT_TEXT_FIELDS = {
    "title_hi": "title_alt_script", "language": "language", "category": "category", "catalogue": "catalogue",
    "matrix": "matrix", "label": "label", "description": "description", "artist_credit": "artist_credit",
    "work_type": "work_type", "work_kind": "kind", "date": "date", "issues": "issues", "full_title": "full_title",
    "recorded_year": "recorded_year", "release_year": "release_year", "primary_performer": "performer",
    "raga": "raga",
}
CONTAINER_TEXT_FIELDS = {
    "title_hi": "title_alt_script", "language": "language", "container_kind": "kind", "date": "date",
    "session_date": "date", "place": "place", "banner": "banner", "record_company": "label", "subject": "subject",
    "personnel": "personnel", "note": "note",
}


def _xref_link(source_id: str, value: str) -> str | None:
    """A browsable page for an external id, for the sources the core knows."""
    if value.startswith(("http://", "https://")):
        return value
    if source_id == "musicbrainz":
        m = re.fullmatch(r"(?:(work|release-group|release|recording):)?([0-9a-f-]{36})", value)
        if m:
            return f"https://musicbrainz.org/{m.group(1) or 'recording'}/{m.group(2)}"
    if source_id == "wikidata":
        m = re.search(r"(Q\d+)", value)
        if m:
            return f"https://www.wikidata.org/wiki/{m.group(1)}"
    if source_id == "discogs":
        m = re.match(r"(master|release|artist):(\d+)", value)
        if m:
            return f"https://www.discogs.com/{m.group(1)}/{m.group(2)}"
    if source_id == "bnf":
        m = re.search(r"(cb\w+)", value)
        if m:
            return f"https://data.bnf.fr/ark:/12148/{m.group(1)}"
    return None


def _page_link(url: str | None) -> str | None:
    """The source page a claim was read from, unless it is an API endpoint nobody would browse."""
    if not url or "/ws/2/" in url or "api.discogs.com" in url or "query.wikidata.org" in url or "/sparql" in url:
        return None
    return url


# ---- where to watch, listen and read ---------------------------------------------------
# Outbound links only: the explorer never copies lyrics or hosts audio (pack legal.lyrics, legal.audio).
# A host counts only for pages about one song (a track, a video, a lyrics page), not for lists or charts.
MEDIA_HOSTS: list[tuple[str, str, str, str]] = [  # (host, path pattern, kind, site)
    ("youtube.com", r"^/(?:watch|shorts/)", "watch", "YouTube"),
    ("youtu.be", r"^/[\w-]{6,}", "watch", "YouTube"),
    ("last.fm", r"^/(?:\w\w/)?music/[^/]+/(?:[^/]+/)?_/[^/]+", "listen", "Last.fm"),
    ("open.spotify.com", r"^/track/", "listen", "Spotify"),
    ("music.apple.com", r"/(?:song|album)/", "listen", "Apple Music"),
    ("jiosaavn.com", r"^/song/", "listen", "JioSaavn"),
    ("gaana.com", r"^/song/", "listen", "Gaana"),
    ("archive.org", r"^/details/", "listen", "Internet Archive"),
    ("loc.gov", r"^/item/jukebox", "listen", "National Jukebox"),
    ("genius.com", r"-lyrics$", "lyrics", "Genius"),
    ("musixmatch.com", r"^/lyrics/", "lyrics", "Musixmatch"),
    ("hindigeetmala.net", r"^/song/", "lyrics", "HindiGeetMala"),
    ("lyricstranslate.com", r"lyrics", "lyrics", "LyricsTranslate"),
]
MEDIA_KINDS = ("watch", "listen", "lyrics")
DEFAULT_LYRICS_SITES = ["genius.com", "musixmatch.com"]


def media_kind(url: str, source: str | None = None, sources: dict[str, str] | None = None) -> tuple[str, str] | None:
    """(kind, site) when `url` is a page to watch, listen to or read one unit, else None."""
    if source and sources and source in sources:
        return sources[source], source
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    for h, path, kind, site in MEDIA_HOSTS:
        if (host == h or host.endswith("." + h)) and re.search(path, parts.path):
            return kind, site
    return None


def media_search(pack_data: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Search links the explorer fills in per unit (`{q}`: title, container, maestro) where no page is known."""
    cfg = pack_data.get("media_links") or {}
    out = {"watch": [{"site": "YouTube", "url": "https://www.youtube.com/results?search_query={q}"}],
           "listen": [{"site": "Last.fm", "url": "https://www.last.fm/search/tracks?q={q}"}]}
    sites = cfg.get("lyrics_sites", DEFAULT_LYRICS_SITES)
    if sites:
        out["lyrics"] = [{"site": ", ".join(sites), "url": "https://www.google.com/search?q={q}+lyrics+"
                          + "+OR+".join(f"site%3A{s}" for s in sites)}]
    return out


def add_media(snap: dict[str, Any], pack_data: dict[str, Any]) -> dict[str, Any]:
    """Give each unit of a snapshot its `media` (where to watch, listen, read) and the snapshot its searches.

    Reads only the snapshot (source links and popularity evidence, strongest first), so it also
    refreshes snapshots whose database is gone. One link per site, the best first."""
    sources = (pack_data.get("media_links") or {}).get("sources") or {}
    names = {}
    for unit in snap["units"]:
        found: dict[tuple[str, str], dict[str, str]] = {}
        cands = [(x["source"], x["url"], None) for x in unit.get("links", [])]
        cands += [(e["source"], e["url"], e.get("note")) for e in (unit.get("pop") or {}).get("evidence", []) if e.get("url")]
        for src, url, note in cands:
            km = media_kind(url, src, sources)
            if not km or km in found:
                continue
            kind, site = km
            if site in sources:  # a source id: show its name as the pack gives it
                site = names.setdefault(site, _site_name(snap, site))
            found[km] = {"kind": kind, "site": site, "url": url, **({"note": note} if note else {})}
        media = sorted(found.values(), key=lambda m: MEDIA_KINDS.index(m["kind"]))
        if media:
            unit["media"] = media
        else:
            unit.pop("media", None)
    snap["media_search"] = media_search(pack_data)
    return snap


def _site_name(snap: dict[str, Any], source: str) -> str:
    url = (snap.get("sources", {}).get(source) or {}).get("url")
    host = urlsplit(url).hostname if url else None
    return re.sub(r"^www\.", "", host) if host else source


def _add(d: dict[str, list], key: str, value: Any) -> None:
    if value in (None, "", [], {}):
        return
    lst = d.setdefault(key, [])
    if value not in lst:
        lst.append(value)


def snapshot(store: Store, include_private: bool = False) -> dict[str, Any]:
    pack = store.pack
    groups = Groups(store)
    units = load_units(store, groups)
    report = completeness_report(store)
    maestro = groups.find(store.maestro_entity())
    vis = ("publishable", "private") if include_private else ("publishable",)
    lyrics = LyricsPolicy(pack.data)
    lyric_titles = "title" in lyrics.fields
    sources = {r["id"]: r for r in store.conn.execute("SELECT * FROM source").fetchall()}

    unit_type = pack.unit
    members: dict[int, int] = {}
    for r in store.conn.execute("SELECT id FROM entity WHERE type = %s", (unit_type,)).fetchall():
        root = groups.find(r["id"])
        if root in units:
            members[r["id"]] = root

    rows = store.conn.execute(
        """SELECT c.entity_id, c.field, c.value, c.ref_entity_id, c.source_id, c.visibility, p.url AS page
           FROM claim c LEFT JOIN raw_page p ON p.id = c.raw_page_id
           WHERE c.entity_id = ANY(%s) AND c.status = 'active' AND c.visibility = ANY(%s)
           ORDER BY c.id""",
        (list(members), list(vis)),
    ).fetchall()

    facts: dict[int, dict[str, Any]] = defaultdict(dict)
    visible_units: set[int] = set()
    people: set[int] = set()
    containers: set[int] = set()
    for r in rows:
        uid = members[r["entity_id"]]
        f, v, src = r["field"], r["value"], r["source_id"]
        d = facts[uid]
        visible_units.add(uid)
        if f in ("title", "xref"):
            _add(d, "sources", src)
        if f in ("title", "first_line"):
            _add(d, "titles", v)
        elif f in UNIT_TEXT_FIELDS:
            _add(d, UNIT_TEXT_FIELDS[f], v)
        elif f == "credit_unlinked" and isinstance(v, dict):
            _add(d, "credits_unlinked", {"role": v.get("role"), "name": v.get("name")})
        elif f == "on_screen" and r["ref_entity_id"]:
            p = groups.find(r["ref_entity_id"])
            people.add(p)
            _add(d, "on_screen", p)
        elif f == "part_of" and r["ref_entity_id"]:
            c = groups.find(r["ref_entity_id"])
            containers.add(c)
            _add(d, "containers", c)
            if isinstance(v, dict) and v.get("position") is not None and d.get("position") is None:
                d["position"] = v["position"]
        elif f == "xref" and isinstance(v, str):
            link = _xref_link(src, v)
            if link:
                _add(d, "links", {"source": src, "url": link})
        elif f == "dahr_url" and isinstance(v, str):
            _add(d, "links", {"source": src, "url": "https://adp.library.ucsb.edu" + v if v.startswith("/") else v})
        elif f == "film" and isinstance(v, str):
            _add(d, "film_ids", v)
        if f in ("title", "credit", "part_of"):
            link = _page_link(r["page"])
            if link:
                d.setdefault("_pages", {}).setdefault(src, link)

    for d in facts.values():  # a source's page only where no direct link to the record exists
        for src, link in d.pop("_pages", {}).items():
            if not any(x["source"] == src for x in d.get("links", [])):
                _add(d, "links", {"source": src, "url": link})

    out_units = []
    for uid, u in units.items():
        if uid not in visible_units:
            continue
        d = facts[uid]
        credits = {}
        for role, ids in u.credits.items():
            if role:
                credits[role] = sorted(ids)
                people.update(ids)
        title = u.label
        titles = d.pop("titles", [])
        if not include_private and lyric_titles:
            # Publishable snapshots carry only first lines of lyric titles (pack legal.lyrics).
            title = first_line(title, lyrics.max_chars) if beyond_first_line(title, lyrics.max_chars) else title
            titles = list(dict.fromkeys(first_line(t, lyrics.max_chars) if beyond_first_line(t, lyrics.max_chars)
                                        else t for t in titles))
        alt = [t for t in titles if t != title]
        if u.container is not None:
            containers.add(u.container)
        unit = {
            "id": uid, "title": title, "tier": u.tier, "year": u.year, "container": u.container,
            "credits": credits, "lineages": sorted(u.lineages), "sources": d.pop("sources", []),
        }
        if alt:
            unit["alt_titles"] = alt
        if u.live:
            unit["live"] = True
        if u.pending_match:
            unit["pending_match"] = True
        if u.tier_inherited or u.tier_default:
            unit["tier_basis"] = "container" if u.tier_inherited else "default"
        d.pop("containers", None)
        unit.update(d)
        out_units.append(unit)
    out_units.sort(key=lambda x: (x["year"] or 9999, x["title"].lower()))

    # Popularity and gems (design doc: popularity and gem scoring).
    pop = score_canon(store, include_private=include_private)
    names = {r["id"]: r["label"] for r in store.conn.execute(
        "SELECT id, label FROM entity WHERE type IN ('person', %s)", (unit_type,)).fetchall()}
    for unit in out_units:
        sc = pop["units"].get(unit["id"])
        summary = unit_summary(sc) if sc else None
        if summary:
            for k in ("gem", "lead"):
                if k in summary:
                    summary[k]["reasons"] = resolve_reasons(summary[k]["reasons"], names, names)
            unit["pop"] = summary
    popularity = {k: pop[k] for k in ("signals", "facets_present", "gems", "listed_without_data",
                                       "unmatched_mentions")}

    # Containers: title, year and facts from every merged record.
    cmembers: dict[int, int] = {}
    for r in store.conn.execute("SELECT id FROM entity WHERE type = 'container'").fetchall():
        root = groups.find(r["id"])
        if root in containers:
            cmembers[r["id"]] = root
    cfacts: dict[int, dict[str, Any]] = defaultdict(dict)
    for r in store.conn.execute(
        """SELECT c.entity_id, c.field, c.value, c.ref_entity_id, c.source_id, p.url AS page FROM claim c
           LEFT JOIN raw_page p ON p.id = c.raw_page_id
           WHERE c.entity_id = ANY(%s) AND c.status = 'active' AND c.visibility = ANY(%s) ORDER BY c.id""",
        (list(cmembers), list(vis)),
    ).fetchall():
        cid = cmembers[r["entity_id"]]
        f, v, d = r["field"], r["value"], cfacts[cid]
        if f == "title":
            _add(d, "titles", v)
        elif f == "year" and isinstance(v, int):
            d.setdefault("year", v)
        elif f in CONTAINER_TEXT_FIELDS:
            _add(d, CONTAINER_TEXT_FIELDS[f], v)
        elif f == f"{unit_type}_count" and isinstance(v, int):
            d["listed"] = max(d.get("listed", 0), v)
        elif f == "credit" and r["ref_entity_id"] and isinstance(v, dict) and v.get("role"):
            p = groups.find(r["ref_entity_id"])
            if p != maestro:
                people.add(p)
                d.setdefault("credits", {}).setdefault(v["role"], [])
                if p not in d["credits"][v["role"]]:
                    d["credits"][v["role"]].append(p)
        elif f == "xref" and isinstance(v, str):
            link = _xref_link(r["source_id"], v)
            if link:
                _add(d, "links", {"source": r["source_id"], "url": link})
        if f == "title":
            link = _page_link(r["page"])
            if link:
                d.setdefault("_pages", {}).setdefault(r["source_id"], link)
    for d in cfacts.values():
        for src, link in d.pop("_pages", {}).items():
            if not any(x["source"] == src for x in d.get("links", [])):
                _add(d, "links", {"source": src, "url": link})
    labels = {r["id"]: r["label"] for r in store.conn.execute(
        "SELECT id, label FROM entity WHERE id = ANY(%s)", (list(containers | people),)).fetchall()}
    out_containers = {}
    for cid in sorted(containers):
        d = cfacts.get(cid, {})
        titles = d.pop("titles", [])
        title = labels.get(cid) or (titles[0] if titles else str(cid))
        alt = [t for t in titles if t != title]
        out_containers[str(cid)] = {"title": title, **({"alt_titles": alt} if alt else {}), **d}

    # People: the label of the merge root, plus other spellings from merged records.
    pmembers: dict[int, list[str]] = defaultdict(list)
    for r in store.conn.execute("SELECT id, label FROM entity WHERE type = 'person'").fetchall():
        root = groups.find(r["id"])
        if root in people and r["label"] != labels.get(root) and r["label"] not in pmembers[root]:
            pmembers[root].append(r["label"])
    out_people = {str(p): {"name": labels.get(p, str(p)), **({"aliases": pmembers[p]} if pmembers.get(p) else {})}
                  for p in sorted(people)}

    ident = pack.data["identity"]
    snap = {
        "format": FORMAT,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "includes_private": include_private,
        "maestro": {
            "slug": pack.slug, "name": ident["name"], "variants": ident.get("variants", []),
            "ids": ident.get("ids", {}), "active": ident.get("active"), "person_id": maestro,
            "roles": pack.data["roles"], "unit": unit_type, "container": pack.container,
        },
        "tiers": {k: {"label": v["label"], "target_missing": v["target_missing"]} for k, v in pack.tiers.items()},
        "sources": {sid: {"kind": s["kind"], "lineage": s["lineage"], "reliability": s["reliability"],
                          **({"url": pack.source(sid)["url"]} if sid in {x["id"] for x in pack.data["sources"]}
                             and pack.source(sid).get("url") else {})}
                    for sid, s in sources.items()},
        "units": out_units,
        "containers": out_containers,
        "people": out_people,
        "coverage": report,
        "popularity": popularity,
    }
    return add_media(snap, pack.data)


# ---- CLI ---------------------------------------------------------------------------

def _page_source() -> str:
    return resources.files("maestro").joinpath("explorer/index.html").read_text("utf-8")


def write_site(out: Path, snaps: list[dict[str, Any]]) -> list[Path]:
    """Write index.html, canon/<slug>.json per snapshot and canon/index.json (merged with earlier runs)."""
    (out / "canon").mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(_page_source(), "utf-8")
    written = [out / "index.html"]
    idx_path = out / "canon" / "index.json"
    try:
        index = json.loads(idx_path.read_text("utf-8"))
    except (OSError, ValueError):
        index = {"maestros": []}
    for snap in snaps:
        m = snap["maestro"]
        path = out / "canon" / f"{m['slug']}.json"
        path.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":"), default=str), "utf-8")
        written.append(path)
        index["maestros"] = [x for x in index["maestros"] if x["slug"] != m["slug"]] + [{
            "slug": m["slug"], "name": m["name"], "unit": m["unit"], "units": len(snap["units"]),
            "file": f"canon/{m['slug']}.json", "generated_at": snap["generated_at"],
        }]
    index["maestros"].sort(key=lambda x: x["name"])
    idx_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), "utf-8")
    written.append(idx_path)
    return written


def serve(out: Path, port: int) -> None:
    class Handler(http.server.SimpleHTTPRequestHandler):
        extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                          ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8"}

    handler = functools.partial(Handler, directory=str(out))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"canon explorer at http://127.0.0.1:{port}/  (Ctrl-C to stop)")
        httpd.serve_forever()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="maestro-explore",
        description="Write the canon explorer (page plus one snapshot per maestro) and optionally serve it.")
    p.add_argument("--maestro", action="append", default=[], metavar="PACK[=DSN]",
                   help="a maestro folder and its store, e.g. maestros/rafi=postgresql://... (repeatable; without "
                        "=DSN, $MAESTRO_DSN_<SLUG> from the environment or .env); "
                        "with --pack/--dsn or $MAESTRO_PACK/$MAESTRO_DSN for a single maestro")
    p.add_argument("--pack", default=os.environ.get("MAESTRO_PACK"))
    p.add_argument("--dsn", help="default $MAESTRO_DSN_<SLUG>, then $MAESTRO_DSN")
    p.add_argument("--out", type=Path, default=Path("data/explorer"), help="site folder (default data/explorer)")
    p.add_argument("--include-private", action="store_true", help="also include private claims (never publish these)")
    p.add_argument("--relink", action="store_true",
                   help="refresh the watch, listen and lyrics links of the snapshots already in --out from their "
                        "packs (maestros/<slug>), without a database")
    p.add_argument("--serve", type=int, nargs="?", const=8765, metavar="PORT", help="serve the site on localhost")
    args = p.parse_args(argv)
    from maestro import config

    pairs = [m.split("=", 1) if "=" in m else [m, None] for m in args.maestro]
    if not pairs and args.pack:
        pairs = [[args.pack, args.dsn]]
    snaps = []
    for pack_dir, dsn in pairs:
        pack = load_pack(pack_dir)
        dsn = config.dsn_for(pack.slug, dsn)
        if not dsn:
            p.error(f"no store for {pack.slug}: give {pack_dir}=DSN or set {config.dsn_env_name(pack.slug)}")
        store = Store.connect(dsn, pack)
        try:
            snaps.append(snapshot(store, include_private=args.include_private))
        finally:
            store.close()
        print(f"{snaps[-1]['maestro']['name']}: {len(snaps[-1]['units'])} {snaps[-1]['maestro']['unit']}s")
    if args.relink:
        for path in sorted((args.out / "canon").glob("*.json")):
            if path.name == "index.json":
                continue
            snap = json.loads(path.read_text("utf-8"))
            if any(x["maestro"]["slug"] == snap["maestro"]["slug"] for x in snaps):
                continue
            pack = load_pack(Path("maestros") / snap["maestro"]["slug"])
            add_media(snap, pack.data)
            path.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":"), default=str), "utf-8")
            n = sum(1 for u in snap["units"] if u.get("media"))
            print(f"{snap['maestro']['name']}: links for {n} of {len(snap['units'])} {snap['maestro']['unit']}s")
    if not snaps and not (args.out / "canon" / "index.json").exists():
        p.error("give --maestro PACK=DSN (or --pack and --dsn), or --out a folder written earlier")
    if snaps or not (args.out / "index.html").exists():
        write_site(args.out, snaps)
    else:
        (args.out / "index.html").write_text(_page_source(), "utf-8")  # refresh the page only
    print(f"wrote {args.out}/")
    if args.serve:
        serve(args.out, args.serve)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
