"""Songs of Yore posts (and posts on other expert blogs) about Rafi's songs: expert opinion for gems.

songsofyore.com's robots.txt shuts out AI crawlers, so its pages are never crawled: the user saves
a post ("Webpage, HTML only") and it is imported with `maestro-import --extractor songsofyore --url
<post url> <file>`. Saved copies live in manual-imports/rafi/expert/.

A post lists its songs as numbered lines, in one of these shapes:
  "1. Title – Film (1968) – Lyrics: ... – Music: ..."
  "2. Title – [duet with X –] Film – 1952 – composer – lyricist – actor"
  "3. Music Composer X: Title from the film Film (1956), lyrics by ..."
  "4. Title [by Rafi and X] from Film (1951), lyrics ..."
  "5. 1946 – Title [(duet with X)] – Film – composer – lyricist – actor"
  "6. Title (Film, 1957): actor ..." (Dustedoff)
Each song becomes a `songsofyore` esteem mention whose voice (`expert`) is the post's author (a
guest author when the post names one, else the site's author) and whose framing comes from the
post's title: posts about hidden, less heard, rare, forgotten or unrecognised songs single their
songs out as gems; other posts ("best songs of ...") praise them. `--opt framing=gem|praise|listed`
overrides. Only the song, its film and year and the post's title are kept, not the post's text.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urlsplit

from maestro.popularity import record_mention

SOURCE = "songsofyore"
# Other expert blogs, by host: (source id, signal, the blog's author when the page does not name one).
BLOGS = {"songsofyore.com": (SOURCE, "songsofyore", None),
         "madhulikaliddle.com": ("dustedoff", "expert_posts", "Madhulika Liddle"),
         "anuradhawarrier.blogspot.com": ("anuradhawarrier", "expert_posts", "Anuradha Warrier")}
GEM_WORDS = re.compile(r"\b(gems?|hidden|less[- ]heard|rare|forgotten|unrecognised|unrecognized|unsung|obscure|"
                       r"underrated|lesser[- ]known)\b", re.I)
DASH = r"\s+[–—-]\s+|\s*[–—]\s*"


def _text(html: str) -> str:
    html = re.sub(r"<br\s*/?>|</p>|</h\d>|</li>|</div>", "\n", html)
    return unescape(re.sub(r"<[^>]+>", "", html))


def _body(page: str) -> str:
    i = page.find('class="entry-content')
    j = page.find('id="comments"', i if i >= 0 else 0)
    return page[i if i >= 0 else 0: j if j > 0 else len(page)]


def parse_line(line: str) -> dict | None:
    m = re.match(r"\s*(\d{1,3})\.\s*(.+)$", line)
    if not m:
        return None
    n, rest = int(m.group(1)), m.group(2).strip()
    c = re.match(r"(?:Music Composer|Composer|Music)\s+[^:]{2,40}:\s*(.+?)\s+from the film\s+(.+?)\s*\((\d{4})\)", rest, re.I)
    if c:
        return {"n": n, "title": c.group(1).strip(" '\"‘’“”"), "film": c.group(2).strip(), "year": int(c.group(3))}
    f = re.match(r"(.+?)\s+(?:by\s+[^()]{2,80}?\s+)?from\s+(?:the film\s+)?(.+?)\s*\((\d{4})\)", rest)
    if f and not re.search(DASH, f.group(1)):
        return {"n": n, "title": f.group(1).strip(" '\"‘’“”"), "film": f.group(2).strip(), "year": int(f.group(3))}
    f = re.match(r"(.+?)\s*\(([^,()]+),\s*(\d{4})\)", rest)  # "Title (Film, 1957): actor"
    if f and not re.search(DASH, f.group(1)):
        return {"n": n, "title": f.group(1).strip(" '\"‘’“”"), "film": f.group(2).strip(), "year": int(f.group(3))}
    parts = [p.strip() for p in re.split(DASH, rest) if p.strip()]
    if len(parts) >= 3 and re.fullmatch(r"\d{4}", parts[0]):  # "1946 – Title – Film – composer – ..."
        return {"n": n, "title": re.sub(r"\s*\((?:duet )?with [^)]*\)", "", parts[1]).strip(), "film": parts[2],
                "year": int(parts[0])}
    if len(parts) < 2:
        return None
    title, rest_parts = parts[0], [p for p in parts[1:] if not re.match(r"(duet )?with\b", p, re.I)]
    if not rest_parts:
        return None
    f = re.match(r"(.+?)\s*\((\d{4})\)", rest_parts[0])
    if f:
        return {"n": n, "title": title, "film": f.group(1).strip(), "year": int(f.group(2))}
    y = re.match(r"-?\s*(\d{4})\b", rest_parts[1]) if len(rest_parts) > 1 else None
    film_year = re.match(r"(.+?)\s*-\s*(\d{4})$", rest_parts[0])  # "Baradari -1955"
    if film_year:
        return {"n": n, "title": title, "film": film_year.group(1).strip(), "year": int(film_year.group(2))}
    if y:
        return {"n": n, "title": title, "film": rest_parts[0], "year": int(y.group(1))}
    return None


def parse(page: str) -> dict:
    title = re.search(r"<title>([^<|]+)", page)
    post = re.sub(r"\s+[–—-]\s+Dustedoff$", "", unescape(title.group(1)).strip()) if title else ""
    text = _text(_body(page))
    guest = re.search(r"[Gg]uest article by ([A-Z][\w.]*(?: [A-Z][\w.]*){0,3})", text)
    author = re.search(r'<meta name="author" content="([^"]+)"', page)
    songs, seen = [], set()
    for line in text.split("\n"):
        s = parse_line(line)
        if s:
            s["title"] = re.sub(r",\s+in\s+(?:raga\s+)?[A-Z][\w ]{2,25}$", "", s["title"])  # "..., in Malkauns"
        if s and s["n"] not in seen and len(s["title"]) <= 120:
            seen.add(s["n"])
            songs.append(s)
    expert = guest.group(1) if guest else (author.group(1) if author else SOURCE)
    expert = re.sub(r"\s+[A-Z]\.?(?=\s)", "", expert)  # "Ashok M Vaishnav" and "Ashok Vaishnav" are one voice
    return {"post": post, "expert": expert,
            "framing": "gem" if GEM_WORDS.search(post) else "praise", "songs": songs}


def extract(ctx) -> None:
    data = parse(ctx.page.text)
    host = re.sub(r"^www\.", "", urlsplit(ctx.page.url).netloc)
    source, signal, blog_author = BLOGS.get(host, (SOURCE, "songsofyore", None))
    expert = data["expert"] if data["expert"] not in (SOURCE, None) or not blog_author else blog_author
    framing = ctx.options.get("framing") or data["framing"]
    site = "Songs of Yore" if source == SOURCE else host
    for s in data["songs"]:
        record_mention(ctx, source, signal, s["title"], strength=1, year=s["year"], container=s["film"],
                       expert=expert, framing=framing, url=ctx.page.url, extractor="songsofyore",
                       note=f"{expert}, {site}: “{data['post']}”")
