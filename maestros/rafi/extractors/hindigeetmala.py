"""hindigeetmala.net's song list for Rafi: every song the site credits to him, with film, year and credits.

Seed: https://www.hindigeetmala.net/singer/mohammed_rafi.php (robots.txt allows everything). The list
runs over pages of ten (`?page=2` ... "Page 1 of 456" in the title); the first page queues the rest.
Each song is marked up with schema.org microdata: title, singers, music director, film with its year,
lyricist, actors on screen and the site's categories (moods, and ragas such as "Raga - Pahadi").

A song becomes a unit keyed by the site's song page, in a film container keyed by its film page.
Albums named "Non-Film (...)" are not films: their songs stay uncontained with that category.
Film songs with a year are Tier A, non-film songs Tier B, as the pack defines them.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

SOURCE = "hindigeetmala"
EXTRACTOR = "hindigeetmala"
BASE = "https://www.hindigeetmala.net/"
MAESTRO_PATH = "/singer/mohammed_rafi.php"

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
    if path == MAESTRO_PATH:
        eid = store.maestro_entity()
        store.upsert_claim(eid, "xref", value=path, source_id=SOURCE)
        return eid
    return store.find_or_create_by_xref("person", SOURCE, path, name)


def extract(ctx) -> None:
    data = parse(ctx.page.text)
    if "page=" not in ctx.page.url:
        for p in range(2, data["pages"] + 1):
            ctx.enqueue(urljoin(BASE, f"{MAESTRO_PATH.lstrip('/')}?page={p}"), EXTRACTOR, "spine")

    store, raw = ctx.store, ctx.raw_page_id
    maestro = store.maestro_entity()

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor=EXTRACTOR, extractor_version="1", raw_page_id=raw)

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
            tier = "B" if non_film else ("A" if film["year"] else None)
            if tier:
                store.upsert_claim(song, "tier", value=tier, source_id="claude", kind="decided", extractor=EXTRACTOR)
