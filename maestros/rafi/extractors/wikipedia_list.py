"""Wikipedia's "List of songs recorded by Mohammed Rafi": the songs editors chose to list.

Seed: https://en.wikipedia.org/wiki/List_of_songs_recorded_by_Mohammed_Rafi (robots.txt allows
/wiki/ pages). The list holds a few hundred of his several thousand songs, in tables of year, film,
song, composer, singer and writer: a selection of the songs people remember. Each row becomes a
`wikipedia_list` mention with the film (strength 1, capped at 0.8: a choice, not a ranking).
"""

from __future__ import annotations

import re

from maestro.popularity import record_mention
from maestro.wikitable import read_tables

SOURCE = "wikipedia"


def parse(page: str) -> list[dict]:
    out = []
    for t in read_tables(page):
        heads = [h.lower() for h in t.header_texts()]
        if not {"year", "film", "song"} <= set(heads):
            continue
        for row in t.body():
            if len(row) < len(heads):
                continue
            m = re.match(r"(\d{4})", row[heads.index("year")].text)
            song = row[heads.index("song")].text.strip('"“” ')
            if m and song:
                out.append({"year": int(m.group(1)), "song": song, "film": row[heads.index("film")].text.strip()})
    return out


def extract(ctx) -> None:
    seen = set()
    for r in parse(ctx.page.text):
        if (r["song"], r["film"]) in seen:
            continue
        seen.add((r["song"], r["film"]))
        record_mention(ctx, SOURCE, "wikipedia_list", r["song"], strength=1, year=r["year"], container=r["film"],
                       cap=0.8, note="in Wikipedia's list of songs recorded by Mohammed Rafi", extractor="wikipedia_list")
