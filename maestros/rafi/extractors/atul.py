"""Atul's Song A Day: the "List of songs yearwise" page (https://atulsongaday.me/list-of-songs-yearwise/).

The site blocks automated clients, so the page is saved in a browser and loaded with maestro-import.
Each year section lists every song posted on the blog as `Title (Film)`, linking to its post. Singers
are not given, so these records never credit Rafi: they are matched to HFGK's film song lists, which
checks those lists from outside (a song from a Rafi film that matches no HFGK song is a listing gap).
Non-film songs (`(Singer NFS)`) are recorded without a film.
"""

from __future__ import annotations

import html
import re

SOURCE = "atulsongaday"
_SECTION = re.compile(r'<a (?:id|name)="(\d{4})"></a>')
_ENTRY = re.compile(r'<a href="(https?://wp\.me/[^"]+)">([^<]*)</a>')
_FILM = re.compile(r"^(.*?)\s*\(([^()]+)\)\s*(?:\(\d{4}\))?\s*$")


def parse(page_html: str) -> list[dict]:
    """Songs as {url, title, film, year, non_film}. Entries whose film cannot be read are kept without one."""
    out = []
    cuts = [(m.start(), int(m.group(1))) for m in _SECTION.finditer(page_html)]
    for i, (start, year) in enumerate(cuts):
        end = cuts[i + 1][0] if i + 1 < len(cuts) else len(page_html)
        for url, text in _ENTRY.findall(page_html[start:end]):
            text = html.unescape(text).strip()
            m = _FILM.match(text)
            title, film = (m.group(1).strip(), m.group(2).strip()) if m else (text, None)
            non_film = bool(film and re.search(r"\bNFS\b", film))
            out.append({"url": url, "title": title, "film": None if non_film else film,
                        "year": year, "non_film": non_film})
    return out


def extract(ctx) -> None:
    store = ctx.store

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="atul", extractor_version="1", raw_page_id=ctx.raw_page_id)

    films: dict[tuple[str, int], int] = {}
    for s in parse(ctx.page.text):
        uid = store.find_or_create_by_xref("song", SOURCE, s["url"], s["title"])
        claim(uid, "title", s["title"])
        claim(uid, "year", s["year"])
        if s["non_film"]:
            claim(uid, "category", "non-film")
        if s["film"]:
            key = (s["film"], s["year"])
            if key not in films:
                fid = store.find_or_create_by_xref("container", SOURCE, f"film:{s['film']}|{s['year']}", s["film"])
                claim(fid, "title", s["film"])
                claim(fid, "year", s["year"])
                films[key] = fid
            claim(uid, "part_of", {}, ref=films[key])
