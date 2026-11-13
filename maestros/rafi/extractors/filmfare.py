"""Wikipedia's "Filmfare Award for Best Male Playback Singer": the maestro's wins and nominations.

Seed: https://en.wikipedia.org/wiki/Filmfare_Award_for_Best_Male_Playback_Singer (robots.txt allows
/wiki/ pages). The "Winners and nominees" tables list year, singer, song and film per row; the
winner's row is in bold (and marked with a double dagger). Each row naming the maestro becomes a
`filmfare` mention with the film, strength 2 for a win and 1 for a nomination.
"""

from __future__ import annotations

import re

from maestro.popularity import names_maestro, record_mention
from maestro.wikitable import read_tables

SOURCE = "wikipedia"


def parse(page: str) -> list[dict]:
    out = []
    for t in read_tables(page):
        if not t.headings or t.headings[0] != "Winners and nominees":
            continue
        heads = [h.lower() for h in t.header_texts()]
        if not {"year", "singer", "song", "film"} <= set(heads):
            continue
        i = {h: heads.index(h) for h in ("year", "singer", "song", "film")}
        for row in t.body():
            if len(row) <= max(i.values()):
                continue
            m = re.match(r"(\d{4})", row[i["year"]].text)
            song = row[i["song"]].text.strip().strip('"“”').strip()
            if not m or not song or row[i["song"]].text == row[i["singer"]].text:
                continue  # a note spanning the row ("Award won by a female singer")
            singer = row[i["singer"]].text.replace("‡", "").strip()
            won = row[i["singer"]].bold or "‡" in row[i["singer"]].text
            out.append({"year": int(m.group(1)), "singer": singer, "song": song,
                        "film": row[i["film"]].text.strip(), "won": won})
    return out


def extract(ctx) -> None:
    for r in parse(ctx.page.text):
        if not names_maestro(ctx.store, r["singer"]):
            continue
        what = "won" if r["won"] else "nominated"
        # The award year is the ceremony's; the song is from the film year before.
        record_mention(ctx, SOURCE, "filmfare", r["song"], strength=2 if r["won"] else 1, year=r["year"] - 1,
                       container=r["film"], note=f"Filmfare Best Male Playback, {what} ({r['year']})",
                       extractor="filmfare", award_year=r["year"], won=r["won"])
