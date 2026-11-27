"""Wikipedia's "List of American films of YEAR" pages, and the index that links them all.

  Lists_of_American_films (or any page linking the year lists): queues every List_of_American_films_of_YYYY
  List_of_American_films_of_YYYY: one container per film row (tables with Title and Director columns;
  box-office tables are skipped). Each film is keyed `<year>:<article>` when its title links to an
  article (the article name is what matches it to Wikidata), else `<year>:<title>`.

Each row gives the title, directors (`credit_unlinked`), the Genre column's labels as `subject` claims
("Musical comedy", "Western"; the atlas folds them into its genre tree) and the studio, the Notes column's
first part ("MGM; Oscar for Bergman" -> MGM), as a `banner` claim. Short films and serials keep their genre
label ("Animated short", "Serial"), so a register can tell them from features.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from maestro.wikitable import read_tables, wiki_title

SOURCE = "wikipedia"
VERSION = "1"
YEAR_LIST = re.compile(r"https://en\.wikipedia\.org/wiki/List_of_American_films_of_(\d{4})\b")
_SPLIT = re.compile(r"\s*(?:,|;|\band\b|&|/)\s*")
_STUDIO_END = re.compile(r"\s*(?:;|\.\s|\(|$)")


def year_list_url(year: int) -> str:
    return f"https://en.wikipedia.org/wiki/List_of_American_films_of_{year}"


def parse_index(page_html: str) -> list[int]:
    return sorted({int(y) for y in YEAR_LIST.findall(page_html)})


def _col(headers: list[str], *names: str) -> int | None:
    for i, h in enumerate(headers):
        if h.strip().lower() in names:
            return i
    return None


def studio(notes: str) -> str | None:
    """'MGM; Oscar for Bergman' -> 'MGM'; 'Paramount. 7 Oscar nominations' -> 'Paramount'; '' -> None."""
    first = _STUDIO_END.split((notes or "").strip(), 1)[0].strip(" .")
    return first if first and len(first) <= 40 and not re.search(r"\d|\b(based|remake|oscar|academy)\b", first, re.I) else None


def parse_year_page(page_html: str) -> list[dict]:
    films = []
    for t in read_tables(page_html):
        headers = t.header_texts()
        ti, di = _col(headers, "title", "film"), _col(headers, "director", "director(s)", "directors")
        if ti is None or di is None:
            continue
        gi = _col(headers, "genre", "genres")
        ni = next((i for i, h in enumerate(headers) if h.lower().startswith(("notes", "studio", "distributor"))), None)
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
            directors = [n.strip() for n in _SPLIT.split(row[di].text) if n.strip()] if di < len(row) else []
            genres = [g.strip() for g in re.split(r"\s*[,/;]\s*", row[gi].text) if g.strip()] \
                if gi is not None and gi < len(row) else []
            st = studio(row[ni].text) if ni is not None and ni < len(row) else None
            title, _, aka = cell.text.strip().partition(" a.k.a. ")
            films.append({"title": title.strip(), "aka": aka.strip() or None, "article": article,
                          "directors": directors, "genres": genres, "studio": st})
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
                           extractor="wikipedia_year", extractor_version=VERSION, raw_page_id=ctx.raw_page_id)

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
        for g in f["genres"]:
            claim(fid, "subject", g)
        if f["studio"]:
            claim(fid, "banner", f["studio"])
