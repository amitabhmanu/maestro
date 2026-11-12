"""MusicBrainz web service (ws/2): every release on which the maestro is a track artist.

Seed with `release_url(mbid)`; each page queues the next. For each track credited to the maestro
it writes a unit (keyed by the MusicBrainz recording id) with its title and a credit to the maestro
in the pack's primary role. Releases whose release group is a soundtrack become containers (keyed by
release group), so their recordings can be matched to a film's song list. Compilations add a
sighting of the recording but no container.

MusicBrainz data is CC0. The API asks for at most one request per second and a descriptive
User-Agent; set `api_rps` on the pack's musicbrainz source.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlencode, urlsplit

SOURCE = "musicbrainz"
API = "https://musicbrainz.org/ws/2/"
INC = "recordings+release-groups+artist-credits"


def release_url(artist_mbid: str, offset: int = 0) -> str:
    query = urlencode({"track_artist": artist_mbid, "limit": 100, "offset": offset, "fmt": "json"})
    return f"{API}release?{query}&inc={INC}"


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


def parse(data: dict, artist_mbid: str) -> dict:
    """Containers and tracks credited to the artist in one page of `release?track_artist=`."""
    containers, tracks = {}, []
    for rel in data.get("releases", []):
        rg = rel.get("release-group") or {}
        soundtrack = "Soundtrack" in (rg.get("secondary-types") or [])
        if soundtrack and rg.get("id"):
            containers[rg["id"]] = {"title": rg.get("title") or rel.get("title"),
                                    "year": _year(rg.get("first-release-date") or rel.get("date"))}
        for medium in rel.get("media") or []:
            for t in medium.get("tracks") or []:
                credit = t.get("artist-credit") or (t.get("recording") or {}).get("artist-credit") or []
                if not any((c.get("artist") or {}).get("id") == artist_mbid for c in credit):
                    continue
                rec = t.get("recording") or {}
                tracks.append({
                    "recording": rec.get("id"),
                    "title": rec.get("title") or t.get("title"),
                    "track_title": t.get("title"),
                    "credit": "".join(c.get("name", "") + c.get("joinphrase", "") for c in credit).strip(),
                    "release": rel.get("id"),
                    "container": rg.get("id") if soundtrack else None,
                })
    return {"containers": containers, "tracks": [t for t in tracks if t["recording"]],
            "count": data.get("release-count", 0), "offset": data.get("release-offset", 0),
            "page_size": len(data.get("releases", []))}


def extract(ctx) -> None:
    store, pack = ctx.store, ctx.store.pack
    mbid = pack.data["identity"]["ids"]["musicbrainz"]
    q = parse_qs(urlsplit(ctx.page.url).query)
    if q.get("track_artist", [None])[0] != mbid:
        raise ValueError(f"musicbrainz extractor expects a release browse for {mbid}: {ctx.page.url}")
    page = parse(json.loads(ctx.page.text), mbid)

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="musicbrainz", extractor_version="1", raw_page_id=ctx.raw_page_id)

    cids = {}
    for rg, c in page["containers"].items():
        cid = store.find_or_create_by_xref("container", SOURCE, f"release-group:{rg}", c["title"])
        claim(cid, "title", c["title"])
        if c["year"]:
            claim(cid, "year", c["year"])
        cids[rg] = cid
    maestro = store.maestro_entity()
    role = pack.data["roles"]["primary"]
    for t in page["tracks"]:
        uid = store.find_or_create_by_xref(pack.unit, SOURCE, t["recording"], t["title"])
        claim(uid, "title", t["title"])
        claim(uid, "credit", {"role": role}, ref=maestro)
        claim(uid, "artist_credit", t["credit"])
        claim(uid, "on_release", t["release"])
        if t["container"]:
            claim(uid, "part_of", {}, ref=cids[t["container"]])

    nxt = page["offset"] + page["page_size"]
    if page["page_size"] and nxt < page["count"]:
        ctx.enqueue(release_url(mbid, nxt), "musicbrainz", ctx.job.get("purpose") or "spine")
