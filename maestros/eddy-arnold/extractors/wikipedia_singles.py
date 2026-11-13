"""Wikipedia's "Eddy Arnold discography": singles and their peak chart positions.

Seed: https://en.wikipedia.org/wiki/Eddy_Arnold_discography (robots.txt allows /wiki/ pages).

Reads the tables under "Singles", "Other singles" and "Charted B-sides". Every single's A side is a
`single` mention (it was chosen for release); every chart peak a mention of that chart, with
strength 101 - peak. Columns are told apart by their headers: "US Country" is Billboard's country
chart, "US" the Hot 100 (Best Sellers before 1958), "US AC" the adult contemporary chart, "CAN
Country" and "CAN" RPM, "UK" the UK singles chart. A dash means the side did not chart.
"""

from __future__ import annotations

import re

from maestro.popularity import record_mention
from maestro.wikitable import read_tables

SOURCE = "wikipedia"
SECTIONS = ("Singles", "Other singles", "Charted B-sides")
CHARTS = {"us country": ("billboard_country", "US Country"), "us": ("billboard_hot100", "US"),
          "us ac": ("billboard_ac", "US AC"), "can country": ("rpm_country", "CAN Country"),
          "can": ("rpm", "CAN"), "uk": ("uk_singles", "UK")}


def _chart(header: str) -> tuple[str, str] | None:
    h = re.sub(r"^(peak (chart )?positions?)\s*", "", header.strip().lower())
    return CHARTS.get(h)


def parse(page: str) -> list[dict]:
    """One dict per charted or released side: title, year, chart peaks, b_side flag, album."""
    out = []
    for t in read_tables(page):
        if not t.headings or t.headings[0] not in SECTIONS:
            continue
        heads = t.header_texts()
        col = {h.lower(): i for i, h in enumerate(heads)}
        title_col = next((i for i, h in enumerate(heads) if h.lower().startswith(("single", "b-side"))), None)
        year_col = col.get("year")
        if title_col is None or year_col is None:
            continue
        charts = {i: c for i, h in enumerate(heads) if (c := _chart(h))}
        b_side = t.headings[0] == "Charted B-sides"
        for row in t.body():
            if len(row) <= title_col or not re.match(r"\d{4}$", row[year_col].text[:4]):
                continue
            year = int(row[year_col].text[:4])
            sides = re.findall(r'"([^"]+)"', row[title_col].text)
            if not sides:
                continue
            peaks = {}
            for i, (sig, label) in charts.items():
                m = re.match(r"(\d{1,3})\b", row[i].text) if i < len(row) else None
                if m:
                    peaks[sig] = (int(m.group(1)), label)
            album = row[col["album"]].text if "album" in col and col["album"] < len(row) else None
            out.append({"title": sides[0].strip(), "year": year, "peaks": peaks, "b_side": b_side,
                        "flip": sides[1].strip() if len(sides) > 1 and not b_side else None,
                        "album": album, "section": t.heading})
    return out


def extract(ctx) -> None:
    seen = set()
    for s in parse(ctx.page.text):
        key = (s["title"], s["year"], s["b_side"])
        if key in seen:  # the same single repeated in a table (a rowspan artefact or a reissue)
            continue
        seen.add(key)
        if not s["b_side"]:
            record_mention(ctx, SOURCE, "single", s["title"], strength=1, year=s["year"],
                           note=f"A side of a {s['year']} single", extractor="wikipedia_singles", cap=0.5,
                           section=s["section"])
        for sig, (peak, label) in s["peaks"].items():
            record_mention(ctx, SOURCE, sig, s["title"], strength=101 - peak, year=s["year"],
                           note=f"#{peak} {label}, {s['year']}", extractor="wikipedia_singles", peak=peak,
                           b_side=s["b_side"] or None)
