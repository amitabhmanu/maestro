"""YouTube: how many times each of the maestro's songs has been watched (`youtube` mentions).

Searching costs 100 of the API's 10,000 daily quota units, so this extractor never searches. It reads
the uploads of the channels that publish the maestro's catalogue (record labels), where a page of 50
videos costs one unit:

1. Seed with `channel_url(handle)` (e.g. "@saregamamusic"): channels.list gives the channel's uploads
   playlist, and its first page is queued.
2. Each playlistItems page queues the next one, and one videos.list call (one unit) for those of its
   50 videos whose title or description names the maestro (any `identity.variants` spelling).
3. Each video becomes a `youtube` mention with strength = views. Label titles look like
   "Title | Film | Singer | Composer" or "Title - Singer | Film"; descriptions often carry
   "Film: X" or "Movie: X". The film, when found, becomes the mention's container.

A label with 100,000 uploads costs about 2,000 units for the playlist and fewer for the videos. The key
is read from YOUTUBE_API_KEY and sent as the `key` query parameter on the request only (the pack's
source sets `api_auth.param`), so it never appears in a stored URL.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from maestro.popularity import names_maestro, record_mention

SOURCE = "youtube"
API = "https://www.googleapis.com/youtube/v3/"


def channel_url(handle: str) -> str:
    return API + "channels?" + urlencode({"part": "contentDetails", "forHandle": handle})


def playlist_url(playlist: str, page_token: str | None = None) -> str:
    q = {"part": "snippet", "playlistId": playlist, "maxResults": 50}
    if page_token:
        q["pageToken"] = page_token
    return API + "playlistItems?" + urlencode(q)


def videos_url(ids: list[str]) -> str:
    return API + "videos?" + urlencode({"part": "snippet,statistics", "id": ",".join(ids)})


SEP = re.compile(r"\s+[|｜]\s+|\s+[-–—]\s+")
NOISE = re.compile(r"\b(?:full\s+(?:video\s+)?song|lyrical|audio|video|hd|4k|remaster(?:ed)?|official|jukebox|"
                   r"old\s+is\s+gold|evergreen|hits?|classic|song|songs)\b", re.I)


def split_title(title: str, description: str = "") -> tuple[str, str | None]:
    """'Chahoonga Main Tujhe | Dosti | Mohammed Rafi | Laxmikant-Pyarelal' -> ('Chahoonga Main Tujhe', 'Dosti')."""
    film = None
    m = re.search(r"^\s*(?:Film|Movie|Album)\s*[:\-–]\s*(.+?)\s*(?:\(\d{4}\))?\s*$", description or "", re.I | re.M)
    if m:
        film = m.group(1).strip()
    m = re.search(r"""[\(\[]\s*(?:From\s+)?["“']?([^"”'\)\]]+?)["”']?\s*(?:\(\d{4}\))?\s*[\)\]]""", title)
    parts = [p.strip() for p in SEP.split(title) if p.strip()]
    name = parts[0] if parts else title.strip()
    if m and not NOISE.fullmatch(m.group(1).strip()) and m.start() > 0 and m.start() <= len(name):
        film = film or m.group(1).strip()
        name = title[:m.start()].strip()
    elif not film and len(parts) > 1 and not NOISE.search(parts[1]):
        film = parts[1]
    name = re.sub(r"[\(\[][^\)\]]*[\)\]]", "", name).strip(" -|'\"")
    if film:
        film = re.sub(r"\s*\(\d{4}\)$", "", film).strip()
    return name, film


def extract(ctx) -> None:
    data = json.loads(ctx.page.text)
    if "error" in data:
        err = data["error"]
        raise ValueError(f"YouTube error {err.get('code')}: {err.get('message')}")
    kind = urlsplit(ctx.page.url).path.rsplit("/", 1)[-1]
    q = parse_qs(urlsplit(ctx.page.url).query)
    purpose = ctx.job.get("purpose") or "enrich"
    if kind == "channels":
        for item in data.get("items", []):
            uploads = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            if uploads:
                ctx.enqueue(playlist_url(uploads), "youtube", purpose)
        return
    if kind == "playlistItems":
        if data.get("nextPageToken"):
            ctx.enqueue(playlist_url(q["playlistId"][0], data["nextPageToken"]), "youtube", purpose)
        ids = []
        for item in data.get("items", []):
            sn = item.get("snippet", {})
            vid = sn.get("resourceId", {}).get("videoId")
            if vid and names_maestro(ctx.store, f"{sn.get('title', '')} {sn.get('description', '')}"):
                ids.append(vid)
        if ids:
            ctx.enqueue(videos_url(ids), "youtube", purpose)
        return
    for item in data.get("items", []):  # videos
        sn, st = item.get("snippet", {}), item.get("statistics", {})
        views = int(st.get("viewCount") or 0)
        title, film = split_title(sn.get("title", ""), sn.get("description", ""))
        if views <= 0 or not title:
            continue
        record_mention(ctx, SOURCE, "youtube", title, strength=views, container=film,
                       url=f"https://www.youtube.com/watch?v={item.get('id')}", extractor="youtube",
                       channel=sn.get("channelTitle"), as_listed=sn.get("title"),
                       note=f"{views:,} YouTube views ({sn.get('channelTitle')})")
