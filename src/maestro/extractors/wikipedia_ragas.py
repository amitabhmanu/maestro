"""Wikipedia's "List of film songs based on ragas": the raga of a Hindi film maestro's songs.

Seed: https://en.wikipedia.org/wiki/List_of_film_songs_based_on_ragas (one table: Rāg, Song, Film,
Music composer, Singer(s), Lang). Rows in Hindi whose singers name the maestro (the pack's name or one
of its variants, in full: "Lata Mangeshkar", not "Hemlata") are placed in the canon the way Hindi
Geetmala's are (`hindigeetmala.place`: the canon's own film by title, the year from the film's
article, the song by title within it). A placed song gets a `raga` claim as the list words it
("Yaman / Kalyani (raga)"; the explorer's raga table reads the Hindustani name out of it). The source
adds details only: no title, credit, part_of or tier, so no lineage. A row the canon cannot place is
kept on the maestro as `listing_gap` (reason `film_not_in_canon` or `song_not_in_film`, source
`wikipedia_ragas`), so nothing is lost.

The film register (maestros/hindi-films) has its own extractor of this name, which places every row
by the film's linked article.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from maestro.resolve import fold
from maestro.wikitable import read_tables, wiki_title

SOURCE = "wikipedia_ragas"
EXTRACTOR = "wikipedia_ragas"
VERSION = "1"
SEED = "https://en.wikipedia.org/wiki/List_of_film_songs_based_on_ragas"

_NOTE = re.compile(r"\[[^\]]*\]")  # footnote marks and "[circular reference]"


def parse(page_html: str) -> list[dict]:
    """One dict per Hindi row: raga, song, film (title and year), article, singers."""
    rows = []
    for t in read_tables(page_html):
        headers = [h.lower() for h in t.header_texts()]
        if not headers or "song" not in headers or "film" not in headers:
            continue
        ri = next((i for i, h in enumerate(headers) if h.startswith(("rāg", "rag", "raag"))), 0)
        si, fi = headers.index("song"), headers.index("film")
        li = next((i for i, h in enumerate(headers) if h.startswith("lang")), None)
        gi = next((i for i, h in enumerate(headers) if h.startswith("singer")), None)
        for r in t.body():
            if max(si, fi) >= len(r):
                continue
            lang = r[li].text.strip() if li is not None and li < len(r) else ""
            if "hindi" not in lang.lower():
                continue
            article = None
            for link in r[fi].links:
                if "redlink=1" not in link and (a := wiki_title(link)):
                    article = unquote(a).replace("_", " ")
                    break
            raga = _NOTE.sub("", r[ri].text).strip()
            song = _NOTE.sub("", r[si].text).strip().strip('"“”').strip()
            film = _NOTE.sub("", r[fi].text).strip()
            year = re.search(r"\((\d{4})(?: [^)]*)?\)", article or "") or re.search(r"\((\d{4})", film)
            film = re.sub(r"\s*\([^)]*\)\s*$", "", film).strip()
            singers = r[gi].text.strip() if gi is not None and gi < len(r) else ""
            if raga and song and film:
                rows.append({"raga": raga, "song": song, "film": film, "year": int(year.group(1)) if year else None,
                             "article": article, "singers": singers})
    return rows


def sung_by(row: dict, names: list[str]) -> bool:
    """Whether the row's singers name the maestro in full (any of the pack's spellings)."""
    s = " " + fold(row["singers"]) + " "
    return any(f" {fold(n)} " in s for n in names if len(fold(n)) >= 6)


def extract(ctx) -> None:
    from maestro.extractors.hindigeetmala import place

    store = ctx.store
    ident = store.pack.data["identity"]
    names = [ident["name"], *ident.get("variants", [])]
    maestro = store.maestro_entity()

    def claim(eid, field, value):
        store.upsert_claim(eid, field, value=value, source_id=SOURCE, extractor=EXTRACTOR,
                           extractor_version=VERSION, raw_page_id=ctx.raw_page_id)

    # The list is read whole each time: earlier gaps are superseded, never kept twice.
    store.conn.execute("""UPDATE claim SET status = 'superseded' WHERE entity_id = %s AND field = 'listing_gap'
                          AND source_id = %s AND status = 'active'""", (maestro, SOURCE))
    for row in parse(ctx.page.text):
        if not sung_by(row, names):
            continue
        units, why = place(store, {"title": row["film"], "year": row["year"]}, row["song"])
        if not units:
            claim(maestro, "listing_gap", {"title": row["song"], "film": row["film"], "year": row["year"],
                                           "raga": row["raga"], "url": SEED, "reason": why})
            continue
        for unit in units:
            claim(unit, "raga", row["raga"])
