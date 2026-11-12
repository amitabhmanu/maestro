"""MusicBrainz web service (ws/2): works the maestro wrote (composer, lyricist, ...), for canons whose unit is the work.

Seed with `works_url(mbid)`; each page queues the next. A work becomes a unit (keyed `work:<id>`)
when MusicBrainz credits the maestro on it in one of the pack's in-scope roles. A movement or other
part of a larger work is skipped: the larger work is the unit. MusicBrainz data is CC0; keep to the
API's one request per second (`api_rps` on the pack's musicbrainz source).
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlencode, urlsplit

SOURCE = "musicbrainz"
API = "https://musicbrainz.org/ws/2/"
INC = "artist-rels+work-rels"
ROLE = {"composer": "composer", "lyricist": "lyricist", "writer": "composer", "arranger": "arranger",
        "librettist": "lyricist", "orchestrator": "orchestrator"}


def works_url(artist_mbid: str, offset: int = 0) -> str:
    query = urlencode({"artist": artist_mbid, "limit": 100, "offset": offset, "fmt": "json"})
    return f"{API}work?{query}&inc={INC}"


def parse(data: dict, artist_mbid: str) -> dict:
    works = []
    for w in data.get("works", []):
        rels = w.get("relations") or []
        if any(r.get("type") == "parts" and r.get("direction") == "backward" for r in rels):
            continue  # a movement: its parent work is the unit
        roles = sorted({ROLE[r["type"]] for r in rels
                        if (r.get("artist") or {}).get("id") == artist_mbid and r.get("type") in ROLE})
        if not roles:
            continue
        years = [int(r["begin"][:4]) for r in rels if (r.get("begin") or "")[:4].isdigit()]
        works.append({"id": w["id"], "title": w.get("title") or "", "type": w.get("type"),
                      "roles": roles, "year": min(years) if years else None})
    return {"works": works, "count": data.get("work-count", 0), "offset": data.get("work-offset", 0),
            "page_size": len(data.get("works", []))}


def extract(ctx) -> None:
    store, pack = ctx.store, ctx.store.pack
    mbid = pack.data["identity"]["ids"]["musicbrainz"]
    if parse_qs(urlsplit(ctx.page.url).query).get("artist", [None])[0] != mbid:
        raise ValueError(f"musicbrainz_works expects a work browse for {mbid}: {ctx.page.url}")
    page = parse(json.loads(ctx.page.text), mbid)
    in_scope = set(pack.data["roles"]["in_scope"])
    maestro = store.maestro_entity()

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="musicbrainz_works", extractor_version="1", raw_page_id=ctx.raw_page_id)

    for w in page["works"]:
        uid = store.find_or_create_by_xref(pack.unit, SOURCE, f"work:{w['id']}", w["title"])
        claim(uid, "title", w["title"])
        if w["type"]:
            claim(uid, "work_type", w["type"])
        if w["year"]:
            claim(uid, "year", w["year"])
        for role in w["roles"]:
            if role in in_scope:
                claim(uid, "credit", {"role": role}, ref=maestro)
        if w["type"] == "Soundtrack":
            claim(uid, "work_kind", "film_score")  # the pack's canon_model.kind_tiers may tier it

    nxt = page["offset"] + page["page_size"]
    if page["page_size"] and nxt < page["count"]:
        ctx.enqueue(works_url(mbid, nxt), "musicbrainz_works", ctx.job.get("purpose") or "spine")
