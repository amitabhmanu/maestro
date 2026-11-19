"""HFGK Online database V2.0 (hamraaz.org/filmssite): parsers, and an extractor for whole films.

The site is the online edition of the Hindi Film Geet Kosh: every Hindi film 1931-1985 with its full
song list and credits (films never released carry a decade instead of a year). Page kinds:

  index.php                  the home page: its year selector lists every year and decade
  index.php?year=Y           every film of year (or decade) Y
  person.php?id=N            a person's filmography
  film.php?id=N              a film with its full song list and every credit

A singer's pack reads person pages with the core `hamraaz` extractor (the maestro's person id is the
pack's `identity.ids.hfgk`), which imports these parsers. The `extract` here serves a film register (a pack of `identity.kind: register`,
e.g. hindi-films): the home page queues every year, a year page queues its films, and a film page
writes the film, its crew, every song and every song credit. Persons are keyed by HFGK person id.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, quote_plus, urljoin, urlsplit

SOURCE = "hfgk_online"
BASE = "https://hamraaz.org/filmssite/public/"
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


# ---- home and year pages -----------------------------------------------------

_YEAR_SELECT = re.compile(r'<select name="year".*?</select>', re.S)
_OPTION = re.compile(r'<option value="([^"]+)"')
_FILM_LIST_ROW = re.compile(r'<a href="film\.php\?id=(\d+)"\s+class="film-row".*?</a>', re.S)


def parse_years(page_html: str) -> list[str]:
    """Every value of the home page's year selector: years and decades (films never dated)."""
    m = _YEAR_SELECT.search(page_html)
    return [html.unescape(v) for v in _OPTION.findall(m.group(0))] if m else []


def year_url(value: str) -> str:
    return f"{BASE}index.php?year={quote_plus(value)}"


def parse_year_list(page_html: str) -> list[dict]:
    """The films of a year page (the list under the filters, not the 'a few films' sample)."""
    films = []
    for m in _FILM_LIST_ROW.finditer(page_html):
        spans = [_text(s) for s in _SPAN.findall(m.group(0))]
        if len(spans) < 2:
            continue
        when = spans[0]
        films.append({"id": m.group(1), "year": int(when) if when.isdigit() else None,
                      "period": None if when.isdigit() else when, "title": spans[1],
                      "title_hi": spans[2] if len(spans) > 2 else None})
    return films


# ---- extractor (film register) -----------------------------------------------

class BotCheck(RuntimeError):
    pass


def is_bot_check(page_html: str) -> bool:
    """The site's bot check page ("One moment, please...") rather than a film."""
    return "<title>One moment, please" in page_html[:3000] or (
        'class="song-row"' not in page_html and "<h1" not in page_html and "window.location.reload" in page_html)

def _person(store, pid: str | None, name: str) -> int | None:
    if pid is None or pid in PLACEHOLDER_PERSON_IDS:
        return None
    return store.find_or_create_by_xref("person", SOURCE, pid, name)


def _year_filter(ctx):
    years = _year_range(ctx.options.get("years"))
    return (lambda y: y is not None and years[0] <= y <= years[1]) if years else (lambda y: True)


def extract(ctx) -> None:
    url = ctx.page.url
    parts = urlsplit(url)
    keep = _year_filter(ctx)
    if parts.path.endswith("index.php") or parts.path.endswith("/public/"):
        year = parse_qs(parts.query).get("year", [None])[0]
        if year is None:
            for value in parse_years(ctx.page.text):
                if value.isdigit() and not keep(int(value)):
                    continue
                if not value.isdigit() and ctx.options.get("years"):
                    continue  # a year range leaves out the undated films
                ctx.enqueue(year_url(value), "hfgk", "spine", priority=500)
            return
        for film in parse_year_list(ctx.page.text):
            # Earlier films first, so a partial run covers whole years.
            ctx.enqueue(urljoin(BASE, f"film.php?id={film['id']}"), "hfgk", "container_sweep",
                        priority=-(film["year"] or 9999))
        return
    if "film.php" not in parts.path:
        raise ValueError(f"hfgk extractor does not handle {url}")

    store, raw = ctx.store, ctx.raw_page_id
    film_xref = re.search(r"id=(\d+)", url).group(1)
    if is_bot_check(ctx.page.text):
        # The site answers a fast crawl with a "One moment, please..." page now and then. Never get
        # around it: fail the job so nothing is written, and fetch the film again later, more slowly.
        raise BotCheck(f"HFGK served its bot check page instead of {url}")
    data = parse_film(ctx.page.text)

    def claim(eid, field, value=None, ref=None, **kw):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="hfgk", extractor_version="1", raw_page_id=raw, **kw)

    film = store.find_or_create_by_xref("container", SOURCE, film_xref, data["title"])
    claim(film, "title", data["title"])
    if data["title_hi"]:
        claim(film, "title_hi", data["title_hi"])
    year = data["details"].get("Year", "")
    if year.isdigit():
        claim(film, "year", int(year))
    elif year:
        claim(film, "period", year)
    for label, field in (("Banner", "banner"), ("Record Company", "record_company"), ("Subject", "subject")):
        if data["details"].get(label):
            claim(film, field, data["details"][label])
    for c in data["crew"]:
        pid = _person(store, c["person_id"], c["name"])
        if pid is None:
            claim(film, "credit_unlinked", {"role": c["role"], "name": c["name"]})
        else:
            claim(film, "credit", {"role": c["role"]}, ref=pid)
    claim(film, "song_count", len(data["songs"]))

    # The pack tiers songs by kind (canon_model.kind_tiers): a dated film was released.
    kind = "film_song" if year.isdigit() else "unreleased_film_song"
    tier = (store.pack.data["canon_model"].get("kind_tiers") or {}).get(kind)
    for s in data["songs"]:
        song = store.find_or_create_by_xref("song", SOURCE, s["id"], s["title"])
        claim(song, "title", s["title"])
        if s["title_hi"]:
            claim(song, "title_hi", s["title_hi"])
        claim(song, "part_of", {"position": s["number"]}, ref=film)
        claim(song, "work_kind", kind)
        for c in s["credits"]:
            pid = _person(store, c["person_id"], c["name"])
            if pid is None:
                claim(song, "credit_unlinked", {"role": c["role"], "name": c["name"]})
            else:
                claim(song, "credit", {"role": c["role"]}, ref=pid)
        for o in s["on_screen"]:
            pid = _person(store, o["person_id"], o["name"])
            if pid is not None:
                claim(song, "on_screen", ref=pid)
        if tier:
            store.upsert_claim(song, "tier", value=tier, source_id="claude", kind="decided", extractor="hfgk")
