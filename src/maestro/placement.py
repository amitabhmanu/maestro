"""Placing a listing in the canon: which canon film it names, and which of that film's units it is.

Sources that add details only (`unit_mode: enrich`) never create units: each song they list is placed
in the canon's own film (same film-title key as resolution uses, years at most one apart; a film the
canon still holds twice is searched as one) and matched to that film's units by title, with
resolution's own thresholds for titles in one film. Titles are compared in their own script: a
Devanagari title (a Discogs track, say) is compared with the Devanagari titles the canon holds
(HFGK's `title_hi`), with matras, nukta and vowel length folded, since `fold` cannot read them.
"""

from __future__ import annotations

import re
import unicodedata

_DEVA = re.compile(r"[ऀ-ॿ]")
_DROP = dict.fromkeys(map(ord, "़ँंः्​‌‍"), None)
_FOLD = str.maketrans({"ी": "ि", "ू": "ु", "ई": "इ", "ऊ": "उ", "ौ": "ो", "ै": "े", "ॉ": "ा", "ॅ": "े",
                       "ऐ": "ए", "औ": "ओ"})


def deva_key(text: str) -> str:
    """'यह लकनौ की सर ज़मीं' -> 'यह लकनो कि सर जमि': nukta, nasal marks and virama dropped, long vowels
    folded to short; other scripts and digits removed."""
    t = unicodedata.normalize("NFD", text or "").translate(_DROP).translate(_FOLD)
    t = re.sub(r"[^ऀ-॥॰-ॿ\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def is_devanagari(text: str) -> bool:
    letters = [ch for ch in text or "" if ch.isalpha() or unicodedata.category(ch).startswith("M")]
    return bool(letters) and sum(1 for ch in letters if _DEVA.match(ch)) * 2 > len(letters)


def title_keys(norm, text: str) -> tuple[str, str]:
    """(script, key): 'deva' with `deva_key`, else 'latin' with the normaliser's title key."""
    if is_devanagari(text):
        return "deva", deva_key(text)
    return "latin", norm.title(text)


def canon_index(store, exclude_source: str | None = None):
    """Per film-title key, the canon's films (merge roots) and their years; per film, its units' title
    keys (label and every active title-like claim), for units credited to the maestro. Containers carrying an xref from `exclude_source`
    are left out (a source in create mode once made them). Built once per store and source."""
    from maestro.resolve import Groups, Normaliser, _squash_container

    caches = store.__dict__.setdefault("_placement_index", {})
    if exclude_source in caches:
        return caches[exclude_source]
    norm, groups = Normaliser.for_store(store), Groups(store)
    films: dict[str, dict[int, set[int]]] = {}
    for r in store.conn.execute(
            """SELECT e.id, e.label, (SELECT min((c.value #>> '{}')::int) FROM claim c WHERE c.entity_id = e.id
                     AND c.field = 'year' AND c.status = 'active' AND jsonb_typeof(c.value) = 'number') AS year
               FROM entity e WHERE e.type = 'container'
                 AND NOT EXISTS (SELECT 1 FROM claim x WHERE x.entity_id = e.id AND x.field = 'xref'
                                 AND x.source_id = %s AND x.status = 'active')""", (exclude_source,)).fetchall():
        key = _squash_container(norm.container(r["label"]))
        if key:
            years = films.setdefault(key, {}).setdefault(groups.find(r["id"]), set())
            if r["year"]:
                years.add(r["year"])
    # Only the maestro's own units: a listing names one of the maestro's songs, never another
    # singer's song from the same film that some source keeps.
    credited = {groups.find(r["entity_id"]) for r in store.conn.execute(
        """SELECT DISTINCT entity_id FROM claim WHERE field = 'credit' AND status = 'active' AND ref_entity_id = %s""",
        (store.maestro_entity(),)).fetchall()}
    titles: dict[int, set[str]] = {}
    for r in store.conn.execute(
            """SELECT e.id, e.label FROM entity e WHERE e.type = %s
                 AND EXISTS (SELECT 1 FROM claim p WHERE p.entity_id = e.id AND p.field = 'part_of' AND p.status = 'active')""",
            (store.pack.unit,)).fetchall():
        titles.setdefault(r["id"], set()).add(r["label"])
    for r in store.conn.execute(
            """SELECT c.entity_id, c.value #>> '{}' AS t FROM claim c JOIN entity e ON e.id = c.entity_id
               WHERE e.type = %s AND c.status = 'active' AND c.field IN ('title', 'title_hi', 'first_line')
                 AND jsonb_typeof(c.value) = 'string'""", (store.pack.unit,)).fetchall():
        if r["entity_id"] in titles and r["t"]:
            titles[r["entity_id"]].add(r["t"])
    songs: dict[int, list[tuple[int, tuple[str, str]]]] = {}
    for r in store.conn.execute(
            """SELECT p.entity_id, p.ref_entity_id AS film FROM claim p JOIN entity e ON e.id = p.entity_id
               WHERE e.type = %s AND p.field = 'part_of' AND p.status = 'active'""", (store.pack.unit,)).fetchall():
        root, film = groups.find(r["entity_id"]), groups.find(r["film"])
        if root not in credited:
            continue
        for t in titles.get(r["entity_id"], ()):
            key = title_keys(norm, t)
            if key[1]:
                songs.setdefault(film, []).append((root, key))
    caches[exclude_source] = (films, songs, norm, groups)
    return caches[exclude_source]


def film_roots(store, film_title: str, film_year: int | None, exclude_source: str | None = None,
               later_issue: bool = False) -> list[int]:
    """The canon films a listing's film names: same key, years at most one apart (or unknown). With
    `later_issue` (the listing's year is its issue's, maybe a reissue's), a key held by one film only
    is that film when the issue is not older than it."""
    from maestro.resolve import _squash_container

    films, _songs, norm, _groups = canon_index(store, exclude_source)
    cands = films.get(_squash_container(norm.container(film_title)), {})
    roots = [root for root, years in cands.items()
             if not years or not film_year or any(abs(y - film_year) <= 1 for y in years)]
    if not roots and later_issue and film_year and len(cands) == 1:
        root, years = next(iter(cands.items()))
        if min(years) <= film_year:
            roots = [root]
    return roots


def place(store, film: dict, title: str, exclude_source: str | None = None,
          later_issue: bool = False) -> tuple[list[int], str]:
    """The canon units a listing names (one, or the versions of one song that tie), or none with the
    reason it could not be placed: `film_not_in_canon` or `song_not_in_film`."""
    from maestro.resolve import AUTO_TITLE_RATIO, CLEAR_MARGIN, CLEAR_TITLE_RATIO, TIE_MARGIN, title_similarity

    roots = film_roots(store, film["title"], film.get("year"), exclude_source, later_issue)
    if not roots:
        return [], "film_not_in_canon"
    _films, songs, norm, _groups = canon_index(store, exclude_source)
    script, key = title_keys(norm, title)
    best: dict[int, float] = {}
    for root in roots:
        for unit, (s, k) in songs.get(root, []):
            if s == script and key:
                best[unit] = max(best.get(unit, 0.0), title_similarity(key, k))
    ranked = sorted(best.items(), key=lambda x: -x[1])
    if not ranked or ranked[0][1] < CLEAR_TITLE_RATIO:
        return [], "song_not_in_film"
    top = ranked[0][1]
    tied = [u for u, sim in ranked if top - sim < TIE_MARGIN]
    if top >= AUTO_TITLE_RATIO:
        return tied, "matched"          # versions of one song ("... (Sad)") all match
    rest = ranked[len(tied)][1] if len(ranked) > len(tied) else 0.0
    if len(tied) == 1 and top - rest >= CLEAR_MARGIN:
        return tied, "matched"
    return [], "song_not_in_film"


GAP_FIELD = "listing_gap"


def record_gap(store, source: str, url: str, gap: dict | None, claim) -> None:
    """Keep one active `listing_gap` per listing url: a re-read whose outcome changed supersedes the
    earlier gap (claims are never deleted), and a gap is written when the listing is still unplaced."""
    import json

    maestro = store.maestro_entity()
    store.conn.execute(
        """UPDATE claim SET status = 'superseded' WHERE entity_id = %s AND field = %s AND source_id = %s
             AND status = 'active' AND value ->> 'url' = %s AND value IS DISTINCT FROM %s::jsonb""",
        (maestro, GAP_FIELD, source, url, json.dumps(gap) if gap else None))
    if gap:
        claim(maestro, GAP_FIELD, gap)
