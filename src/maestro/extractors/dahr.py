"""Discography of American Historical Recordings (DAHR, UC Santa Barbara): a name page's recordings.

DAHR's firewall refuses cloud machines, so the page is saved by hand (preferably the "View All"
listing, `...?Matrix_page=100000`) and loaded with `maestro-import --extractor dahr`.

Each row of the Recordings table is one master: company, matrix number, first recording date,
title, primary performer, description and the maestro's roles on it. A row becomes a unit keyed
`matrix:<company>:<matrix>` with title, date, year, `matrix`, and `work_kind` issued_recording or
unissued_recording (the pack's `canon_model.kind_tiers` can tier these). The maestro is credited in
each role DAHR gives, mapped to pack roles (vocalist -> singer, composer/songwriter -> composer, ...).
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


def extract(ctx) -> None:
    store, pack = ctx.store, ctx.store.pack
    maestro = store.maestro_entity()

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="dahr", extractor_version="1", raw_page_id=ctx.raw_page_id)

    for r in parse(ctx.page.text):
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
        claim(uid, "work_kind", "issued_recording" if r["issued"] else "unissued_recording")
        for role in sorted({ROLE[x] for x in r["roles"] if x in ROLE}):
            claim(uid, "credit", {"role": role}, ref=maestro)
