"""English Wikipedia film articles: the songs of films a register already knows (track listings).

`film_url(article)` is a film's article. Its Soundtrack, Music or Songs section gives:

  - track listing tables ({{Track listing}}, class "tracklist") and song tables (a wikitable with a Song or
    Title column and a Singer column): each row becomes a song (source `wikipedia`, xref
    `<article>#<n>`, in order across the tables) with `title`, `part_of` the film, `credit_unlinked`
    singers, lyricists and composers when the table has those columns, `duration`, and `version`
    when the title marks one ("Reprise", "Remix", "Female", "Unplugged"...), the table's caption as
    `album_part` ("Bonus tracks"), and a `note` column when there is one;
  - a "Main article" link to the soundtrack's own article ("Dil Chahta Hai (soundtrack)"): that page is
    queued (the film records it as `soundtrack_article`) and read the same way, with its album infobox
    (`record_company` label, `album_release` date).

Song titles go through the pack's lyrics policy like any title. The film is the container whose
`wikipedia` claim names this article (or whose `soundtrack_article` names the soundtrack page); a page
that matches no register film writes nothing.
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote, unquote, urlsplit

from maestro.wikitable import Table, read_tables

SOURCE = "wikipedia"
VERSION = "1"
BASE = "https://en.wikipedia.org/wiki/"
MUSIC_HEADINGS = re.compile(r"\b(soundtrack|music|songs?|track ?list(?:ing)?|album|musical numbers?)\b", re.I)
_SPLIT = re.compile(r"\s*(?:,|;|\band\b|&|/|\n)\s*")
_VERSION = re.compile(r"\((?:[^()]*\b)?(reprise|remix|remixed|unplugged|female|male|duet|sad|version|club mix|"
                      r"lounge|instrumental|theme|redux|reloaded|lofi|lo-fi|acoustic|recreated|revisited|"
                      r"extended|dance mix|film version|mashup)\b[^()]*\)", re.I)


def film_url(article: str) -> str:
    return BASE + quote(article.replace(" ", "_"), safe="()',!-.:_&")


def article_of(url: str) -> str:
    return unquote(urlsplit(url).path.split("/wiki/", 1)[1]).replace("_", " ")


def _col(headers: list[str], *names: str) -> int | None:
    for i, h in enumerate(headers):
        h = h.lower().strip()
        if any(h == n or h.startswith(n) for n in names):
            return i
    return None


def _names(text: str) -> list[str]:
    text = re.sub(r"\[\d+\]|\[[a-z]\]", "", text or "")
    out = []
    for n in _SPLIT.split(text):
        n = n.strip(" .\u200b")
        if n and len(n) <= 60 and not n.lower().startswith(("feat", "featuring")):
            out.append(re.sub(r"^(?:feat\.?|featuring)\s+", "", n, flags=re.I))
    return out


def _title(text: str) -> str:
    t = re.sub(r"\[\d+\]|\[[a-z]\]", "", text or "").strip()
    m = re.match(r'^"(.+?)"\s*(.*)$', t)
    if m:
        t = m.group(1) + (f" {m.group(2)}" if m.group(2) else "")
    return t.strip().strip('"').strip()


_ALL_LYRICS = re.compile(r"[Aa]ll lyrics (?:are |were )?(?:written|penned|by)(?: by)? (.+?)(?:;|\.(?:\s|$)|$)")
_ALL_MUSIC = re.compile(r"[Aa]ll (?:music|songs|tracks) (?:is |are |was |were )?(?:composed|written|produced|by)(?: by)? "
                        r"(.+?)(?:;|\.(?:\s|$)|$)")
_TAG = re.compile(r"<[^>]+>")


def _plain(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", fragment))).strip()


def _blocks(page_html: str) -> list[tuple[int, str]]:
    """Each rendered {{Track listing}}: its position and HTML (the credit line and the table)."""
    out = []
    for m in re.finditer(r'<div class="track-listing"', page_html):
        end = page_html.find("</table>", m.start())
        if end > 0:
            out.append((m.start(), page_html[m.start():end + 8]))
    return out


def _heading_at(page_html: str, pos: int) -> str:
    last = ""
    for m in _HEADING.finditer(page_html, 0, pos):
        last = m.group(2).replace("_", " ")
    return last


def song_tables(page_html: str, whole_page: bool = False) -> list[tuple[Table, dict[str, list[str]]]]:
    """Track listings and song tables, each with the credits its listing gives every song ("All lyrics
    are written by ..."): in music sections, or anywhere on a soundtrack's own page."""
    out = []
    for pos, block in _blocks(page_html):
        heading = _heading_at(page_html, pos)
        if not (whole_page or MUSIC_HEADINGS.search(heading)):
            continue
        text = _plain(block.split("<table", 1)[0])
        common = {}
        if m := _ALL_LYRICS.search(text):
            common["lyricists"] = _names(m.group(1))
        if m := _ALL_MUSIC.search(text):
            common["composers"] = _names(m.group(1))
        for t in read_tables(block, None):
            if "tracklist" in t.classes:
                t.headings = [heading] if heading else []
                out.append((t, common))
    for t in read_tables(page_html, None):
        cls = set(t.classes)
        if "wikitable" not in cls or "tracklist" in cls:
            continue
        headers = [h.lower() for h in t.header_texts()]
        has_song = _col(headers, "song", "title", "track") is not None
        has_singer = _col(headers, "singer", "artist", "vocal", "performer", "playback") is not None
        if has_song and has_singer and (whole_page or any(MUSIC_HEADINGS.search(h) for h in t.headings)):
            out.append((t, {}))
    return out


def parse_songs(page_html: str, whole_page: bool = False) -> list[dict]:
    songs = []
    for t, common in song_tables(page_html, whole_page):
        headers = [h.lower() for h in t.header_texts()]
        ti = _col(headers, "title", "song", "track")
        si = _col(headers, "singer", "artist", "vocal", "performer", "playback", "extra")
        li = _col(headers, "lyric", "lyricist", "written", "words")
        mi = _col(headers, "music", "composer", "composed")
        di = _col(headers, "length", "duration", "time")
        ni = _col(headers, "note", "remark")
        if ti is None:
            continue
        part = t.headings[-1] if t.headings else ""
        for row in t.body():
            cells = [c.text.strip() for c in row]
            if ti >= len(cells) or not cells[ti] or cells[ti].lower().startswith("total length"):
                continue
            title = _title(cells[ti])
            if not title or title.lower() in ("title", "song"):
                continue
            def get(i):
                return cells[i] if i is not None and i < len(cells) else ""
            v = _VERSION.search(title)
            songs.append({
                "title": title,
                "singers": _names(get(si)),
                "lyricists": _names(get(li)) or common.get("lyricists", []),
                "composers": _names(get(mi)) or common.get("composers", []),
                "duration": get(di) if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", get(di)) else None,
                "note": get(ni) or None,
                "version": v.group(1).lower() if v else None,
                "part": part,
            })
    return songs


# Song lists: older Hollywood articles list the musical numbers as bullets, not tables:
#   "The Boy Next Door", Hugh Martin and Ralph Blane, 1944, performed by Judy Garland.
#   "Over the Rainbow" – music by Harold Arlen, lyrics by E. Y. Harburg; sung by Judy Garland
_LI = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S)
_QUOTED = re.compile(r'^\s*["\u201c](.+?)["\u201d]\s*(.*)$', re.S)
_PERFORMED = re.compile(r"\b(?:performed|sung|reprised|danced|played)\s+(?:by|with)\s+", re.I)
_ROLE = re.compile(r"\b(words and music|music and lyrics|music|lyrics|words)\s+by\s+", re.I)
_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")


