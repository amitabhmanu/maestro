"""Extractor for the HFGK Online database V2.0 (hamraaz.org/filmssite).

Two page kinds:
  person.php?id=N  a filmography: queues one film job per film (optionally limited by year). On the
                   maestro's own page (the pack's `identity.ids.hfgk`, e.g. 4357 "Md Rafi") it also
                   records each film and the maestro's roles in it, so a film that credits them but
                   yields no song of theirs shows up as a gap.
  film.php?id=N    a film with its full song list: writes the film, its songs and every credit

Song-level singers, lyricists and composers come from the song rows; actors shown with a song
(picturisation) become `on_screen` claims, which the canon finder uses later.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from maestro.extractors.hfgk import (  # noqa: F401  (parsers shared with the film register)
    BASE,
    FILM_ROLE_HEADINGS,
    ROLE_ICONS,
    SOURCE,
    _text,
    _year_range,
    is_bot_check,
    parse_film,
    parse_person,
)

PLACEHOLDER_PERSON_IDS = {"10000"}  # "Others": unnamed chorus or co-singers


# ---- extractor entry point ---------------------------------------------------

def maestro_person_ids(store) -> list[str]:
    """The maestro's person ids in this database, from the pack's `identity.ids.hfgk`: one id, or a
    list whose first id is the maestro's own filmography page and whose others are credit records the
    user ruled are the maestro too (e.g. Lata's "Lata Mangeshkar + Rafi", 16433)."""
    pid = (store.pack.data["identity"].get("ids") or {}).get("hfgk")
    if not pid:
        raise ValueError("the pack has no identity.ids.hfgk: find the maestro's person.php?id= on hamraaz.org")
    return [str(p) for p in pid] if isinstance(pid, list) else [str(pid)]


def maestro_person_id(store) -> str:
    """The maestro's own filmography page id."""
    return maestro_person_ids(store)[0]


def _person(store, pid: str | None, name: str) -> int | None:
    if pid is None or pid in PLACEHOLDER_PERSON_IDS:
        return None
    if pid in maestro_person_ids(store):
        eid = store.maestro_entity()
        store.upsert_claim(eid, "xref", value=pid, source_id=SOURCE)
        return eid
    return store.find_or_create_by_xref("person", SOURCE, pid, name)


PERSON_PAGE_ROLES = {"singer": "singer", "artists": "actor", "lyricist": "lyricist",
                     "music director": "composer", "director": "director", "producer": "producer"}


def _record_filmography_entry(ctx, film: dict) -> None:
    store = ctx.store
    kw = dict(source_id=SOURCE, extractor="hamraaz", extractor_version="1", raw_page_id=ctx.raw_page_id)
    fid = store.find_or_create_by_xref("container", SOURCE, film["id"], film["title"])
    store.upsert_claim(fid, "title", value=film["title"], **kw)
    if film["year"]:
        store.upsert_claim(fid, "year", value=film["year"], **kw)
    maestro = store.maestro_entity()
    for role in film["roles"]:
        mapped = PERSON_PAGE_ROLES.get(role.lower())
        if mapped:
            store.upsert_claim(fid, "credit", value={"role": mapped}, ref_entity_id=maestro, **kw)
        else:
            store.upsert_claim(fid, "credit_unmapped", value={"role": role}, ref_entity_id=maestro, **kw)


def extract(ctx) -> None:
    url = ctx.page.url
    if "person.php" in url:
        years = _year_range(ctx.options.get("years"))
        is_maestro = re.search(r"id=(\d+)", url).group(1) == maestro_person_id(ctx.store)
        for film in parse_person(ctx.page.text):
            if years and (film["year"] is None or not years[0] <= film["year"] <= years[1]):
                continue
            if is_maestro:
                _record_filmography_entry(ctx, film)
            # Earlier films first, so a partial run covers whole years.
            priority = -(film["year"] or 9999)
            ctx.enqueue(urljoin(BASE, f"film.php?id={film['id']}"), "hamraaz", "container_sweep", priority=priority)
        return
    if "film.php" not in url:
        raise ValueError(f"hamraaz extractor does not handle {url}")

    store, raw = ctx.store, ctx.raw_page_id
    film_xref = re.search(r"id=(\d+)", url).group(1)
    if is_bot_check(ctx.page.text):
        raise ValueError(f"HFGK served its bot check page instead of {url}: fetch it again later")
    data = parse_film(ctx.page.text)

    def claim(eid, field, value=None, ref=None, **kw):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="hamraaz", extractor_version="1", raw_page_id=raw, **kw)

    film = store.find_or_create_by_xref("container", SOURCE, film_xref, data["title"])
    claim(film, "title", data["title"])
    if data["title_hi"]:
        claim(film, "title_hi", data["title_hi"])
    year = data["details"].get("Year", "")
    if year.isdigit():
        claim(film, "year", int(year))
    for label, field in (("Banner", "banner"), ("Record Company", "record_company"), ("Subject", "subject")):
        if data["details"].get(label):
            claim(film, field, data["details"][label])
    for c in data["crew"]:
        claim(film, "credit", {"role": c["role"]}, ref=_person(store, c["person_id"], c["name"]))
    claim(film, "song_count", len(data["songs"]))

    maestro = store.maestro_entity()
    for s in data["songs"]:
        song = store.find_or_create_by_xref("song", SOURCE, s["id"], s["title"])
        claim(song, "title", s["title"])
        if s["title_hi"]:
            claim(song, "title_hi", s["title_hi"])
        claim(song, "part_of", {"position": s["number"]}, ref=film)
        sings = False
        for c in s["credits"]:
            pid = _person(store, c["person_id"], c["name"])
            if pid is None:
                claim(song, "credit_unlinked", {"role": c["role"], "name": c["name"]})
                continue
            claim(song, "credit", {"role": c["role"]}, ref=pid)
            sings = sings or (pid == maestro and c["role"] == "singer")
        for o in s["on_screen"]:
            claim(song, "on_screen", ref=_person(store, o["person_id"], o["name"]))
        if sings:
            # Tier A = Hindi film songs from released films; HFGK gives undated (unreleased) films a decade only.
            store.upsert_claim(song, "tier", value="A" if year.isdigit() else "C", source_id="claude",
                               kind="decided", extractor="hamraaz")
    if any(c["role"] == "singer" and c["person_id"] in maestro_person_ids(store) for s in data["songs"] for c in s["credits"]):
        claim(film, "credit", {"role": "singer"}, ref=maestro)
