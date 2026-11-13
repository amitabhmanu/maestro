"""hindigeetmala.net's Binaca Geetmala annual lists: every ranked song of each year, 1953 to 1983.

Seed: https://www.hindigeetmala.net/geetmala/binaca_geetmala_1953.php (robots.txt allows everything).
Each year's list is split over pages of ten (`?page=2` ...). The first page of a year queues that
year's other pages; the seed year's first page also queues every other year. Each song is marked up
with schema.org microdata: rank, title, singers, music director and film (with its year). A song
whose singers include the maestro becomes a `binaca_geetmala` mention with strength 41 - rank (at
least 1), like the #1s read from Wikipedia, so the two sources agree on scale.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

from maestro.popularity import names_maestro, record_mention

SOURCE = "hindigeetmala"
BASE = "https://www.hindigeetmala.net/geetmala/"
SEED_YEAR = 1953
LIST_LENGTH = 40

TRACK = re.compile(r'<tr itemprop="track".*?</tr></table></td></tr>', re.S)


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", s))).strip()


def _names(chunk: str, kind: str) -> list[str]:
    return [_text(n) for n in re.findall(rf'href="/{kind}/[^"]+"[^>]*>(?:<span[^>]*>)?([^<]+)', chunk)]


def parse(page: str) -> dict:
    """The year, the page links and one dict per ranked song: rank, title, singers, composers, film, film_year."""
    m = re.search(r"Annual List for Year (\d{4})", page)
    year = int(m.group(1)) if m else None
    songs = []
    for tr in TRACK.findall(page):
        rank = re.search(r'<span class="song">(\d+)</span>', tr)
        title = re.search(r'itemprop="url"><span itemprop="name">([^<]+)</span>', tr)
        if not rank or not title:
            continue
        film = re.search(r'href="/movie/[^"]+"><span itemprop="name">([^<]+)</span>\s*(?:\((\d{4})\))?', tr)
        songs.append({"rank": int(rank.group(1)), "title": _text(title.group(1)),
                      "singers": _names(tr, "singer"), "composers": _names(tr, "music_director"),
                      "film": _text(film.group(1)) if film else None,
                      "film_year": int(film.group(2)) if film and film.group(2) else None})
    years = sorted({int(y) for y in re.findall(r'href="/geetmala/binaca_geetmala_(\d{4})\.php"', page)})
    pages = sorted({int(p) for p in re.findall(rf'binaca_geetmala_{year}\.php\?page=(\d+)', page)}) if year else []
    return {"year": year, "songs": songs, "years": years, "pages": pages}


def extract(ctx) -> None:
    data = parse(ctx.page.text)
    year = data["year"]
    if year is None:
        return
    first_page = "page=" not in ctx.page.url
    if first_page:
        for p in data["pages"]:
            if p > 1:
                ctx.enqueue(urljoin(BASE, f"binaca_geetmala_{year}.php?page={p}"), "hindigeetmala_binaca", "enrich")
        if year == SEED_YEAR:
            for y in data["years"]:
                if y != year:
                    ctx.enqueue(urljoin(BASE, f"binaca_geetmala_{y}.php"), "hindigeetmala_binaca", "enrich")
    for s in data["songs"]:
        if not names_maestro(ctx.store, ", ".join(s["singers"])):
            continue
        record_mention(ctx, SOURCE, "binaca_geetmala", s["title"], strength=max(1, LIST_LENGTH + 1 - s["rank"]),
                       year=s["film_year"] or year, container=s["film"], rank=s["rank"], list_year=year,
                       extractor="hindigeetmala_binaca", note=f"#{s['rank']} in Binaca Geetmala's {year} annual list")