def _upto(text: str) -> str:
    """Names up to where the next clause starts."""
    return re.split(r"\s*(?:;|(?<!\b[A-Z])(?<!Bros)(?<!Jr)(?<!Sr)\.\s|\(|\bwith sections\b|\bperformed\b|\bsung\b|\bmusic by\b|\blyrics by\b|"
                    r"\bwords by\b|\bvoices?\b|\b(?:1[89]|20)\d\d\b)", text, maxsplit=1)[0]


def _people(text: str) -> list[str]:
    """Names in a clause; descriptions ("the film's producer", "melody adapted by ...") are dropped."""
    return [n for n in _names(_upto(text)) if n[:1].isupper() and " by " not in n and len(n.split()) <= 5]


def parse_list_item(text: str) -> dict | None:
    """One bullet of a song list, or None when the bullet does not start with a quoted title."""
    m = _QUOTED.match(text)
    if not m or len(m.group(1)) > 120:
        return None
    title, rest = m.group(1).strip().rstrip(","), m.group(2).strip(" ,.\u2013\u2014-:")
    out = {"title": title, "singers": [], "lyricists": [], "composers": [], "songwriters": [],
           "written": None, "note": rest[:300] or None}
    if pm := _PERFORMED.search(rest):
        out["singers"] = _people(rest[pm.end():])
    roles = list(_ROLE.finditer(rest))
    for rm in roles:
        names = _people(rest[rm.end():])
        kind = rm.group(1).lower()
        if kind in ("words and music", "music and lyrics"):
            out["composers"] += names
            out["lyricists"] += names
        elif kind == "music":
            out["composers"] += names
        else:
            out["lyricists"] += names
    if not roles:
        head = rest[:_PERFORMED.search(rest).start()] if _PERFORMED.search(rest) else rest
        head = re.sub(r"^(?:by|written by|composed by)\s+", "", head.strip(" ,"), flags=re.I)
        if head and not re.match(r"(?:traditional|instrumental|uncredited|reprise)", head, re.I):
            out["songwriters"] = _people(head)
    if y := _YEAR.search(rest):
        out["written"] = int(y.group(1))
    return out


