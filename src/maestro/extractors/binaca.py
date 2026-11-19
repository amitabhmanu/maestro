"""Wikipedia's "Binaca Geetmala": the song at the top of each year's annual list.

Seed: https://en.wikipedia.org/wiki/Binaca_Geetmala (robots.txt allows /wiki/ pages). The table under
"Lists of top songs per year" gives year, song, film, music director, lyricist and singers; a row
whose singers include the maestro becomes a `binaca_geetmala` mention (rank 1, strength 40). The
full yearly lists (rank 1 to about 40) are on other sites and would add more mentions of the same signal.
"""

from __future__ import annotations

import re

from maestro.popularity import names_maestro, record_mention
from maestro.wikitable import read_tables

SOURCE = "wikipedia"
LIST_LENGTH = 40  # strength = LIST_LENGTH + 1 - rank


def parse(page: str) -> list[dict]:
    out = []
    for t in read_tables(page):
        heads = [h.lower() for h in t.header_texts()]
        if not {"year", "song", "film"} <= set(heads) or not any(h.startswith("singer") for h in heads):
            continue
        sc = next(i for i, h in enumerate(heads) if h.startswith("singer"))
        for row in t.body():
            m = re.match(r"(\d{4})", row[heads.index("year")].text)
            if m:
                out.append({"year": int(m.group(1)), "song": row[heads.index("song")].text.strip('"“” '),
                            "film": re.sub(r"\s*\(film\)$", "", row[heads.index("film")].text).strip(),
                            "singers": row[sc].text, "rank": 1})
    return out


def extract(ctx) -> None:
    for r in parse(ctx.page.text):
        if names_maestro(ctx.store, r["singers"]):
            record_mention(ctx, SOURCE, "binaca_geetmala", r["song"], strength=LIST_LENGTH + 1 - r["rank"],
                           year=r["year"], container=r["film"], rank=r["rank"], extractor="binaca",
                           note=f"#{r['rank']} in Binaca Geetmala's {r['year']} annual list")
