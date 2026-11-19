"""Wikipedia's "List of film songs based on ragas": the raga of Hindi film songs.

Seed: https://en.wikipedia.org/wiki/List_of_film_songs_based_on_ragas (one table: Rāg, Song, Film, Music
composer, Singer(s), Lang). Each Hindi row's film is found by its linked article (any record of the
film that names that article); the song is the film's song whose title begins the same way (HFGK's
titles run on past the first line, Wikipedia's stop at the opening words). A matched song gets a
`raga` claim; a row that finds its film but no song is kept on the film as `listing_gap`
(reason `raga_song_not_found`), so nothing is lost.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from maestro.resolve import Groups, fold
from maestro.wikitable import read_tables, wiki_title

SOURCE = "wikipedia"
VERSION = "1"


def parse(page_html: str) -> list[dict]:
    rows = []
    for t in read_tables(page_html):
        headers = [h.lower() for h in t.header_texts()]
        if not headers or "song" not in headers or "film" not in headers:
            continue
        ri = next((i for i, h in enumerate(headers) if h.startswith(("rāg", "rag", "raag"))), 0)
        si, fi = headers.index("song"), headers.index("film")
        li = next((i for i, h in enumerate(headers) if h.startswith("lang")), None)
        for r in t.body():
            if max(si, fi) >= len(r):
                continue
            lang = r[li].text.strip() if li is not None and li < len(r) else ""
            if "hindi" not in lang.lower():
                continue
            article = None
            for link in r[fi].links:
                if "redlink=1" not in link:
                    article = wiki_title(link)
                    if article:
                        article = unquote(article).replace("_", " ")
                        break
            raga = re.sub(r"\[\d+\]", "", r[ri].text).strip()
            song = r[si].text.strip().strip('"').strip()
            if raga and song:
                rows.append({"raga": raga, "song": song, "film": r[fi].text.strip(), "article": article})
    return rows


def title_key(title: str, words: int = 3) -> str:
    """The first words of a title as consonant skeletons: "Tute Na Dil" meets HFGK's "Toote Na Dil"."""
    from maestro.films import skeleton

    w = re.sub(r"[^a-z0-9 ]+", " ", fold(title or "")).split()
    return " ".join(skeleton(x) or x for x in w[:words])


def extract(ctx) -> None:
    store = ctx.store
    groups = Groups(store)
    by_root: dict[int, set[int]] = {}
    for r in store.conn.execute(
            "SELECT entity_id, ref_entity_id FROM claim WHERE field = 'same_as' AND status = 'active'").fetchall():
        for x in (r["entity_id"], r["ref_entity_id"]):
            by_root.setdefault(groups.find(x), {groups.find(x)}).add(x)

    def claim(eid, field, value=None):
        store.upsert_claim(eid, field, value=value, source_id=SOURCE, extractor="wikipedia_ragas",
                           extractor_version=VERSION, raw_page_id=ctx.raw_page_id)

    # The list is read whole each time: earlier unmatched rows are superseded, not kept twice.
    store.conn.execute("""UPDATE claim SET status = 'superseded' WHERE field = 'listing_gap'
                          AND extractor = 'wikipedia_ragas' AND status = 'active'""")
    for row in parse(ctx.page.text):
        if not row["article"]:
            continue
        hit = store.conn.execute(
            """SELECT c.entity_id FROM claim c JOIN entity e ON e.id = c.entity_id AND e.type = 'container'
               WHERE c.field = 'wikipedia' AND c.status = 'active' AND lower(c.value #>> '{}') = lower(%s)
               LIMIT 1""", (row["article"],)).fetchone()
        if not hit:
            continue
        root = groups.find(hit["entity_id"])
        members = sorted(by_root.get(root, {root}))
        songs = store.conn.execute(
            """SELECT DISTINCT m.entity_id, t.value #>> '{}' AS title FROM claim m
               JOIN claim t ON t.entity_id = m.entity_id AND t.field IN ('title', 'first_line') AND t.status = 'active'
               WHERE m.field = 'part_of' AND m.status = 'active' AND m.ref_entity_id = ANY(%s)""",
            (members,)).fetchall()
        matched = False
        for words in (4, 3, 2):
            key = title_key(row["song"], words)
            if len(key.replace(" ", "")) < 4:
                continue
            hits = {s["entity_id"] for s in songs if title_key(s["title"], words) == key}
            if hits:
                for sid in hits:
                    claim(sid, "raga", row["raga"])
                matched = True
                break
        if not matched:
            claim(root, "listing_gap", {"reason": "raga_song_not_found", "song": row["song"], "raga": row["raga"]})