def song_lists(page_html: str) -> list[dict]:
    """Songs listed as bullets in the article's music sections (h2/h3 whose title names music or songs)."""
    marks = [(m.start(), m.group(2)) for m in _HEADING.finditer(page_html)]
    out = []
    for i, (pos, hid) in enumerate(marks):
        if not MUSIC_HEADINGS.search(hid.replace("_", " ")):
            continue
        end = marks[i + 1][0] if i + 1 < len(marks) else len(page_html)
        section = re.sub(r"<(?:ul|ol)\b[^>]*>.*?</(?:ul|ol)>(?=\s*</li>)", "", page_html[pos:end], flags=re.S)
        for li in _LI.finditer(section):
            text = re.sub(r"\[\d+\]|\[[a-z]\]", "", _plain(li.group(1)))
            song = parse_list_item(text)
            if song:
                song["part"] = hid.replace("_", " ")
                out.append(song)
    return out


_MAIN = re.compile(r'(?:Main article|Main articles|Further information|See also):\s*<a[^>]*href="(?:https://en\.wikipedia\.org)?/wiki/([^"#?]+)"')
_HEADING = re.compile(r'<h([23])[^>]*id="([^"]+)"')


def soundtrack_articles(page_html: str) -> list[str]:
    """"Main article" links of the film's music sections that name a soundtrack or album page."""
    marks = [(m.start(), m.group(2)) for m in _HEADING.finditer(page_html)]
    out = []
    for i, (pos, hid) in enumerate(marks):
        if not MUSIC_HEADINGS.search(hid.replace("_", " ")):
            continue
        end = marks[i + 1][0] if i + 1 < len(marks) else len(page_html)
        for target in _MAIN.findall(page_html[pos:end]):
            title = unquote(html.unescape(target)).replace("_", " ")
            if re.search(r"soundtrack|album|music", title, re.I) and title not in out:
                out.append(title)
    return out


def album_details(page_html: str) -> dict[str, str]:
    """The album infobox's Label and Released rows."""
    out: dict[str, str] = {}
    for t in read_tables(page_html, None):
        if "infobox" not in t.classes:
            continue
        for row in t.rows:
            if len(row) >= 2 and row[0].text.strip() in ("Label", "Released") and row[1].text.strip():
                key = "record_company" if row[0].text.strip() == "Label" else "album_release"
                out.setdefault(key, re.sub(r"\[\d+\]", "", row[1].text).strip())
        if out:
            break
    return out


def _film_for(store, article: str) -> tuple[int | None, bool]:
    """The register container for this page: (id, is a soundtrack page)."""
    for field, soundtrack in (("wikipedia", False), ("soundtrack_article", True)):
        row = store.conn.execute(
            """SELECT c.entity_id FROM claim c JOIN entity e ON e.id = c.entity_id AND e.type = 'container'
               WHERE c.field = %s AND c.status = 'active' AND lower(c.value #>> '{}') = lower(%s)
               ORDER BY (c.source_id = 'wikidata') DESC, c.id LIMIT 1""", (field, article)).fetchone()
        if row:
            return row["entity_id"], soundtrack
    return None, False


def extract(ctx) -> None:
    store = ctx.store
    article = article_of(ctx.page.url)
    film, is_soundtrack = _film_for(store, article)
    if film is None:
        return

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="wikipedia_film_songs", extractor_version=VERSION, raw_page_id=ctx.raw_page_id)

    page = ctx.page.text
    if not is_soundtrack:
        for st in soundtrack_articles(page):
            claim(film, "soundtrack_article", st)
            ctx.enqueue(film_url(st), "wikipedia_film_songs", "enrich", priority=20)
    for k, v in album_details(page).items() if is_soundtrack else ():
        claim(film, k, v)
    songs = parse_songs(page, whole_page=is_soundtrack)
    if not songs and not is_soundtrack:
        songs = [{**s, "duration": None, "version": None} for s in song_lists(page)]
    if not songs:
        return
    year = store.conn.execute(
        "SELECT value FROM claim WHERE entity_id = %s AND field = 'year' AND status = 'active' LIMIT 1",
        (film,)).fetchone()
    tiers = store.pack.data["canon_model"].get("kind_tiers") or {}
    tier = None
    if tiers.get("film_song"):
        tier = "B" if year and isinstance(year["value"], int) and year["value"] > 1985 and \
            "B" in store.pack.data.get("tiers", {}) else tiers["film_song"]
    for n, s in enumerate(songs, 1):
        song = store.find_or_create_by_xref("song", SOURCE, f"{article}#{n}", s["title"])
        claim(song, "title", s["title"])
        claim(song, "part_of", {"position": n}, ref=film)
        claim(song, "work_kind", "film_song")
        for role, key in (("singer", "singers"), ("lyricist", "lyricists"), ("composer", "composers"),
                          ("songwriter", "songwriters")):
            for name in s.get(key, []):
                claim(song, "credit_unlinked", {"role": role, "name": name})
        for field in ("duration", "version", "note", "written"):
            if s.get(field):
                claim(song, field, s[field])
        if s["part"] and not MUSIC_HEADINGS.fullmatch(s["part"].strip()):
            claim(song, "album_part", s["part"])
        if tier:
            store.upsert_claim(song, "tier", value=tier, source_id="claude", kind="decided",
                               extractor="wikipedia_film_songs")
