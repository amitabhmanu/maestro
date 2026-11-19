"""hindigeetmala.net's song list for a singer: every song the site credits to them, with film, year and credits.

Seed: https://www.hindigeetmala.net/singer/<identity.ids.hindigeetmala>.php, e.g. mohammed_rafi
(robots.txt allows everything). The list
runs over pages of ten (`?page=2` ... "Page 1 of 456" in the title); the first page queues the rest.
Each song is marked up with schema.org microdata: title, singers, music director, film with its year,
lyricist, actors on screen and the site's categories (moods, and ragas such as "Raga - Pahadi").

A song becomes a unit keyed by the site's song page, in a film container keyed by its film page.
Albums named "Non-Film (...)" are not films: their songs stay uncontained with that category.
Film songs with a year are Tier A.

With the pack's `unit_mode: enrich` (Amitabh's choice, 15 Nov 2026) the site adds details only: each
song is placed in the canon's own film (same film-title key, years at most one apart; a film
the canon still holds twice is searched as one) and matched to that film's song by title. A matched song gains the site's ragas, actors on
screen and categories; nothing that counts as a sighting (title, credit, part_of, tier) is written, so
the site adds no lineage. A song the canon cannot place becomes a `listing_gap` claim on the maestro
(reason `film_not_in_canon` or `song_not_in_film`), the list Amitabh checks by hand. Non-film songs get no tier from this source: the site lists only a
handful, and a third Tier B lineage with so few songs throws the estimate off (6,085 for 234 on a test).
"""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urljoin

SOURCE = "hindigeetmala"
EXTRACTOR = "hindigeetmala"
BASE = "https://www.hindigeetmala.net/"


def maestro_path(store) -> str:
    """The maestro's list page on the site, from the pack's `identity.ids.hindigeetmala`."""
    name = (store.pack.data["identity"].get("ids") or {}).get("hindigeetmala")
    if not name:
        raise ValueError("the pack has no identity.ids.hindigeetmala (the <name> in /singer/<name>.php)")
    return f"/singer/{name}.php"

TRACK = re.compile(r'<tr itemprop="track".*?</tr></table></td></tr>', re.S)
NON_FILM = re.compile(r"^non[- ]?film", re.I)


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", s))).strip()


def _people(chunk: str, kind: str) -> list[tuple[str, str]]:
    """(page path, name) for each link to a person page of this kind."""
    return [(path, _text(name).rstrip(",").strip()) for path, name in
            re.findall(rf'href="(/{kind}/[^"]+)"[^>]*>(?:<span[^>]*>)?([^<]+)', chunk)]


def parse(page: str) -> dict:
    """The page count and one dict per song: key, title, singers, composers, lyricists, actors,
    film (key, title, year), categories."""
    m = re.search(r"Page \d+ of (\d+)", page)
    songs = []
    for tr in TRACK.findall(page):
        head = re.search(r'<a href="/song/([^"]+)\.htm"\s+itemprop="url"><span itemprop="name">([^<]+)</span>', tr)
        if not head:
            continue
        film = re.search(r'href="/movie/([^"]+)\.htm"><span itemprop="name">([^<]+)</span>\s*(?:\((\d{4})\))?', tr)
        songs.append({
            "key": head.group(1), "title": _text(head.group(2)),
            "singers": _people(tr, "singer"), "composers": _people(tr, "music_director"),
            "lyricists": _people(tr, "lyricist"), "actors": _people(tr, "actor"),
            "film": {"key": film.group(1), "title": _text(film.group(2)),
                     "year": int(film.group(3)) if film.group(3) else None} if film else None,
            "categories": [_text(c) for c in re.findall(r'<span itemprop="genre">([^<]+)</span>', tr)],
        })
    return {"pages": int(m.group(1)) if m else 1, "songs": songs}


def _person(store, path: str, name: str) -> int:
    if path == maestro_path(store):
        eid = store.maestro_entity()
        store.upsert_claim(eid, "xref", value=path, source_id=SOURCE)
        return eid
    return store.find_or_create_by_xref("person", SOURCE, path, name)


GAP_FIELD = "listing_gap"


def _canon_index(store):
    """Per film-title key, the canon's films (merge roots) and their years; per film, its songs' titles."""
    from maestro.resolve import Groups, Normaliser, _squash_container

    cached = getattr(store, "_hgm_canon_index", None)
    if cached is not None:
        return cached
    norm, groups = Normaliser.for_store(store), Groups(store)
    films: dict[str, dict[int, set[int]]] = {}
    for r in store.conn.execute(
            """SELECT e.id, e.label, (SELECT min((c.value #>> '{}')::int) FROM claim c WHERE c.entity_id = e.id
                     AND c.field = 'year' AND c.status = 'active' AND jsonb_typeof(c.value) = 'number') AS year
               FROM entity e WHERE e.type = 'container'
                 AND NOT EXISTS (SELECT 1 FROM claim x WHERE x.entity_id = e.id AND x.field = 'xref'
                                 AND x.source_id = %s AND x.status = 'active')""", (SOURCE,)).fetchall():
        key = _squash_container(norm.container(r["label"]))
        if key:
            years = films.setdefault(key, {}).setdefault(groups.find(r["id"]), set())
            if r["year"]:
                years.add(r["year"])
    songs: dict[int, list[tuple[int, str]]] = {}
    for r in store.conn.execute(
            """SELECT e.id, e.label, p.ref_entity_id AS film FROM entity e
               JOIN claim p ON p.entity_id = e.id AND p.field = 'part_of' AND p.status = 'active'
               WHERE e.type = %s""", (store.pack.unit,)).fetchall():
        key = norm.title(r["label"])
        if key:
            songs.setdefault(groups.find(r["film"]), []).append((groups.find(r["id"]), key))
    cached = store._hgm_canon_index = (films, songs, norm, groups)
    return cached


