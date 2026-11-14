"""Discogs API: every release the maestro appears on, with tracks, catalogue numbers and credits.

Seed with `artist_releases_url(artist_id)`. Each listing page queues the next one and, for every
entry, one release: a master's main release, or the release itself. Unofficial releases are skipped.

For each track credited to the maestro (as a track artist, in the track's credits, or in a release
credit that covers the track) it writes a unit with its title, a credit to the maestro, the release
(`on_release`), its catalogue numbers (`catalogue`, which resolution uses to match units across
sources) and the other people credited on the track (composer, lyricist, singer). A track is keyed by
master and title, so the reissues of one record add sightings of one unit rather than new units.
Releases in Discogs' "Stage & Screen" genre are soundtracks: they become containers (keyed by
master), so their tracks can be matched to a film's song list.

Discogs release data is CC0. The API allows 25 requests a minute without a token and 60 with one;
the pack's source sets `api_rps` and `api_auth` (token from DISCOGS_TOKEN in the environment or
.env, never the pack).
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from maestro.resolve import fold

SOURCE = "discogs"
API = "https://api.discogs.com/"
SKIP_ROLES = {"UnofficialRelease"}
SOUNDTRACK_GENRES = {"Stage & Screen"}

# Discogs credit roles (without their [bracketed] detail) -> canon roles.
ROLES = {
    "vocals": "singer", "lead vocals": "singer", "voice": "singer", "featuring": "singer",
    "music by": "composer", "music director": "composer", "composed by": "composer", "composer": "composer", "written-by": "composer",
    "lyrics by": "lyricist", "words by": "lyricist", "lyricist": "lyricist",
    "arranged by": "arranger", "orchestrated by": "arranger", "conductor": "conductor",
    "piano": "instrumentalist", "organ": "instrumentalist", "harpsichord": "instrumentalist",
}


def artist_releases_url(artist_id: str | int, page: int = 1) -> str:
    return f"{API}artists/{artist_id}/releases?" + urlencode({"sort": "year", "per_page": 100, "page": page})


def release_url(release_id: str | int) -> str:
    return f"{API}releases/{release_id}"


def canon_role(discogs_role: str) -> str | None:
    """'Vocals [Playback]' -> singer; roles the canon does not track -> None."""
    base = re.sub(r"\[.*?\]", "", discogs_role or "").strip().lower()
    return ROLES.get(base)


_PART = re.compile(r"\s*[-–]\s*(?:[0-9०-९]+|part\s*[0-9]+)\s*$", re.I)
_FILM_PREFIX = re.compile(r"^[^-–:]*\bfilms?\b\s*[-–:]\s*", re.I)


def clean_track_title(title: str) -> str:
    """'ओ दुनिया के रखवाले - १' -> 'ओ दुनिया के रखवाले': the two sides of a long song are one song."""
    return _PART.sub("", (title or "").strip()).strip()


def clean_release_title(title: str) -> str:
    """'Hindustani Film - Baiju Bawra = बैजु बावरा' -> 'Baiju Bawra': the film's name, without the
    label's "... Film -" prefix or Discogs' " = " translation."""
    title = (title or "").split(" = ")[0].strip()
    return _FILM_PREFIX.sub("", title).strip() or title


def _name(a: dict) -> str:
    return re.sub(r"\s\(\d+\)$", "", a.get("anv") or a.get("name") or "").strip()  # "Rafi (2)" -> "Rafi"


def _artist_line(artists: list[dict]) -> str:
    out = ""
    for a in artists:
        join = (a.get("join") or "").strip()
        out += _name(a) + (f" {join} " if join and join != "," else ", " if join == "," else " ")
    return re.sub(r"\s+", " ", out).strip(" ,")


def _flatten(tracklist: list[dict]) -> list[dict]:
    """Playable tracks in order: headings dropped, index tracks replaced by their sub-tracks."""
    out = []
    for t in tracklist or []:
        if t.get("type_") == "heading":
            continue
        if t.get("sub_tracks"):
            out.extend(s for s in t["sub_tracks"] if s.get("type_", "track") == "track")
        elif t.get("type_", "track") == "track":
            out.append(t)
    return out


def covered(spec: str, positions: list[str]) -> set[str]:
    """Positions a release credit's `tracks` covers ("A1 to A3, B2"); all of them when empty."""
    if not (spec or "").strip():
        return set(positions)
    out: set[str] = set()
    for part in spec.split(","):
        part = part.strip()
        if " to " in part:
            a, b = (x.strip() for x in part.split(" to ", 1))
            if a in positions and b in positions:
                i, j = positions.index(a), positions.index(b)
                out.update(positions[min(i, j):max(i, j) + 1])
                continue
        out.add(part)
    return out


def parse_listing(data: dict) -> dict:
    """Release ids to fetch from one page of `artists/<id>/releases`, and the next page if any."""
    ids = []
    for r in data.get("releases", []):
        if r.get("role") in SKIP_ROLES:
            continue
        rid = r.get("main_release") if r.get("type") == "master" else r.get("id")
        if rid:
            ids.append(int(rid))
    pg = data.get("pagination") or {}
    page, pages = pg.get("page", 1), pg.get("pages", 1)
    return {"releases": list(dict.fromkeys(ids)), "next": page + 1 if page < pages else None}


