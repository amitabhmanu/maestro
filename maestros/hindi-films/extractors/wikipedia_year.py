"""Wikipedia's "List of Hindi films of YEAR" pages, and the index that links them all.

  Lists_of_Indian_films (or any page linking the year lists): queues every List_of_Hindi_films_of_YYYY
  List_of_Hindi_films_of_YYYY: one container per film row (tables with Title and Director columns;
  box-office tables are skipped). Each film is keyed `<year>:<article>` when its title links to an
  article (the article name is what matches it to Wikidata), else `<year>:<title>`.

Older lists put the music director, lyricists and singers in a Notes column ("Music: Naushad
Lyrics: Shakeel Badayuni"); those become `credit_unlinked` claims by name. Directors too.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from maestro.wikitable import read_tables, wiki_title

SOURCE = "wikipedia"
YEAR_LIST = re.compile(r"https://en\.wikipedia\.org/wiki/List_of_(?:Hindi|Bollywood)_films_of_(\d{4})\b")
_NOTE_KEYS = {"music": "composer", "music director": "composer", "lyrics": "lyricist", "lyricist": "lyricist",
              "lyricists": "lyricist", "singers": "singer", "playback": "singer", "singer": "singer"}
_NOTE = re.compile(r"\b(Music(?: director)?|Lyrics|Lyricists?|Singers?|Playback)\s*[:\-]\s*", re.I)
_SPLIT = re.compile(r"\s*(?:,|;|\band\b|&)\s*")


def year_list_url(year: int) -> str:
    return f"https://en.wikipedia.org/wiki/List_of_Hindi_films_of_{year}"


def parse_index(page_html: str) -> list[int]:
    return sorted({int(y) for y in YEAR_LIST.findall(page_html)})


def _col(headers: list[str], *names: str) -> int | None:
    for i, h in enumerate(headers):
        if h.strip().lower() in names:
            return i
    return None


def parse_notes(text: str) -> dict[str, list[str]]:
    """'New Theatres. Music: R. C. Boral. Lyrics: Kidar Sharma' -> {composer: [...], lyricist: [...]}."""
    out: dict[str, list[str]] = {}
    marks = list(_NOTE.finditer(text or ""))
    for i, m in enumerate(marks):
        role = _NOTE_KEYS[m.group(1).lower()]
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        chunk = text[m.end():end].strip()
        # A sentence ends the list: "R. C. Boral. Lyrics" is cut by the next key; a full stop after a
        # word of 3+ letters ("Boral. New Theatres") ends it too, initials ("R. C.") do not.
        chunk = re.split(r"(?<=[a-z]{3})\.\s", chunk)[0].rstrip(". ")
        names = [re.sub(r"\s*\([^)]*\)$", "", n).strip(" .") for n in _SPLIT.split(chunk)]
        names = [n for n in names if n]
        out.setdefault(role, []).extend(n for n in names if len(n) <= 60)
    return out


def parse_year_page(page_html: str) -> list[dict]:
    films = []
    for t in read_tables(page_html):
        headers = t.header_texts()
        ti, di = _col(headers, "title", "film"), _col(headers, "director", "director(s)")
        if ti is None or di is None:
            continue
        ni = next((i for i, h in enumerate(headers) if h.lower().startswith(("notes", "music"))), None)
        for row in t.body():
            if ti >= len(row) or not row[ti].text.strip():
                continue
            cell = row[ti]
            article = None
            for link in cell.links:
                if "redlink=1" in link or "action=edit" in link:
                    continue
                article = wiki_title(link)
                if article:
                    article = unquote(article).replace("_", " ")
                    break
            notes = parse_notes(row[ni].text) if ni is not None and ni < len(row) else {}
            directors = [n.strip() for n in _SPLIT.split(row[di].text) if n.strip()] if di < len(row) else []
            title, _, aka = cell.text.strip().partition(" a.k.a. ")
            films.append({"title": title.strip(), "aka": aka.strip() or None, "article": article,
                          "directors": directors, **notes})
    return films


def extract(ctx) -> None:
    url = ctx.page.url
    m = YEAR_LIST.match(url)
    if not m:
        for year in parse_index(ctx.page.text):
            ctx.enqueue(year_list_url(year), "wikipedia_year", "spine", priority=400)
        return
    year = int(m.group(1))
    store = ctx.store

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="wikipedia_year", extractor_version="1", raw_page_id=ctx.raw_page_id)

    seen: set[str] = set()
    for f in parse_year_page(ctx.page.text):
        key = f"{year}:{f['article'] or f['title']}"
        if key in seen:
            continue
        seen.add(key)
        fid = store.find_or_create_by_xref("container", SOURCE, key, f["title"])
        claim(fid, "title", f["title"])
        claim(fid, "year", year)
        if f["aka"]:
            claim(fid, "alt_title", f["aka"])
        if f["article"]:
            claim(fid, "wikipedia", f["article"])
        for d in f["directors"]:
            claim(fid, "credit_unlinked", {"role": "director", "name": d})
        for role in ("composer", "lyricist", "singer"):
            for name in f.get(role, []):
                claim(fid, "credit_unlinked", {"role": role, "name": name})
