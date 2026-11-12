"""Extractor for the HFGK Online database V2.0 (hamraaz.org/filmssite).

Two page kinds:
  person.php?id=N  a filmography: queues one film job per film (optionally limited by year). On Rafi's
                   own page it also records each film and Rafi's roles in it, so a film that credits
                   him but yields no Rafi song shows up as a gap.
  film.php?id=N    a film with its full song list: writes the film, its songs and every credit

Song-level singers, lyricists and composers come from the song rows; actors shown with a song
(picturisation) become `on_screen` claims, which the canon finder uses later.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urljoin

SOURCE = "hfgk_online"
BASE = "https://hamraaz.org/filmssite/public/"
MAESTRO_PERSON_ID = "4357"  # "Md Rafi" in this database
PLACEHOLDER_PERSON_IDS = {"10000"}  # "Others": unnamed chorus or co-singers

ROLE_ICONS = {"🎤": "singer", "✍️": "lyricist", "🎼": "composer"}
FILM_ROLE_HEADINGS = {
    "Director": "director",
    "Music Director": "composer",
    "Asst. Music Director": "assistant_composer",
    "Lyricist": "lyricist",
    "Producer": "producer",
}

_TAG = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", fragment))).strip()


def _year_range(spec: str | None) -> tuple[int, int] | None:
    if not spec:
        return None
    lo, _, hi = spec.partition("-")
    return int(lo), int(hi or lo)


# ---- person page -------------------------------------------------------------

_FILM_ROW = re.compile(
    r'<a href="film\.php\?id=(\d+)"\s+class="person-film-row".*?>(.*?)</a>', re.S
)
_SPAN = re.compile(r"<span[^>]*>(.*?)</span>", re.S)


def parse_person(page_html: str) -> list[dict]:
    films = []
    for film_id, body in _FILM_ROW.findall(page_html):
        spans = [_text(s) for s in _SPAN.findall(body)]
        if len(spans) < 3:
            continue
        when, title = spans[0], spans[1]
        roles = [r.strip() for r in spans[-1].split(",")]
        hindi = spans[2] if len(spans) >= 4 else None
        films.append({
            "id": film_id,
            "year": int(when) if when.isdigit() else None,
            "period": None if when.isdigit() else when,
            "title": title,
            "title_hi": hindi,
            "roles": roles,
        })
    return films


# ---- film page ---------------------------------------------------------------

_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>\s*<div[^>]*>(.*?)</div>", re.S)
_DETAIL = re.compile(r'<span class="detail-label">(.*?)</span><span class="detail-val">(.*?)</span>', re.S)
_ROLE_BOX = re.compile(r'<div class="detail-box"[^>]*>\s*<h3[^>]*>(.*?)</h3>(.*?)</div>\s*</div>', re.S)
_PERSON_LINK = re.compile(r'<a href="person\.php\?id=(\d+)"(.*?)</a>', re.S)
_SONG_ROW = re.compile(r'<a href="song\.php\?id=(\d+)" class="song-row".*?</a>', re.S)
_ROLE_SPAN = re.compile(
    r"<span>(🎤|✍️|🎼)\s*(.*?)</span>(?=\s*(?:<span>(?:🎤|✍️|🎼)|</div>))", re.S
)
_ONCLICK_PERSON = re.compile(r"person\.php\?id=(\d+)'\"[^>]*>([^<]*)</span>")
_ON_SCREEN = re.compile(r"<span title=\"([^\"]*)\"[^>]*window\.location='person\.php\?id=(\d+)'")
# The Devanagari title, when present, is a text-only div right after the Roman one.
_SONG_TITLES = re.compile(r'<div[^>]*font-weight:600[^>]*>(.*?)</div>\s*(?:<div[^>]*>([^<]*)</div>)?', re.S)


def parse_film(page_html: str) -> dict:
    m = _H1.search(page_html)
    film = {
        "title": _text(m.group(1)) if m else "",
        "title_hi": _text(m.group(2)) if m else None,
        "details": {_text(k): _text(v) for k, v in _DETAIL.findall(page_html)},
        "crew": [],
        "songs": [],
    }
    for heading, box in _ROLE_BOX.findall(page_html):
        role = FILM_ROLE_HEADINGS.get(_text(heading))
        if role:
            for pid, inner in _PERSON_LINK.findall(box):
                spans = _SPAN.findall(inner)  # an avatar initial may come first; the name is last
                film["crew"].append({"role": role, "person_id": pid, "name": _text(spans[-1]) if spans else ""})

    for row in _SONG_ROW.finditer(page_html):
        song_id, body = row.group(1), row.group(0)
        titles = _SONG_TITLES.search(body)
        title = _text(titles.group(1)) if titles else ""
        title_hi = _text(titles.group(2)) if titles and titles.group(2) else None
        number, _, title = title.partition(". ") if re.match(r"^\d+\. ", title) else ("", "", title)
        if title_hi and re.match(r"^\d+\. ", title_hi):
            title_hi = title_hi.split(". ", 1)[1]
        credits = []
        for icon, inner in _ROLE_SPAN.findall(body):
            people = _ONCLICK_PERSON.findall(inner)
            for pid, name in people:
                credits.append({"role": ROLE_ICONS[icon], "person_id": pid, "name": _text(name)})
            if not people and _text(inner):  # e.g. "Others" with no link
                for name in _text(inner).split(","):
                    if name.strip():
                        credits.append({"role": ROLE_ICONS[icon], "person_id": None, "name": name.strip()})
        on_screen = [{"person_id": pid, "name": html.unescape(name)} for name, pid in _ON_SCREEN.findall(body)]
        film["songs"].append({
            "id": song_id, "number": int(number) if number else None,
            "title": title, "title_hi": title_hi, "credits": credits, "on_screen": on_screen,
        })
    return film


# ---- extractor entry point ---------------------------------------------------

def _person(store, pid: str | None, name: str) -> int | None:
    if pid is None or pid in PLACEHOLDER_PERSON_IDS:
        return None
    if pid == MAESTRO_PERSON_ID:
        eid = store.maestro_entity()
        store.upsert_claim(eid, "xref", value=pid, source_id=SOURCE)
        return eid
    return store.find_or_create_by_xref("person", SOURCE, pid, name)


PERSON_PAGE_ROLES = {"singer": "singer", "artists": "actor", "lyricist": "lyricist",
                     "music director": "composer", "director": "director", "producer": "producer"}


def _record_filmography_entry(ctx, film: dict) -> None:
    store = ctx.store
    kw = dict(source_id=SOURCE, extractor="hamraaz", extractor_version="1", raw_page_id=ctx.raw_page_id)
    fid = store.find_or_create_by_xref("container", SOURCE, film["id"], film["title"])
    store.upsert_claim(fid, "title", value=film["title"], **kw)
    if film["year"]:
        store.upsert_claim(fid, "year", value=film["year"], **kw)
    maestro = store.maestro_entity()
    for role in film["roles"]:
        mapped = PERSON_PAGE_ROLES.get(role.lower())
        if mapped:
            store.upsert_claim(fid, "credit", value={"role": mapped}, ref_entity_id=maestro, **kw)
        else:
            store.upsert_claim(fid, "credit_unmapped", value={"role": role}, ref_entity_id=maestro, **kw)


def extract(ctx) -> None:
    url = ctx.page.url
    if "person.php" in url:
        years = _year_range(ctx.options.get("years"))
        is_maestro = re.search(r"id=(\d+)", url).group(1) == MAESTRO_PERSON_ID
        for film in parse_person(ctx.page.text):
            if years and (film["year"] is None or not years[0] <= film["year"] <= years[1]):
                continue
            if is_maestro:
                _record_filmography_entry(ctx, film)
            # Earlier films first, so a partial run covers whole years.
            priority = -(film["year"] or 9999)
            ctx.enqueue(urljoin(BASE, f"film.php?id={film['id']}"), "hamraaz", "container_sweep", priority=priority)
        return
    if "film.php" not in url:
        raise ValueError(f"hamraaz extractor does not handle {url}")

    store, raw = ctx.store, ctx.raw_page_id
    film_xref = re.search(r"id=(\d+)", url).group(1)
    data = parse_film(ctx.page.text)

    def claim(eid, field, value=None, ref=None, **kw):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="hamraaz", extractor_version="1", raw_page_id=raw, **kw)

    film = store.find_or_create_by_xref("container", SOURCE, film_xref, data["title"])
    claim(film, "title", data["title"])
    if data["title_hi"]:
        claim(film, "title_hi", data["title_hi"])
    year = data["details"].get("Year", "")
    if year.isdigit():
        claim(film, "year", int(year))
    for label, field in (("Banner", "banner"), ("Record Company", "record_company"), ("Subject", "subject")):
        if data["details"].get(label):
            claim(film, field, data["details"][label])
    for c in data["crew"]:
        claim(film, "credit", {"role": c["role"]}, ref=_person(store, c["person_id"], c["name"]))
    claim(film, "song_count", len(data["songs"]))

    maestro = store.maestro_entity()
    for s in data["songs"]:
        song = store.find_or_create_by_xref("song", SOURCE, s["id"], s["title"])
        claim(song, "title", s["title"])
        if s["title_hi"]:
            claim(song, "title_hi", s["title_hi"])
        claim(song, "part_of", {"position": s["number"]}, ref=film)
        sings = False
        for c in s["credits"]:
            pid = _person(store, c["person_id"], c["name"])
            if pid is None:
                claim(song, "credit_unlinked", {"role": c["role"], "name": c["name"]})
                continue
            claim(song, "credit", {"role": c["role"]}, ref=pid)
            sings = sings or (pid == maestro and c["role"] == "singer")
        for o in s["on_screen"]:
            claim(song, "on_screen", ref=_person(store, o["person_id"], o["name"]))
        if sings:
            # Tier A = Hindi film songs from released films; HFGK gives undated (unreleased) films a decade only.
            store.upsert_claim(song, "tier", value="A" if year.isdigit() else "C", source_id="claude",
                               kind="decided", extractor="hamraaz")
    if any(c["role"] == "singer" and c["person_id"] == MAESTRO_PERSON_ID for s in data["songs"] for c in s["credits"]):
        claim(film, "credit", {"role": "singer"}, ref=maestro)