def parse_release(data: dict, artist_id: int) -> dict:
    """The tracks of one release that credit the maestro, with their credits and the release's facts."""
    tracks = _flatten(data.get("tracklist"))
    positions = [t.get("position") or "" for t in tracks]
    rel_artists = data.get("artists") or []
    rel_main = any(a.get("id") == artist_id for a in rel_artists)
    rel_credits = [(c, covered(c.get("tracks", ""), positions)) for c in data.get("extraartists") or []]
    soundtrack = bool(SOUNDTRACK_GENRES & set(data.get("genres") or []))
    catalogues = sorted({lab["catno"].strip() for lab in data.get("labels") or []
                         if lab.get("catno") and lab["catno"].strip().lower() not in ("none", "")})
    out = []
    for t, pos in zip(tracks, positions):
        artists = t.get("artists") or []
        credits: list[dict[str, Any]] = []
        maestro_roles: set[str] = set()
        # Main artists stay in artist_credit only: on a soundtrack the release artist is often the
        # composer, so a role is taken only from explicit credits.
        main = any(a.get("id") == artist_id for a in artists) if artists else rel_main
        for c in t.get("extraartists") or []:
            credits.append({"id": c.get("id"), "name": _name(c), "role": c.get("role", "")})
        for c, cover in rel_credits:
            if pos in cover:
                credits.append({"id": c.get("id"), "name": _name(c), "role": c.get("role", "")})
        for c in credits:
            if c["id"] == artist_id:
                role = canon_role(c["role"])
                if role:
                    maestro_roles.add(role)
        if not main and not maestro_roles:
            continue
        out.append({
            "position": pos,
            "title": clean_track_title(t.get("title") or ""),
            "artist_credit": _artist_line(artists or rel_artists),
            "main": main,
            "maestro_roles": sorted(maestro_roles),
            "credits": [c for c in credits if c["id"] != artist_id and c["id"] and canon_role(c["role"])],
        })
    return {
        "id": data.get("id"), "title": clean_release_title(data.get("title") or ""), "year": data.get("year") or None,
        "master": data.get("master_id") or None, "soundtrack": soundtrack, "catalogues": catalogues,
        "tracks": [t for t in out if t["title"]],
    }


def unit_key(release: dict, track: dict) -> str:
    """A track's id here: master + folded title (one unit across reissues), else release + position."""
    if release["master"]:
        return f"master:{release['master']}:{fold(track['title'])}"
    return f"release:{release['id']}:{track['position']}"


def _counts(pack_data: dict, track: dict) -> bool:
    """Whether a track credits the maestro in a role the canon covers (see `extract`)."""
    primary = pack_data["roles"]["primary"]
    in_scope = set(pack_data["roles"].get("in_scope") or [primary])
    source = next((x for x in pack_data.get("sources", []) if x["id"] == SOURCE), {})
    main_role = source.get("main_role", primary)
    return bool((set(track["maestro_roles"]) | ({main_role} if track["main"] and main_role else set())) & in_scope)


def _artist_id(pack_data: dict) -> int:
    did = (pack_data["identity"].get("ids") or {}).get("discogs")
    if not did:
        raise ValueError("the discogs extractor needs identity.ids.discogs in the pack")
    return int(did)


def extract(ctx) -> None:
    store, pack = ctx.store, ctx.store.pack
    artist = _artist_id(pack.data)
    parts = urlsplit(ctx.page.url)
    data = json.loads(ctx.page.text)
    purpose = ctx.job.get("purpose") or "spine"

    m = re.fullmatch(r"/artists/(\d+)/releases", parts.path)
    if m:
        if int(m.group(1)) != artist:
            raise ValueError(f"discogs listing is for artist {m.group(1)}, the pack's is {artist}")
        listing = parse_listing(data)
        for rid in listing["releases"]:
            ctx.enqueue(release_url(rid), "discogs", purpose)
        if listing["next"]:
            ctx.enqueue(artist_releases_url(artist, listing["next"]), "discogs", purpose)
        return
    if not re.fullmatch(r"/releases/\d+", parts.path) or parse_qs(parts.query):
        raise ValueError(f"discogs extractor expects an artist listing or a release: {ctx.page.url}")

    rel = parse_release(data, artist)

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="discogs", extractor_version="1", raw_page_id=ctx.raw_page_id)

    cid = None
    if rel["soundtrack"] and any(_counts(pack.data, t) for t in rel["tracks"]):
        ckey = f"master:{rel['master']}" if rel["master"] else f"release:{rel['id']}"
        cid = store.find_or_create_by_xref("container", SOURCE, ckey, rel["title"])
        claim(cid, "title", rel["title"])
        if rel["year"]:
            claim(cid, "year", rel["year"])
    maestro = store.maestro_entity()
    primary = pack.data["roles"]["primary"]
    in_scope = set(pack.data["roles"].get("in_scope") or [primary])
    source = next((x for x in pack.data.get("sources", []) if x["id"] == SOURCE), {})
    main_role = source.get("main_role", primary)
    people: dict[int, int] = {}
    for t in rel["tracks"]:
        # Only tracks that credit the maestro in a role the canon covers become units: a composer's
        # own records hold other people's works.
        roles = sorted((set(t["maestro_roles"]) | ({main_role} if t["main"] and main_role else set())) & in_scope)
        if not roles:
            continue
        uid = store.find_or_create_by_xref(pack.unit, SOURCE, unit_key(rel, t), t["title"])
        claim(uid, "title", t["title"])
        for role in roles:
            claim(uid, "credit", {"role": role}, ref=maestro)
        if t["artist_credit"]:
            claim(uid, "artist_credit", t["artist_credit"])
        claim(uid, "on_release", f"discogs:{rel['id']}")
        for cat in rel["catalogues"]:
            claim(uid, "catalogue", cat)
        if rel["year"]:
            claim(uid, "release_year", rel["year"])
        if cid:
            claim(uid, "part_of", {}, ref=cid)
        for c in t["credits"]:
            if c["id"] not in people:
                people[c["id"]] = store.find_or_create_by_xref("person", SOURCE, f"artist:{c['id']}", c["name"])
            claim(uid, "credit", {"role": canon_role(c["role"])}, ref=people[c["id"]])
