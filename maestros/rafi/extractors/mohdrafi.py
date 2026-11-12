"""Rafi Sahab's Song Database (mohdrafi.com/database), from pages saved by hand.

The site's robots.txt forbids crawling, so pages are saved in a browser and loaded with
`maestro-import --extractor mohdrafi --url <page url> --opt tier=B <file>`.

A page holds one or more blocks, each a header table naming a category ("Non-Filmi Ghazals")
or a film, an optional release year, and rows of track, singers, lyricist and musician.
Category blocks (names starting "Non-Filmi") become a `category` claim on each song;
any other block is treated as a film and becomes a container with `part_of` claims.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlparse

SOURCE = "mohdrafi"
MAESTRO_NAMES = {"mohd rafi", "mohammed rafi", "mohammad rafi", "md rafi", "rafi"}

_BLOCK = re.compile(
    r"<table[^>]*>\s*<tr>\s*<th>(?P<name>[^<]+)</th>\s*</tr>\s*</table>\s*(?P<body><table.*?</table>)",
    re.S | re.I,
)
_YEAR = re.compile(r"Release Year</th>\s*<td[^>]*>(?P<y>.*?)</td>", re.S | re.I)
_ROW = re.compile(r"<tr>\s*<td>\s*<a href='[^']*load=track&(?:amp;)?TRACK=[^']*'>.*?</tr>", re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_LINK = re.compile(r"<a href='([^']*)'>(.*?)</a>", re.S | re.I)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _links(cell: str) -> list[str]:
    return [_text(name) for _, name in _LINK.findall(cell) if _text(name)]


def decode(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


def parse(page: str) -> list[dict]:
    """Blocks of {name, year, tracks: [{track_key, title, singers, lyricists, composers}]}."""
    blocks = []
    for m in _BLOCK.finditer(page):
        body = m.group("body")
        y = _YEAR.search(body)
        year = _text(y.group("y")) if y else ""
        tracks = []
        for row in _ROW.finditer(body):
            cells = _CELL.findall(row.group(0))
            href, title = _LINK.search(cells[0]).groups()
            key = parse_qs(urlparse(html.unescape(href)).query).get("TRACK", [_text(title)])[0]
            tracks.append({
                "track_key": key,
                "title": _text(title),
                "singers": _links(cells[1]) if len(cells) > 1 else [],
                "lyricists": _links(cells[2]) if len(cells) > 2 else [],
                "composers": _links(cells[3]) if len(cells) > 3 else [],
            })
        blocks.append({"name": _text(m.group("name")), "year": int(year) if year.isdigit() else None,
                       "tracks": tracks})
    return blocks


def _person(store, name: str) -> int:
    if name.lower().replace(".", "").strip() in MAESTRO_NAMES:
        eid = store.maestro_entity()
        store.upsert_claim(eid, "xref", value=f"name:{name}", source_id=SOURCE)
        return eid
    return store.find_or_create_by_xref("person", SOURCE, f"name:{name}", name)


def extract(ctx) -> None:
    store, raw = ctx.store, ctx.raw_page_id
    tier = ctx.options.get("tier")
    maestro = store.maestro_entity()

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="mohdrafi", extractor_version="1", raw_page_id=raw)

    for block in parse(decode(ctx.page.body)):
        is_category = block["name"].lower().startswith("non-filmi")
        container = None
        if not is_category:
            container = store.find_or_create_by_xref("container", SOURCE, f"film:{block['name']}", block["name"])
            claim(container, "title", block["name"])
            if block["year"]:
                claim(container, "year", block["year"])
        for n, t in enumerate(block["tracks"], 1):
            song = store.find_or_create_by_xref("song", SOURCE, f"{block['name']}|{t['track_key']}", t["title"])
            claim(song, "title", t["title"])
            if is_category:
                claim(song, "category", block["name"])
                if block["year"]:
                    claim(song, "year", block["year"])
            else:
                claim(song, "part_of", {"position": n}, ref=container)
            sings = False
            for role, names in (("singer", t["singers"]), ("lyricist", t["lyricists"]), ("composer", t["composers"])):
                for name in names:
                    pid = _person(store, name)
                    claim(song, "credit", {"role": role}, ref=pid)
                    sings = sings or (role == "singer" and pid == maestro)
            if sings and tier:
                store.upsert_claim(song, "tier", value=tier, source_id="claude", kind="decided", extractor="mohdrafi")