def place(store, film: dict, title: str) -> tuple[list[int], str]:
    """The canon songs a listing names (one, or the versions of one song that tie), or none with the
    reason it could not be placed. The thresholds are resolve's own for titles in one film."""
    from maestro.resolve import (AUTO_TITLE_RATIO, CLEAR_MARGIN, CLEAR_TITLE_RATIO, TIE_MARGIN,
                                 _squash_container, title_similarity)

    films, songs, norm, _groups = _canon_index(store)
    roots = [root for root, years in films.get(_squash_container(norm.container(film["title"])), {}).items()
             if not years or not film["year"] or any(abs(y - film["year"]) <= 1 for y in years)]
    if not roots:
        return [], "film_not_in_canon"
    # A film the canon still holds twice (HFGK's and Atul's Pyaasa, not yet merged) is searched as one:
    # the song's title decides, with the same thresholds.
    key = norm.title(title)
    best: dict[int, float] = {}
    for root in roots:
        for unit, k in songs.get(root, []):
            best[unit] = max(best.get(unit, 0.0), title_similarity(key, k))
    ranked = sorted(best.items(), key=lambda x: -x[1])
    if not ranked or ranked[0][1] < CLEAR_TITLE_RATIO:
        return [], "song_not_in_film"
    top = ranked[0][1]
    tied = [u for u, sim in ranked if top - sim < TIE_MARGIN]
    if top >= AUTO_TITLE_RATIO:
        return tied, "matched"          # versions of one song ("... (Sad)") all get the details
    rest = ranked[len(tied)][1] if len(ranked) > len(tied) else 0.0
    if len(tied) == 1 and top - rest >= CLEAR_MARGIN:
        return tied, "matched"
    return [], "song_not_in_film"


def _enrich(ctx, data: dict, claim) -> None:
    from maestro.lyrics import first_line

    store, maestro = ctx.store, ctx.store.maestro_entity()
    for s in data["songs"]:
        film = s["film"]
        if film is None or NON_FILM.match(film["title"]):
            continue
        units, why = place(store, film, s["title"])
        url = urljoin(BASE, f"song/{s['key']}.htm")
        gap = None if units else {"title": first_line(s["title"]), "film": film["title"], "year": film["year"],
                                  "url": url, "reason": why}
        # A re-read (after a fix, or once the canon has the film) supersedes this listing's earlier gap
        # when the outcome changed; claims are never deleted.
        store.conn.execute(
            """UPDATE claim SET status = 'superseded' WHERE entity_id = %s AND field = %s AND source_id = %s
                 AND status = 'active' AND value ->> 'url' = %s AND value IS DISTINCT FROM %s::jsonb""",
            (maestro, GAP_FIELD, SOURCE, url, json.dumps(gap) if gap else None))
        if gap:
            claim(maestro, GAP_FIELD, gap)
            continue
        for unit in units:
            for path, name in s["actors"]:
                claim(unit, "on_screen", ref=_person(store, path, name))
            for c in s["categories"]:
                if c.lower().startswith("raga - "):
                    claim(unit, "raga", c[7:].strip())
                else:
                    claim(unit, "category", c)


def extract(ctx) -> None:
    data = parse(ctx.page.text)
    if "page=" not in ctx.page.url:
        for p in range(2, data["pages"] + 1):
            ctx.enqueue(urljoin(BASE, f"{maestro_path(ctx.store).lstrip('/')}?page={p}"), EXTRACTOR, "spine")

    store, raw = ctx.store, ctx.raw_page_id
    maestro = store.maestro_entity()

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor=EXTRACTOR, extractor_version="1", raw_page_id=raw)

    source = next((x for x in store.pack.data.get("sources", []) if x["id"] == SOURCE), {})
    if source.get("unit_mode") == "enrich":
        _enrich(ctx, data, claim)
        return

    for s in data["songs"]:
        film = s["film"]
        non_film = film is None or bool(NON_FILM.match(film["title"]))
        song = store.find_or_create_by_xref("song", SOURCE, f"song:{s['key']}", s["title"])
        claim(song, "title", s["title"])
        if non_film:
            claim(song, "category", film["title"] if film else "Non-Film")
            if film and film["year"]:
                claim(song, "year", film["year"])
        else:
            fid = store.find_or_create_by_xref("container", SOURCE, f"movie:{film['key']}", film["title"])
            claim(fid, "title", film["title"])
            if film["year"]:
                claim(fid, "year", film["year"])
            claim(song, "part_of", {}, ref=fid)
        sings = False
        for role, people in (("singer", s["singers"]), ("composer", s["composers"]), ("lyricist", s["lyricists"])):
            for path, name in people:
                pid = _person(store, path, name)
                claim(song, "credit", {"role": role}, ref=pid)
                sings = sings or (role == "singer" and pid == maestro)
        for path, name in s["actors"]:
            claim(song, "on_screen", ref=_person(store, path, name))
        for c in s["categories"]:
            if c.lower().startswith("raga - "):
                claim(song, "raga", c[7:].strip())
            else:
                claim(song, "category", c)
        if sings:
            if not non_film and film["year"]:
                store.upsert_claim(song, "tier", value="A", source_id="claude", kind="decided", extractor=EXTRACTOR)
