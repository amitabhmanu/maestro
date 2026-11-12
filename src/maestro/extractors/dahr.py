"""Discography of American Historical Recordings (DAHR, UC Santa Barbara): a name page's recordings.

DAHR's firewall refuses cloud machines, so the page is saved by hand (preferably the "View All"
listing, `...?Matrix_page=100000`) and loaded with `maestro-import --extractor dahr`.

Each row of the Recordings table is one master: company, matrix number, first recording date,
title, primary performer, description and the maestro's roles on it. A row becomes a unit keyed
`matrix:<company>:<matrix>` with title, date, year, `matrix`, and `work_kind` issued_recording or
unissued_recording (the pack's `canon_model.kind_tiers` can tier these). The maestro is credited in
each role DAHR gives, mapped to pack roles (vocalist -> singer, composer/songwriter -> composer, ...).

When the pack's unit is the work, a master the maestro performs on becomes a unit of kind
`performance_recording`, and a master of a piece the maestro wrote is a sighting of that work
(keyed by its title, so several recordings of one piece are one unit).

A partner's page (one whose subject is not the maestro) gives the partner's roles: only masters
naming the maestro as a performer are kept, credited in the performing roles only.
"""

from __future__ import annotations

import html
import re
from datetime import date

SOURCE = "dahr"
ROLE = {"vocalist": "singer", "songwriter": "composer", "composer": "composer", "lyricist": "lyricist",
        "arranger": "arranger", "instrumentalist": "instrumentalist", "guitar": "instrumentalist",
        "piano": "instrumentalist", "conductor": "conductor", "leader": "leader"}
_ROW = re.compile(r"<tr(?P<attrs>[^>]*)>(?P<body>.*?)</tr>", re.S)
_CELL = re.compile(r'<td[^>]*data-label="(?P<label>[^"]+)"[^>]*>(?P<v>.*?)</td>', re.S)


def _fold(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _date(s: str) -> str | None:
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
        except ValueError:
            return None
    return None


def subject(page: str) -> str:
    """Whose name page this is (the Role column gives that person's roles)."""
    m = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    return html.unescape(m.group(1)).strip() if m else ""


def parse(page: str) -> list[dict]:
    section = page.split('class="recording-section"', 1)[-1].split("</table>", 1)[0]
    rows = []
    for m in _ROW.finditer(section):
        cells = {c.group("label").rstrip("."): c.group("v") for c in _CELL.finditer(m.group("body"))}
        if "Matrix No" not in cells:
            continue
        link = re.search(r'href="([^"]+)"', cells["Matrix No"])
        d = _text(cells.get("First Recording Date", ""))
        rows.append({
            "company": _text(cells.get("Company", "")),
            "matrix": _text(cells["Matrix No"]),
            "url": html.unescape(link.group(1)) if link else None,
            "date_text": d,
            "date": _date(d),
            "year": int(d[-4:]) if re.search(r"\d{4}$", d) else None,
            "title": _text(cells.get("Primary Title", "")),
            "performer": _text(cells.get("Primary Performer", "")),
            "description": _text(cells.get("Description", "")),
            "roles": [r.strip() for r in _text(cells.get("Role", "")).split(",") if r.strip()],
            # DAHR marks a master issued on disc ("issued") or heard from a disc ("audio-available").
            "issued": bool(re.search(r"\b(issued|audio-available)\b", m.group("attrs"))),
        })
    return rows


PERFORMING = {"singer", "instrumentalist", "conductor", "leader"}
WRITING = {"composer", "lyricist", "arranger"}


def extract(ctx) -> None:
    store, pack = ctx.store, ctx.store.pack
    maestro = store.maestro_entity()
    in_scope = set(pack.data["roles"]["in_scope"])

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="dahr", extractor_version="2", raw_page_id=ctx.raw_page_id)

    names = {_fold(n) for n in [pack.data["identity"]["name"], *pack.data["identity"].get("variants", [])]}
    own_page = _fold(subject(ctx.page.text)) in names
    for r in parse(ctx.page.text):
        roles = {ROLE[x] for x in r["roles"] if x in ROLE}
        if not own_page:
            # A partner's page (e.g. a piano duo): the roles are the partner's. Keep only masters that
            # name the maestro as a performer, and only the performing roles they share.
            if not any(n and n in _fold(r["performer"]) for n in names):
                continue
            roles &= PERFORMING
        if pack.unit == "recording":
            _recording(store, pack, claim, maestro, r, roles, "issued_recording" if r["issued"] else "unissued_recording")
            continue
        # A canon of works: a master the maestro plays on is a performance (its own kind of unit);
        # a master of a piece the maestro wrote is a sighting of that work, keyed by its title.
        if roles & PERFORMING & in_scope:
            _recording(store, pack, claim, maestro, r, roles & PERFORMING, "performance_recording")
        written = roles & WRITING & in_scope
        if written and r["title"]:
            uid = store.find_or_create_by_xref(pack.unit, SOURCE, "title:" + re.sub(r"\W+", " ", r["title"].lower()).strip(),
                                               r["title"])
            claim(uid, "title", r["title"])
            if r["year"]:
                claim(uid, "recorded_year", r["year"])
            claim(uid, "recorded_as", r["matrix"])
            for role in sorted(written):
                claim(uid, "credit", {"role": role}, ref=maestro)


def _recording(store, pack, claim, maestro, r, roles, kind) -> None:
    uid = store.find_or_create_by_xref(pack.unit, SOURCE, f"matrix:{r['company']}:{r['matrix']}", r["title"])
    claim(uid, "title", r["title"])
    claim(uid, "matrix", r["matrix"])
    claim(uid, "label", r["company"])
    if r["date"]:
        claim(uid, "date", r["date"])
    if r["year"]:
        claim(uid, "year", r["year"])
    if r["performer"]:
        claim(uid, "primary_performer", r["performer"])
    if r["description"]:
        claim(uid, "description", r["description"])
    if r["url"]:
        claim(uid, "dahr_url", r["url"])
    claim(uid, "work_kind", kind)
    for role in sorted(roles):
        claim(uid, "credit", {"role": role}, ref=maestro)
