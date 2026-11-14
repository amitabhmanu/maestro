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
   "Film: X" or "Movie: X". The film, when found, becomes the mention's container. Compilations,
   shows, covers and ads (`is_song`) are skipped, and so is a video whose title and singer line do
   not name the maestro (`sung_by_maestro`).

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


SEP = re.compile(r"\s*[|｜]\s*|\s+[-–—]\s+")
NOISE = re.compile(r"\b(?:full\s+(?:video\s+)?songs?|full\s+video|with\s+lyrics|lyrics?|lyrical|audio|video|hd|4k|"
                   r"remaster(?:ed)?|official|old\s+is\s+gold|evergreen|classic|songs?|bollywood|hindi|retro)\b", re.I)
# Videos that are not one recording of one song: compilations, shows, covers and ads.
NOT_A_SONG = re.compile(r"\b(?:jukebox|best\s+of|top\s+\d+|\d+\s+(?:rare\s+|best\s+|hit\s+|non[- ]?stop\s+)?songs|"
                        r"songs\s+of|hits\s+of|non[- ]?stop|medley|mash[- ]?up|(?:party|dance|dj)\s+mix|mix\s+vol|vol(?:ume)?\.?\s*\d+|"
                        r"radio|show|geetmala|season\s+\d+|episode|live\s+(?:concert|performance|in)|concert|recreat(?:ed|ion)|"
                        r"cover|unplugged|tribute|karaoke|instrumental|reprise|trailer|teaser|promo|interview|carvaan)\b|#", re.I)
SINGER_LINE = re.compile(r"^\s*(?:singers?|artists?|vocals?|sung\s+by|voice)\s*[:\-–]\s*(.+)$", re.I | re.M)
FILM_LINE = re.compile(r"^\s*(?:film|movie|album)(?:\s+name)?\s*[:\-–]\s*(.+?)\s*$", re.I | re.M)
DEVANAGARI = re.compile(r"[\u0900-\u097F]")


def is_song(title: str) -> bool:
    """False for jukeboxes, 'best of' and 'top N' lists, mixes, radio and TV shows, concerts, covers and ads."""
    return not NOT_A_SONG.search(title)


def sung_by_maestro(store, title: str, description: str) -> bool:
    """The title names the maestro, or the description's singer line does (a name elsewhere in the
    description is often another song's credit or a playlist plug)."""
    if names_maestro(store, title):
        return True
    return any(names_maestro(store, m.group(1)) for m in SINGER_LINE.finditer(description or ""))


def _clean(part: str) -> str:
    part = re.sub(r"[\(\[][^\)\]]*[\)\]]", "", part)
    part = NOISE.sub("", part)
    return re.sub(r"\s{2,}", " ", part).strip(" -|:'\"")


def split_title(title: str, description: str = "") -> tuple[str, str | None]:
    """'Chahoonga Main Tujhe | Dosti | Mohammed Rafi | Laxmikant-Pyarelal' -> ('Chahoonga Main Tujhe', 'Dosti').

    The film comes from the description's 'Film:' line when there is one, else from a bracketed
    '(From "Film")', else from the title's second part. Parts in Devanagari, and parts that are only
    noise ('Full Video', 'with lyrics', 'Old is Gold'), are skipped."""
    film = None
    m = FILM_LINE.search(description or "")
    if m:
        film = m.group(1)
    b = re.search(r"""[\(\[]\s*From\s+(?:the\s+(?:film|movie)\s+)?["“']?([^"”'\)\]]+?)["”']?\s*[\)\]]""", title, re.I)
    if b:
        film = film or b.group(1)
        title = title[:b.start()] + title[b.end():]
    parts = [c for c in (_clean(p) for p in SEP.split(title) if not DEVANAGARI.search(p)) if c]
    name = parts[0] if parts else _clean(title)
    if not film and len(parts) > 1:
        film = parts[1]
    if film:
        film = re.sub(r"\s*\(\d{4}\)$", "", film).strip(" :-'\"")
    return name, film or None


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
        raw_title, desc = sn.get("title", ""), sn.get("description", "")
        if views <= 0 or not is_song(raw_title) or not sung_by_maestro(ctx.store, raw_title, desc):
            continue
        title, film = split_title(raw_title, desc)
        if not title:
            continue
        record_mention(ctx, SOURCE, "youtube", title, strength=views, container=film,
                       url=f"https://www.youtube.com/watch?v={item.get('id')}", extractor="youtube",
                       channel=sn.get("channelTitle"), as_listed=sn.get("title"),
                       note=f"{views:,} YouTube views ({sn.get('channelTitle')})")
