"""hindigeetmala.net lyrics for the canon's film songs: private text, publishable counts only.

Amitabh's decision (26 Nov 2026): lyrics are collected for a word-frequency analysis, kept private,
and only aggregate word counts may be published. The site's disclaimer restricts lyrics to
"educational and personal use"; its robots.txt allows everything.

Seed: the maestro's song list, https://www.hindigeetmala.net/singer/<identity.ids.hindigeetmala>.php,
with this extractor (`--purpose enrich`). List pages are read with `hindigeetmala.parse()`; the first
page queues the rest. Each film song the canon already holds (placed by `hindigeetmala.place()`, or a
unit the hindigeetmala extractor created itself, found by its `song:<key>` xref) queues its song page
with the unit as the job's entity. Non-film and unplaced songs are skipped: the hindigeetmala extractor
already records those as `listing_gap` claims.

On a song page: if the page's own song row names the maestro as a singer, the lyrics block becomes one
`lyrics` claim on the job's unit (line breaks kept). `upsert_claim` makes it private under the pack's
`legal.lyrics` (no publishable first-line copy is made for the `lyrics` field), and a publishable
`lyrics_meta` claim records only the source, script and line and word counts. A page whose song row
does not name the maestro, or that has no lyrics block, writes nothing.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

from maestro.extractors import hindigeetmala as hgm

SOURCE = "hindigeetmala_lyrics"
EXTRACTOR = "hindigeetmala_lyrics"
VERSION = "1"
BASE = hgm.BASE

LYRICS = re.compile(r'<span itemprop="lyrics"[^>]*>\s*<span itemprop="text">(.*?)</span>', re.S)


# ---- shared with the lrclib extractor ---------------------------------------------------------------

def script_of(text: str) -> str:
    """latin, devanagari or urdu when one script has at least 90% of the letters, else mixed."""
    dev = sum("ऀ" <= c <= "ॿ" for c in text)
    urd = sum("؀" <= c <= "ۿ" or "ݐ" <= c <= "ݿ" or "ﭐ" <= c <= "﻿" for c in text)
    lat = sum(c.isascii() and c.isalpha() for c in text)
    total = dev + urd + lat
    if not total:
        return "mixed"
    for n, name in ((lat, "latin"), (dev, "devanagari"), (urd, "urdu")):
        if n / total >= 0.9:
            return name
    return "mixed"


def lyrics_meta(text: str, source: str) -> dict:
    """Publishable facts about a lyric text: never any of the text itself."""
    lines = [line for line in (x.strip() for x in text.splitlines()) if line]
    return {"source": source, "script": script_of(text), "lines": len(lines),
            "words": sum(len(line.split()) for line in lines)}


def store_lyrics(store, unit: int, text: str, *, source_id: str, meta_source: str, extractor: str,
                 raw_page_id: int | None) -> None:
    """The private `lyrics` claim and its publishable `lyrics_meta` beside it."""
    common = {"source_id": source_id, "extractor": extractor, "extractor_version": VERSION, "raw_page_id": raw_page_id}
    store.upsert_claim(unit, "lyrics", value=text, **common)
    store.upsert_claim(unit, "lyrics_meta", value=lyrics_meta(text, meta_source), **common)


# ---- song pages ----------------------------------------------------------------------------------------

def lyrics_text(page: str) -> str | None:
    """The lyrics block with its line breaks, or None when the page has none."""
    m = LYRICS.search(page)
    if not m:
        return None
    text = unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>\r?\n?", "\n", m.group(1))))
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)  # keep stanza breaks, drop runs of blank lines
    return text or None


SONG_ROW = re.compile(r'itemtype="https?://schema\.org/MusicRecording"|<tr itemprop="track"')
BY_ARTIST = re.compile(r'itemprop="byArtist"[^>]*>\s*<a[^>]*href="(/singer/[^"]+)"')


def song_singers(page: str) -> list[str]:
    """Singer page paths credited (schema.org byArtist) in the page's own song row, which runs from its
    MusicRecording markup to the lyrics heading; the site's navigation menu links singers too."""
    start = SONG_ROW.search(page)
    if not start:
        return []
    end = min([i for i in (page.find("<h2", start.end()), page.find('itemprop="lyrics"', start.end())) if i >= 0]
              or [len(page)])
    return BY_ARTIST.findall(page[start.start():end])


# ---- list pages -------------------------------------------------------------------------------------------

def units_for(store, song: dict) -> list[int]:
    """The canon units a list entry names: the unit the hindigeetmala extractor created for it, else the
    canon song the listing places by film and title."""
    from maestro.resolve import Groups

    own = store.entity_by_xref(hgm.SOURCE, f"song:{song['key']}", "song")
    if own is not None:
        groups = getattr(store, "_hgm_lyrics_groups", None) or Groups(store)
        store._hgm_lyrics_groups = groups
        return [groups.find(own)]
    units, _why = hgm.place(store, song["film"], song["title"])
    return units


def _list_page(ctx, data: dict) -> None:
    store = ctx.store
    if "page=" not in ctx.page.url:
        for p in range(2, data["pages"] + 1):
            ctx.enqueue(urljoin(BASE, f"{hgm.maestro_path(store).lstrip('/')}?page={p}"), EXTRACTOR, "enrich")
    for s in data["songs"]:
        if s["film"] is None or hgm.NON_FILM.match(s["film"]["title"]):
            continue
        units = units_for(store, s)
        if units:
            # Song pages go ahead of the remaining list pages, so the queue stays short.
            ctx.enqueue(urljoin(BASE, f"song/{s['key']}.htm"), EXTRACTOR, "enrich", entity_id=units[0], priority=1)


def extract(ctx) -> None:
    url = ctx.page.url
    if "/singer/" in url:
        _list_page(ctx, hgm.parse(ctx.page.text))
        return
    if "/song/" not in url:
        raise ValueError(f"not a singer list or song page: {url}")
    unit = (ctx.job or {}).get("entity_id")
    if unit is None:
        raise ValueError("a song page job needs the unit as its entity_id (queued from a list page)")
    page = ctx.page.text
    if hgm.maestro_path(ctx.store) not in song_singers(page):
        return
    text = lyrics_text(page)
    if text:
        store_lyrics(ctx.store, unit, text, source_id=SOURCE, meta_source="hindigeetmala", extractor=EXTRACTOR,
                     raw_page_id=ctx.raw_page_id)
