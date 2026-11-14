"""Last.fm: how many people listen to each of the maestro's tracks today (`lastfm` mentions).

Seed with `top_tracks_url(artist)` for each name Last.fm files the maestro under (with autocorrect
on, spellings such as "Mohd. Rafi" resolve to one artist, so one seed is often enough). Each page of artist.getTopTracks lists up to
1000 tracks with their listeners and play counts, and queues the next page. Every track becomes a
`lastfm` mention with strength = listeners. Label uploads often name the film or album as
`Title (From "Film")` or `Title - From "Film"`: that part becomes the mention's container, so matching can use it.

The API needs a free key (last.fm/api), read from LASTFM_API_KEY in the environment or .env and sent
as the `api_key` query parameter on the request only (the pack's source sets `api_auth.param`).
Last.fm asks for no more than five requests a second; the pack's `api_rps` keeps well under that.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from maestro.popularity import record_mention

SOURCE = "lastfm"
API = "https://ws.audioscrobbler.com/2.0/"
PAGE_SIZE = 1000


def top_tracks_url(artist: str, page: int = 1) -> str:
    return API + "?" + urlencode({"method": "artist.gettoptracks", "artist": artist, "autocorrect": 1,
                                  "limit": PAGE_SIZE, "page": page, "format": "json"})


def split_title(name: str) -> tuple[str, str | None]:
    """'Chaudhvin Ka Chand (From "Chaudhvin Ka Chand")' -> ('Chaudhvin Ka Chand', 'Chaudhvin Ka Chand').

    Also 'Title - From "Film"', the other way label uploads name the film."""
    container = None
    m = re.search(r"""\s*[\(\[]\s*From\s+(?:the\s+(?:film|movie)\s+)?["“']?([^"”'\)\]]+?)["”']?\s*[\)\]]""", name, re.I)
    if m:
        container = m.group(1).strip()
        name = name[:m.start()] + name[m.end():]
    else:  # 'Gulabi Ankhen - From "The Train"'
        m = re.search(r"""\s+[-–—]\s+From\s+(?:the\s+(?:film|movie)\s+)?["“']?([^"”']+?)["”']?\s*$""", name, re.I)
        if m:
            container = m.group(1).strip()
            name = name[:m.start()]
    # Version notes that are not part of the title.
    name = re.sub(r"\s*[\(\[](?:[^\)\]]*\b(?:remaster(?:ed)?|version|revival|live|mono|stereo|jhankar|sad|happy)\b[^\)\]]*)[\)\]]", "",
                  name, flags=re.I)
    name = re.sub(r"\s+-\s+(?:remaster(?:ed)?|live|mono|stereo).*$", "", name, flags=re.I)
    return name.strip(" -"), container


def parse(text: str) -> dict:
    data = json.loads(text)
    if "error" in data:
        raise ValueError(f"Last.fm error {data['error']}: {data.get('message')}")
    top = data.get("toptracks") or {}
    attr = top.get("@attr") or {}
    tracks = top.get("track") or []
    if isinstance(tracks, dict):
        tracks = [tracks]
    out = []
    for t in tracks:
        title, container = split_title(t.get("name", ""))
        if not title:
            continue
        out.append({"title": title, "container": container, "listeners": int(t.get("listeners") or 0),
                    "playcount": int(t.get("playcount") or 0), "url": t.get("url"), "raw": t.get("name")})
    return {"artist": attr.get("artist"), "page": int(attr.get("page") or 1),
            "pages": int(attr.get("totalPages") or 1), "tracks": out}


def extract(ctx) -> None:
    data = parse(ctx.page.text)
    q = parse_qs(urlsplit(ctx.page.url).query)
    if data["page"] < data["pages"] and q.get("artist"):
        ctx.enqueue(top_tracks_url(q["artist"][0], data["page"] + 1), "lastfm", ctx.job.get("purpose") or "enrich")
    for t in data["tracks"]:
        if t["listeners"] <= 0:
            continue
        record_mention(ctx, SOURCE, "lastfm", t["title"], strength=t["listeners"], container=t["container"],
                       url=t["url"], extractor="lastfm", playcount=t["playcount"], as_listed=t["raw"],
                       note=f"{t['listeners']:,} Last.fm listeners")
