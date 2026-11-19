"""Film register: match each film across sources, report the repository and where sources disagree.

A register pack (`identity.kind: register`, e.g. maestros/hindi-films) holds one container per film
per source: HFGK's film page, Wikidata's item, a Wikipedia year-list row. `match` links the records
of one film with reversible `same_as` claims, anchored on Wikidata:

  1. a Wikipedia row whose title links an article goes to the Wikidata item with that article;
  2. any other record goes to the Wikidata (or Wikipedia) film whose title has the same spelling key
     (`_squash_container`) or the same native-script title, dated at most a year apart. Several
     candidates are told apart by year and music director; a tie goes to the review queue.

`report` writes the repository (films.csv: one row per film with its Wikidata and IMDb IDs, year, music
directors, lyricists, singers and song count), the review list (review.csv: films matched to no
Wikidata item, films a source dates differently, music directors that disagree, titles spelt
differently, song counts that differ) and a summary (summary.json, summary.md).

`check-canon` runs the same checks on a maestro's canon films (e.g. Rafi's) against the register.

  maestro-films match --pack maestros/hindi-films [--dsn DSN]
  maestro-films report --pack maestros/hindi-films --out data/films
  maestro-films check-canon --pack maestros/hindi-films --canon maestros/rafi=DSN --out data/films
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from maestro import config
from maestro.pack import load_pack
from maestro.resolve import Groups, Normaliser, _distinct_pairs, _squash_container, fold, queue_review
from maestro.store import Store

ANCHOR_SOURCES = ("wikidata", "wikipedia")  # sources other records are matched to, best first
FILM_ROLES = ("composer", "lyricist", "director", "producer", "actor", "screenwriter", "cinematographer", "editor",
              "choreographer")
# Film facts kept as they come, for the explorer's film page (see `film_details`).
EXTRA_FIELDS = ("banner", "distributor", "record_company", "subject", "release", "censor", "box_office", "budget",
                "award", "based_on", "remade_as", "soundtrack", "soundtrack_article", "album_release", "language",
                "country", "link", "alt_title")


# ---- names ---------------------------------------------------------------------

def _native_key(text: str | None) -> str:
    """A Devanagari (or other script) title folded for matching: nukta and chandrabindu dropped."""
    t = unicodedata.normalize("NFC", text or "")
    t = t.replace("़", "").replace("ँ", "ं")
    return re.sub(r"[\s\-–—.,!?'\"]+", "", t)


_JOINT = re.compile(r"\s*(?:\s-\s|(?<=[a-z])-(?=[A-Z])|–|—|&|/|\+|,|\band\b)\s*")  # "Kalyanji-Anandji" too


def name_keys(name: str, norm: Normaliser | None = None) -> set[str]:
    """Keys under which two spellings of one person (or team) meet: the whole name without spaces,
    and each member of a team ("Shankar - Jaikishan"). `name_initials` adds initials plus surname."""
    keys: set[str] = set()
    for p in _name_parts(name, norm):
        flat = "".join(p)
        if len(flat) >= 4:
            keys.add(_squash_container(flat))
    return keys


def _name_parts(name: str, norm: Normaliser | None = None) -> list[list[str]]:
    whole = norm.person(name) if norm else fold(name)
    parts = [whole, *((norm.person(p) if norm else fold(p)) for p in _JOINT.split(name or "") if p.strip())]
    return [w for w in (p.replace("-", " ").split() for p in parts) if w]


def name_initials(name: str, norm: Normaliser | None = None) -> set[tuple[str, str]]:
    """(initials, surname) per name: "S. D. Burman" and "Sachin Dev Burman" are both ("sd", "burman")."""
    return {("".join(w[0] for w in words[:-1]), _squash_container(words[-1]))
            for words in _name_parts(name, norm) if len(words) >= 2 and len(words[-1]) >= 3}


def names_agree(a: Iterable[str], b: Iterable[str], norm: Normaliser | None = None) -> bool | None:
    """True when the two lists share a person, False when both name someone and share no one,
    None when either is empty. Initials meet when one person's run of initials begins the other's
    ("R. C. Boral" / "Raichand Boral", "Khurshid Anwar" / "Khawaja Khurshid Anwar"), so
    "R. D. Burman" and "S. D. Burman" stay apart."""
    a, b = [x for x in a if x], [x for x in b if x]
    if not a or not b:
        return None
    ka = set().union(*(name_keys(x, norm) for x in a))
    kb = set().union(*(name_keys(x, norm) for x in b))
    if ka & kb:
        return True
    ia = set().union(*(name_initials(x, norm) for x in a))
    ib = set().union(*(name_initials(x, norm) for x in b))
    if any(sa == sb and x and y and (x.startswith(y) or y.startswith(x)) for x, sa in ia for y, sb in ib):
        return True
    # Spellings and fuller forms: "Ajit Varman" / "Ajit Verman", "Master Krishnarao" / "Krishnarao
    # Phulambrikar", "Pt Badri Prasad" / "Badri Prasad": every word of the shorter name (titles such as
    # Master and Pandit left out) is a word of the longer, compared by consonants.
    def words(names):  # each name, and each member of a team ("Kalyanji-Anandji")
        out = []
        for x in names:
            for part in [x, *(p for p in _JOINT.split(x) if p.strip() and p.strip() != x.strip())]:
                out.append(_name_words(part, norm))
        return out
    ta, tb = words(a), words(b)
    for x in ta:
        for y in tb:
            if x and "".join(x) == "".join(y):  # "Master Muhammad" / "Master Mohammed"
                return True
            short, long_ = (x, y) if len(x) <= len(y) else (y, x)
            if short and (len(short) >= 2 or len(short[0]) >= 4) and all(w in long_ for w in short):
                return True
            # "Jaidev" / "Jaidev Verma", "Naushad" / "Naushad Ali": a one-word name is the other's first word.
            if len(short) == 1 and len(short[0]) >= 3 and long_ and long_[0] == short[0]:
                return True
            # "Sundar Das" / "Sunderdas Bhatia": the shorter name, run together, begins the longer.
            if len(short) >= 2 and len("".join(short)) >= 5 and "".join(long_).startswith("".join(short)):
                return True
            # "Madhulal Damodar" / "Madhavlal Damodar": same surname, other words one letter apart.
            if len(x) == len(y) >= 2 and x[-1] == y[-1] and len(x[-1]) >= 3 \
                    and all(_near(p, q) for p, q in zip(x[:-1], y[:-1])):
                return True
    return False


def _near(a: str, b: str) -> bool:
    """At most one letter added, dropped or changed (consonant skeletons of 3+ letters)."""
    if a == b:
        return True
    if min(len(a), len(b)) < 3 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(p != q for p, q in zip(a, b)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


_HONORIFICS = {"master", "mastr", "pandit", "pt", "pandi", "ustad", "ustaad", "shri", "sri", "shree", "late", "dr",
               "mr", "md", "seth"}


def _name_words(name: str, norm: Normaliser | None = None) -> list[str]:
    """A person's name as consonant skeletons of its words, titles and footnote marks dropped."""
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)|\d+", " ", name or "")
    words = (norm.person(text) if norm else fold(text)).replace("-", " ").split()
    # B and V are one letter in many transliterations: "Brijlal" / "Vrijlal", "Basant" / "Vasant".
    out = [skeleton(w).replace("b", "v") for w in words if w not in _HONORIFICS]
    return [w for w in out if w]


# ---- loading -----------------------------------------------------------------------

_HOMOGLYPHS = str.maketrans("аеорсухкмтівАЕОРСУХКМТІВН", "aeopcyxkmtibAEOPCYXKMTIBH")


def latin(name: str) -> str:
    """A Latin-script name with a stray Cyrillic look-alike letter fixed (HFGK's "Chitragupт")."""
    if name and re.search(r"[A-Za-z]", name) and re.search(r"[\u0400-\u04ff]", name):
        return name.translate(_HOMOGLYPHS)
    return name

@dataclass
class FilmRecord:
    id: int
    label: str
    source: str | None = None
    xref: str | None = None
    title: str | None = None
    title_native: str | None = None
    year: int | None = None
    period: str | None = None
    qid: str | None = None
    imdb: str | None = None
    article: str | None = None
    song_count: int | None = None
    crew: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    extra: dict[str, list[Any]] = field(default_factory=lambda: defaultdict(list))
    song_people: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    songs: int = 0

    @property
    def composers(self) -> list[str]:
        return self.crew.get("composer") or list(self.song_people.get("composer", {}))

    @property
    def lyricists(self) -> list[str]:
        crew = self.crew.get("lyricist", [])
        return crew + [n for n in self.song_people.get("lyricist", {}) if n not in crew]

    @property
    def singers(self) -> list[str]:
        return [n for n, _ in self.song_people.get("singer", Counter()).most_common()]


def _first(v):
    return v[0] if isinstance(v, list) else v


def load_films(store: Store, ids: set[int] | None = None) -> dict[int, FilmRecord]:
    """Every container with its source, titles, year, IDs, crew and the people of its songs."""
    params = {"ids": sorted(ids) if ids is not None else None}
    films: dict[int, FilmRecord] = {}
    for r in store.conn.execute(
        """SELECT id, label FROM entity WHERE type = 'container'
           AND (%(ids)s::bigint[] IS NULL OR id = ANY(%(ids)s))""", params).fetchall():
        films[r["id"]] = FilmRecord(r["id"], r["label"])
    rows = store.conn.execute(
        """SELECT c.entity_id, c.field, c.value, c.source_id, p.label AS person
           FROM claim c JOIN entity e ON e.id = c.entity_id AND e.type = 'container'
           LEFT JOIN entity p ON p.id = c.ref_entity_id
           WHERE c.status = 'active' AND (%(ids)s::bigint[] IS NULL OR c.entity_id = ANY(%(ids)s))
             AND (c.field IN ('xref', 'title', 'first_line', 'title_hi', 'title_native', 'year', 'period',
                             'wikidata', 'imdb', 'wikipedia', 'song_count', 'credit', 'credit_unlinked')
                  OR (c.field = ANY(%(extra)s) AND c.visibility = 'publishable'))
           ORDER BY c.id""", {**params, "extra": list(EXTRA_FIELDS)}).fetchall()
    for r in rows:
        f = films.get(r["entity_id"])
        if f is None:
            continue
        fld, v = r["field"], r["value"]
        if fld == "xref" and f.source is None:
            f.source, f.xref = r["source_id"], str(v)
        elif fld in ("title", "first_line") and f.title is None:
            f.title = v
        elif fld in ("title_hi", "title_native") and f.title_native is None:
            f.title_native = v
        elif fld == "year" and isinstance(v, int) and f.year is None:
            f.year = v
        elif fld == "period":
            f.period = v
        elif fld == "wikidata":
            f.qid = v
        elif fld == "imdb":
            f.imdb = v
        elif fld == "wikipedia":
            f.article = v
        elif fld == "song_count" and isinstance(v, int):
            f.song_count = v
        elif fld in EXTRA_FIELDS:
            if v not in f.extra[fld]:
                f.extra[fld].append(v)
        elif fld in ("credit", "credit_unlinked") and isinstance(v, dict):
            role, name = v.get("role"), latin(r["person"] or v.get("name"))
            if role in FILM_ROLES and name and name not in f.crew[role]:
                f.crew[role].append(name)
            elif role == "singer" and name:
                f.song_people["singer"][name] += 0  # named on the film (Wikipedia notes), not per song
    for r in store.conn.execute(
        """SELECT m.ref_entity_id AS film, c.value ->> 'role' AS role,
                  coalesce(p.label, c.value ->> 'name') AS name, count(DISTINCT m.entity_id) AS n
           FROM claim m
           JOIN claim c ON c.entity_id = m.entity_id AND c.status = 'active'
                AND c.field IN ('credit', 'credit_unlinked')
           LEFT JOIN entity p ON p.id = c.ref_entity_id
           WHERE m.field = 'part_of' AND m.status = 'active'
             AND (%(ids)s::bigint[] IS NULL OR m.ref_entity_id = ANY(%(ids)s))
           GROUP BY 1, 2, 3""", params).fetchall():
        f = films.get(r["film"])
        if f is not None and r["role"] and r["name"]:
            f.song_people[r["role"]][latin(r["name"])] += r["n"]
    for r in store.conn.execute(
        """SELECT ref_entity_id AS film, count(DISTINCT entity_id) AS n FROM claim
           WHERE field = 'part_of' AND status = 'active'
             AND (%(ids)s::bigint[] IS NULL OR ref_entity_id = ANY(%(ids)s))
           GROUP BY 1""", params).fetchall():
        if r["film"] in films:
            films[r["film"]].songs = r["n"]
    for f in films.values():
        f.title = f.title or f.label
    return films


# ---- matching -----------------------------------------------------------------------

def _exact_title(t: str | None) -> str:
    return re.sub(r"[\W_]+", "", fold(t or ""))


def tie_break(f: FilmRecord, tied: list[FilmRecord], norm: Normaliser) -> tuple[FilmRecord, str] | None:
    """Pick one of several candidates that scored the same, by a rule a person would apply:

      1. the only one whose title is spelt exactly like this film's ("Gangs of Wasseypur – Part 1",
         not "Part 2" or the two-part whole);
      2. the only one whose title begins with this film's whole title ("MSG: The Messenger" is
         "MSG: The Messenger of God", not "MSG-2 The Messenger");
      3. the only one dated in this film's year;
      4. the only one with an English Wikipedia article or an IMDb ID, when the others share its title
         key and year: the others are duplicate Wikidata items (an item made from a Hindi Wikipedia
         article, "आगमन" beside "Aagman"), reported as `wikidata_duplicate`.
    Returns (candidate, rule) or None when no rule decides."""
    key = _exact_title(f.title)
    exact = [a for a in tied if key and _exact_title(a.title) == key]
    if len(exact) == 1:
        return exact[0], "exact title among equal candidates"
    starts = [a for a in tied if key and len(key) >= 6 and _exact_title(a.title).startswith(key)]
    if len(starts) == 1:
        return starts[0], "the only candidate whose title begins with this one"
    if f.year is not None:
        same_year = [a for a in tied if a.year == f.year]
        if len(same_year) == 1:
            return same_year[0], "the only candidate of the same year"
    rich = [a for a in tied if a.article or a.imdb]
    if len(rich) == 1 and len({a.year for a in tied}) == 1:
        return rich[0], "the only candidate with a Wikipedia article or IMDb ID (others look like duplicate items)"
    return None


@dataclass
class MatchStats:
    by_article: int = 0
    by_title: int = 0
    queued: int = 0
    closed: int = 0
    unmatched: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {"merged_by_article": self.by_article, "merged_by_title": self.by_title,
                "queued_for_review": self.queued,
                "reviews_closed_by_rule": self.closed, "unmatched": dict(self.unmatched)}


_DECADES = {"thirties": 1930, "forties": 1940, "fifties": 1950, "sixties": 1960, "seventies": 1970, "eighties": 1980}


def _in_period(year: int, period: str | None) -> bool:
    """HFGK dates a film never released by its decade ("Fifties / ...")."""
    word = (period or "").split("/")[0].strip().lower()
    start = _DECADES.get(word)
    return start is not None and start <= year <= start + 9


def skeleton(key: str) -> str:
    """A title's consonants only, for transliterations that spell vowels their own way: HFGK's
    "Teree Bahon Men", "Aakhri Ghalati", "Rusavaee" meet "Teri Baahon Mein", "Aakhri Galti", "Rusvai"."""
    t = key.replace(" ", "")
    for a, b in (("ow", "o"), ("aw", "a"), ("x", "ks"), ("w", "v"), ("q", "k"), ("z", "j"), ("ph", "f")):
        t = t.replace(a, b)
    t = re.sub(r"[aeiouyh]", "", t)
    return re.sub(r"(.)\1+", r"\1", t)


def _keys(f: FilmRecord, norm: Normaliser) -> set[str]:
    keys = set()
    for t in (f.title, re.sub(r"\s*\((?:\d{4} )?film\)$", "", f.article or "")):
        k = norm.container(t or "")
        if k:
            keys.add("r:" + _squash_container(k))
            sk = skeleton(k)
            if len(sk) >= 3:
                keys.add("s:" + sk)
    if f.title_native and not f.title_native.isascii():
        keys.add("n:" + _native_key(f.title_native))
    return keys


def _score(a: FilmRecord, b: FilmRecord, norm: Normaliser) -> int:
    s = 0
    if a.year is not None and b.year is not None:
        s += 2 if a.year == b.year else 1
    agree = names_agree(a.composers, b.composers, norm)
    s += {True: 2, False: -2, None: 0}[agree]
    if names_agree(a.crew.get("director", []), b.crew.get("director", []), norm):
        s += 1
    return s


def match(store: Store) -> MatchStats:
    """Link the records of one film across sources (see the module docstring)."""
    norm = Normaliser.for_store(store)
    stats = MatchStats()
    with store.exclusive():
        films = load_films(store)
        groups, distinct = Groups(store), _distinct_pairs(store)
        groups.note_sources([{"id": f.id, "sources": [f.source] if f.source else []} for f in films.values()])

        def join(keep: FilmRecord, other: FilmRecord, reason: str) -> bool:
            a, b = groups.find(keep.id), groups.find(other.id)
            if a == b or tuple(sorted((a, b))) in distinct or not groups.can_join(a, b):
                return False
            store.merge_entities(a, b, reason)
            groups.union(a, b)
            return True

        wikidata = [f for f in films.values() if f.source == "wikidata"]
        by_article = {f.article.casefold(): f for f in wikidata if f.article}
        for f in films.values():
            if f.source == "wikipedia" and f.article:
                w = by_article.get(f.article.casefold())
                if w and join(w, f, f"same Wikipedia article {f.article!r}"):
                    stats.by_article += 1

        # Anchors: one record per group that holds a Wikidata or Wikipedia record, keyed by title.
        def anchor_index(sources: tuple[str, ...]) -> dict[str, list[FilmRecord]]:
            idx: dict[str, list[FilmRecord]] = defaultdict(list)
            for f in films.values():
                if f.source in sources:
                    for k in _keys(f, norm):
                        idx[k].append(f)
            return idx

        for anchor_sources in (("wikidata",), ("wikipedia",)):
            idx = anchor_index(anchor_sources)
            for f in sorted(films.values(), key=lambda x: x.id):
                if f.source in ("wikidata",) or f.source in anchor_sources:
                    continue
                if anchor_sources == ("wikipedia",) and f.source == "wikipedia":
                    continue
                if any(s in groups.sources(f.id) for s in anchor_sources):
                    continue
                def gather(keys):
                    cands: dict[int, FilmRecord] = {}
                    for k in keys:
                        for a in idx.get(k, []):
                            if a.year is not None and f.year is not None and abs(a.year - f.year) > 1:
                                continue
                            if f.year is None and (a.year is None or not _in_period(a.year, f.period)):
                                continue  # an undated film only meets one dated inside its decade
                            root = groups.find(a.id)
                            if root != groups.find(f.id) and groups.can_join(root, f.id):
                                cands.setdefault(root, a)
                    return cands

                keys = _keys(f, norm)
                cands = gather(k for k in keys if not k.startswith("s:"))
                if not cands:
                    # Consonants only: looser, so it must be the one candidate, its music director must
                    # not disagree, and the year or the music director must agree outright.
                    loose = [a for a in gather(k for k in keys if k.startswith("s:")).values()
                             if names_agree(f.composers, a.composers, norm) is not False]
                    if len(loose) == 1 and (loose[0].year == f.year
                                            or names_agree(f.composers, loose[0].composers, norm)):
                        if join(loose[0], f, f"film {f.title!r} ({f.year}) / {loose[0].title!r} "
                                             f"({loose[0].year}): same consonants"):
                            stats.by_title += 1
                    continue
                scored = sorted(((_score(f, a, norm), a) for a in cands.values()), key=lambda x: -x[0])
                best, top = scored[0]
                clear = len(scored) == 1 or best > scored[1][0]
                rule = "same title key"
                if not clear and best >= 2:
                    picked = tie_break(f, [a for s, a in scored if s == best], norm)
                    if picked:
                        top, rule = picked
                        clear = True
                if len(scored) == 1 and best < 0:
                    continue  # the one candidate's music director disagrees: not this film
                if clear and best >= (0 if len(scored) == 1 else 2):
                    if join(top, f, f"film {f.title!r} ({f.year}) / {top.title!r} ({top.year}): "
                                     f"{rule}, score {best}"):
                        stats.by_title += 1
                else:
                    subjects = [groups.find(f.id), *(groups.find(a.id) for _, a in scored[:4])]
                    if queue_review(store, "film_match", subjects, {
                            "film": {"title": f.title, "year": f.year, "source": f.source},
                            "candidates": [{"title": a.title, "year": a.year, "source": a.source, "qid": a.qid,
                                            "score": s} for s, a in scored[:4]]}, priority=2):
                        stats.queued += 1
        stats.closed = close_settled_reviews(store, groups)
        for f in films.values():
            if f.source and f.source != "wikidata" and "wikidata" not in groups.sources(f.id):
                stats.unmatched[f.source] += 1
    return stats


def close_settled_reviews(store: Store, groups: Groups) -> int:
    """Close open film_match items that the rules have since settled: two of the item's records are
    now one film, or its only candidate's music director disagrees. The merge claim (source claude,
    with its rule as the reason) is the record; the item's note says so. Returns how many closed."""
    closed = 0
    for r in store.conn.execute(
            "SELECT id, subject_ids, evidence FROM review_item WHERE status = 'open' AND kind = 'film_match'").fetchall():
        ids = list(r["subject_ids"])
        roots = {groups.find(i) for i in ids}
        cands = (r["evidence"] or {}).get("candidates") or []
        note = None
        if len(roots) < len(ids):
            note = "settled by a match rule (see the same_as claim's reason)"
        elif len(cands) == 1 and (cands[0].get("score") or 0) < 0:
            note = "no match: the only candidate's music director disagrees"
        if note:
            store.conn.execute(
                """UPDATE review_item SET status = 'dismissed', decision = 'dismiss', note = %s, decided_at = now()
                   WHERE id = %s""", (note, r["id"]))
            closed += 1
    return closed


# ---- report ---------------------------------------------------------------------------

@dataclass
class Film:
    id: int
    members: list[FilmRecord]

    def by_source(self, source: str) -> list[FilmRecord]:
        return [m for m in self.members if m.source == source]

    def one(self, source: str) -> FilmRecord | None:
        got = self.by_source(source)
        return got[0] if got else None

    def pick(self, attr: str, order=("hfgk_online", "wikidata", "wikipedia")):
        for s in order:
            for m in self.by_source(s):
                v = getattr(m, attr)
                if v:
                    return v
        for m in self.members:
            if getattr(m, attr):
                return getattr(m, attr)
        return None


def films_in_groups(store: Store) -> list[Film]:
    records = load_films(store)
    groups = Groups(store)
    out: dict[int, list[FilmRecord]] = defaultdict(list)
    for f in records.values():
        out[groups.find(f.id)].append(f)
    return [Film(root, sorted(ms, key=lambda m: m.id)) for root, ms in sorted(out.items())]


def _era(films: list[Film]) -> tuple[int, int] | None:
    years = [m.year for f in films for m in f.by_source("hfgk_online") if m.year]
    return (min(years), max(years)) if years else None


def film_flags(film: Film, era: tuple[int, int] | None, norm: Normaliser) -> list[dict[str, Any]]:
    """What a person should look at for this film: each flag has a kind and its evidence."""
    flags: list[dict[str, Any]] = []
    sources = {m.source for m in film.members}
    year = film.pick("year")
    if "wikidata" not in sources:
        flags.append({"kind": "no_wikidata", "detail": f"only in {', '.join(sorted(s for s in sources if s))}"})
    if era and "hfgk_online" not in sources and year and era[0] <= year <= era[1]:
        flags.append({"kind": "not_in_hfgk", "detail": f"dated {year}, inside HFGK's years {era[0]}-{era[1]}"})
    if len([m for m in film.members if m.source == "hfgk_online"]) > 1:
        flags.append({"kind": "duplicate_in_source", "detail": "HFGK lists this film twice"})

    years = {}
    for m in film.members:
        if m.year:
            years.setdefault(m.source, m.year)
    if len(set(years.values())) > 1:
        spread = max(years.values()) - min(years.values())
        flags.append({"kind": "year_mismatch", "detail": ", ".join(f"{s} {y}" for s, y in sorted(years.items())),
                      "severity": "off_by_one" if spread == 1 else "major"})

    composers = {m.source: m.composers for m in film.members if m.composers}
    srcs = sorted(composers)
    for i, a in enumerate(srcs):
        for b in srcs[i + 1:]:
            if names_agree(composers[a], composers[b], norm) is False:
                flags.append({"kind": "composer_mismatch",
                              "detail": f"{a}: {'; '.join(composers[a])} | {b}: {'; '.join(composers[b])}"})

    # Titles: HFGK's transliteration against Wikidata's. Wikipedia and Wikidata often differ only by
    # an English title or a subtitle, which is not a spelling question.
    hfgk_titles = {_squash_container(norm.container(m.title or "")): m.title for m in film.by_source("hfgk_online")}
    for m in film.by_source("wikidata"):
        k = _squash_container(norm.container(m.title or ""))
        if hfgk_titles and k and k not in hfgk_titles:
            flags.append({"kind": "title_spelling",
                          "detail": f"hfgk_online: {' / '.join(hfgk_titles.values())} | wikidata: {m.title}"})

    counts = {m.source: m.song_count for m in film.members if m.song_count is not None}
    if len(set(counts.values())) > 1:
        flags.append({"kind": "song_count_mismatch", "detail": ", ".join(f"{s} {n}" for s, n in sorted(counts.items()))})
    return flags


# ---- resolving flags ---------------------------------------------------------------------
#
# Each flag gets a status: `resolved` (a stated rule, or the user's decision, settles it: the value the
# register uses is given), `open` (a person decides: it goes to to-review.csv) or `info` (a gap, not a
# disagreement: no Wikidata item yet). Rules never change the claims; the user's decisions are `user`
# claims (`register_decision`, recorded with `maestro-films decide`) and always win.

SOURCE_WORDS = {"hfgk_online": "HFGK", "wikidata": "Wikidata", "wikipedia": "Wikipedia"}


def load_decisions(store: Store) -> dict[int, dict[str, dict[str, Any]]]:
    """The user's register decisions by film group root and flag kind."""
    groups = Groups(store)
    out: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for r in store.conn.execute(
            """SELECT entity_id, value FROM claim WHERE field = 'register_decision' AND status = 'active'
               AND source_id = 'user' ORDER BY id""").fetchall():
        v = r["value"] or {}
        if v.get("flag"):
            out[groups.find(r["entity_id"])][v["flag"]] = v
    return out


def resolve_flags(film: Film, flags: list[dict[str, Any]], norm: Normaliser,
                  decisions: dict[str, dict[str, Any]] | None = None,
                  era: tuple[int, int] | None = None) -> list[dict[str, Any]]:
    """Add `status`, `rule` and (where it settles a value) `value` to each flag, in place."""
    decisions = decisions or {}
    years = {m.source: m.year for m in film.members if m.year and m.source}
    composers = {m.source: m.composers for m in film.members if m.composers and m.source}
    in_era = bool(era and film.pick("year") and era[0] <= film.pick("year") <= era[1])
    for f in flags:
        kind = f["kind"]
        d = decisions.get(kind)
        if d:
            f.update(status="resolved", rule=f"your decision{': ' + d['note'] if d.get('note') else ''}",
                     value=d.get("value"))
            continue
        f.update(status="open", rule="")
        if kind == "no_wikidata":
            f.update(status="info", rule="a gap in Wikidata, not a disagreement")
        elif kind == "year_mismatch":
            _resolve_year(f, years)
        elif kind == "composer_mismatch":
            _resolve_composer(f, composers, years, norm)
        elif kind == "title_spelling":
            _resolve_title(f, film, norm)
        elif kind == "not_in_hfgk" and not in_era:
            f.update(status="info", rule="outside HFGK's years")
    return flags


def _resolve_year(f: dict[str, Any], years: dict[str, int]) -> None:
    # The user's rule (19 Nov 2026): the year most sources give wins, where there is such a year.
    votes = Counter(years.values()).most_common()
    if votes and votes[0][1] >= 2 and (len(votes) == 1 or votes[0][1] > votes[1][1]):
        y = votes[0][0]
        agree = [SOURCE_WORDS.get(s, s) for s, v in years.items() if v == y]
        f.update(status="resolved", value=y, rule=f"the majority of sources: {' and '.join(agree)} say {y}")
        return
    h, wd, wp = years.get("hfgk_online"), years.get("wikidata"), years.get("wikipedia")
    if h is not None:
        others = [y for s, y in years.items() if s != "hfgk_online"]
        far = [s for s, y in years.items() if s != "hfgk_online" and abs(y - h) > 1]
        near = [s for s, y in years.items() if s != "hfgk_online" and abs(y - h) <= 1]
        if far and near:
            f.update(rule=f"{' and '.join(SOURCE_WORDS[s] for s in far)} dated {', '.join(str(years[s]) for s in far)}: "
                          f"probably another film of the same title linked to this one; HFGK and "
                          f"{' and '.join(SOURCE_WORDS[s] for s in near)} agree on {h}", value=h)
            return
        if all(abs(y - h) <= 1 for y in others):
            f.update(status="resolved", value=h,
                     rule="HFGK's year (from the censor certificate); the others are a year off, as release "
                          "dates often are")
        elif others and len(set(others)) == 1 and abs(others[0] - h) > 1:
            f.update(rule="HFGK and the others are more than a year apart: check the match")
        return
    if wd is not None and wp is not None:
        if wd < wp or abs(wd - wp) == 1:
            f.update(status="resolved", value=wp,
                     rule="Wikipedia's year list (the Indian release); Wikidata's earliest date is often a "
                          "festival premiere or an earlier release")


def _resolve_composer(f: dict[str, Any], composers: dict[str, list[str]], years: dict[str, int],
                      norm: Normaliser) -> None:
    if "hfgk_online" not in composers:
        return  # Wikidata against Wikipedia, neither a register: a person decides
    h = composers["hfgk_online"]
    agree = [s for s, c in composers.items() if s != "hfgk_online" and names_agree(h, c, norm)]
    hy, others = years.get("hfgk_online"), [y for s, y in years.items() if s != "hfgk_online"]
    far = hy is not None and others and all(abs(y - hy) > 1 for y in others)
    if agree:
        f.update(status="resolved", value=h,
                 rule=f"HFGK's music directors, as {' and '.join(SOURCE_WORDS[s] for s in agree)} also say")
    elif far:
        f.update(rule="the years are far apart too: the match itself may be wrong")
    else:
        f.update(status="resolved", value=h,
                 rule="HFGK's music directors: it credits every song from the records; Wikidata and the "
                      "yearly lists are often wrong for older films (a wrong person linked, a lyricist read as "
                      "the composer)")


def _resolve_title(f: dict[str, Any], film: Film, norm: Normaliser) -> None:
    hfgk = [m.title for m in film.by_source("hfgk_online") if m.title]
    for m in film.by_source("wikidata"):
        t = m.title or ""
        if t and t.isascii():
            # The user's rule (19 Nov 2026): Wikidata's spelling is the correct one.
            f.update(status="resolved", value=t,
                     rule="Wikidata's spelling, the others kept as alternatives")
            return
        if not t.isascii():
            f.update(status="resolved", value=hfgk[0] if hfgk else t,
                     rule="Wikidata has only a Hindi-script title: HFGK's transliteration is used")
            return


def apply_resolutions(row: dict[str, Any], flags: list[dict[str, Any]]) -> None:
    """Use each resolved flag's value in the film's row (the year a rule or the user settled)."""
    for f in flags:
        if f.get("status") == "resolved" and f.get("value") is not None:
            if f["kind"] == "year_mismatch" and isinstance(f["value"], int):
                row["year"] = f["value"]
            elif f["kind"] == "composer_mismatch" and isinstance(f["value"], list):
                row["music_directors"] = "; ".join(f["value"])
            elif f["kind"] == "title_spelling" and isinstance(f["value"], str):
                row["title"] = f["value"]


def _cell(v: Any) -> str:
    if v is None:
        return ""
    return "; ".join(map(str, v)) if isinstance(v, list) else str(v)


def film_row(film: Film) -> dict[str, Any]:
    hfgk = film.by_source("hfgk_online")
    singers = Counter()
    lyricists: list[str] = []
    composers: list[str] = []
    for m in hfgk:
        for n, c in m.song_people.get("singer", Counter()).items():
            singers[n] += c
        lyricists += [n for n in m.lyricists if n not in lyricists]
        composers += [n for n in m.composers if n not in composers]
    if not composers:
        composers = film.pick("composers") or []
    if not lyricists:
        lyricists = film.pick("lyricists") or []
    sing = [n for n, _ in singers.most_common()] or (film.pick("singers") or [])
    return {
        "film_id": film.id,
        "title": film.pick("title"),
        "title_hi": film.pick("title_native"),
        "year": film.pick("year"),
        "period": film.pick("period"),
        "wikidata": film.pick("qid", ("wikidata",)),
        "imdb": film.pick("imdb", ("wikidata",)),
        "wikipedia": film.pick("article", ("wikidata", "wikipedia")),
        "hfgk_id": hfgk[0].xref if hfgk else None,
        "music_directors": "; ".join(composers),
        "lyricists": "; ".join(lyricists),
        "singers": "; ".join(sing),
        "song_count": hfgk[0].song_count if hfgk else None,
        "directors": "; ".join(film.pick("crew").get("director", []) if film.pick("crew") else []),
        "sources": "; ".join(sorted({m.source for m in film.members if m.source})),
    }


def report(store: Store, out: Path) -> dict[str, Any]:
    norm = Normaliser.for_store(store)
    films = films_in_groups(store)
    era = _era(films)
    out.mkdir(parents=True, exist_ok=True)
    rows, review = [], []
    flag_counts: Counter = Counter()
    severity: Counter = Counter()
    by_decade: Counter = Counter()
    combos: Counter = Counter()
    songs = 0
    open_matches = defaultdict(list)
    for r in store.conn.execute(
            "SELECT id, subject_ids FROM review_item WHERE status = 'open' AND kind = 'film_match'").fetchall():
        for s in r["subject_ids"]:
            open_matches[s].append(r["id"])
    groups = Groups(store)
    decisions = load_decisions(store)
    status_counts: Counter = Counter()
    for film in films:
        row = film_row(film)
        flags = film_flags(film, era, norm)
        items = sorted({i for m in film.members for i in open_matches.get(groups.find(m.id), [])}
                       | {i for m in film.members for i in open_matches.get(m.id, [])})
        if items:
            flags.append({"kind": "match_review", "detail": f"review items {items}"})
        resolve_flags(film, flags, norm, decisions.get(film.id), era)
        apply_resolutions(row, flags)
        row["flags"] = "; ".join(sorted({f["kind"] for f in flags if f["status"] == "open"}))
        rows.append(row)
        songs += row["song_count"] or 0
        by_decade[(row["year"] // 10 * 10) if row["year"] else "undated"] += 1
        combos[row["sources"]] += 1
        for f in flags:
            flag_counts[f["kind"]] += 1
            if f["kind"] == "year_mismatch":
                severity[f["severity"]] += 1
            status_counts[f"{f['kind']}:{f['status']}"] += 1
            review.append({"film_id": film.id, "title": row["title"], "year": row["year"],
                           "wikidata": row["wikidata"], "hfgk_id": row["hfgk_id"], "flag": f["kind"],
                           "severity": f.get("severity", ""), "detail": f["detail"], "status": f["status"],
                           "rule": f["rule"], "value": _cell(f.get("value"))})
    song_rows = _add_details_and_songs(store, films, rows)
    rows.sort(key=lambda r: (r["year"] or 9999, (r["title"] or "").casefold()))
    review.sort(key=lambda r: (r["flag"], r["year"] or 9999, (r["title"] or "").casefold()))
    _write_csv(out / "films.csv", rows)
    _write_csv(out / "songs.csv", song_rows)
    _write_csv(out / "review.csv", review)
    # What a person decides, most telling first: matches, then disagreements, then gaps.
    order = {"match_review": 0, "duplicate_in_source": 1, "composer_mismatch": 2, "year_mismatch": 3,
             "title_spelling": 4, "song_count_mismatch": 5, "not_in_hfgk": 6}
    _write_csv(out / "to-review.csv", sorted(
        ({k: v for k, v in r.items() if k not in ("status", "value")} for r in review if r["status"] == "open"),
        key=lambda r: (order.get(r["flag"], 9), r["year"] or 9999, (r["title"] or "").casefold())))
    songs = sum(int(r["song_count"] or 0) for r in rows)
    hfgk_films = sum(1 for f in films if f.by_source("hfgk_online"))
    summary = {
        "films": len(films),
        "films_with_wikidata": sum(1 for f in films if f.by_source("wikidata")),
        "films_with_hfgk": hfgk_films,
        "hfgk_matched_to_wikidata": sum(1 for f in films if f.by_source("hfgk_online") and f.by_source("wikidata")),
        "hfgk_years": list(era) if era else None,
        "songs_hfgk": sum(int(r["song_count"] or 0) for r in rows if r["hfgk_id"]),
        "songs": songs,
        "songs_listed_after_hfgk": sum(int(r["song_count"] or 0) for r in rows if not r["hfgk_id"]),
        "new_songs": sum(int(r["new_songs"] or 0) for r in rows),
        "reused_songs": sum(int(r["reused_songs"] or 0) for r in rows),
        "films_with_credits": sum(1 for r in rows if r["cast"] or r["producers"]),
        "films_with_awards": sum(1 for r in rows if r["awards_won"] or r["awards_nominated"]),
        "remakes": sum(1 for r in rows if r["based_on"]),
        "by_decade": {str(k): v for k, v in sorted(by_decade.items(), key=lambda kv: str(kv[0]))},
        "by_sources": dict(combos.most_common()),
        "flags": dict(flag_counts.most_common()),
        "year_mismatch_severity": dict(severity),
        "flag_status": {k: {st: status_counts[f"{k}:{st}"] for st in ("resolved", "open", "info")
                            if status_counts[f"{k}:{st}"]} for k in flag_counts},
        "to_review": sum(v for k, v in status_counts.items() if k.endswith(":open")),
        "open_match_reviews": store.conn.execute(
            "SELECT count(*) AS n FROM review_item WHERE status = 'open' AND kind = 'film_match'").fetchone()["n"],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), "utf-8")
    (out / "summary.md").write_text(_summary_md(summary), "utf-8")
    return summary


def film_details(film: Film, by_qid: dict[str, int] | None = None) -> dict[str, Any]:
    """Everything else the sources say about a film, merged across its records (HFGK first): crew
    beyond the music, banner, label, dates, box office, awards, remakes, languages and outside IDs."""
    by_qid = by_qid or {}
    order = {"hfgk_online": 0, "wikidata": 1, "wikipedia": 2}
    members = sorted(film.members, key=lambda m: order.get(m.source, 9))
    crew: dict[str, list[str]] = {}
    extra: dict[str, list[Any]] = {}
    for m in members:
        for role, names in m.crew.items():
            have = crew.setdefault(role, [])
            have += [n for n in names if n not in have and not any(names_agree([n], [h]) for h in have)]
        for k, vs in m.extra.items():
            have = extra.setdefault(k, [])
            have += [v for v in vs if v not in have]
    out: dict[str, Any] = {}
    for role, key in (("producer", "prod"), ("actor", "cast"), ("screenwriter", "wri"), ("cinematographer", "cin"),
                      ("editor", "edit"), ("choreographer", "chor")):
        if crew.get(role):
            out[key] = crew[role][:15] if role == "actor" else crew[role]
    for k, key in (("banner", "banner"), ("distributor", "dist"), ("record_company", "label"), ("subject", "genre"),
                   ("language", "lang"), ("alt_title", "aka")):
        vals = [v for v in extra.get(k, []) if isinstance(v, str)]
        if vals:
            out[key] = vals
    releases = [r for r in extra.get("release", []) if isinstance(r, dict) and r.get("date")]
    india = sorted(r["date"] for r in releases if r.get("place") == "India")
    if india or releases:
        out["released"] = india[0] if india else sorted(r["date"] for r in releases)[0]
        firsts = sorted(r["date"] for r in releases)
        if firsts and firsts[0] != out["released"]:
            out["premiere"] = firsts[0]
    censor = [c for c in extra.get("censor", []) if isinstance(c, dict)]
    if censor:
        out["censor"] = censor[0]
    if extra.get("album_release"):
        out["album_released"] = extra["album_release"][0]
    for k in ("box_office", "budget"):
        vals = [v for v in extra.get(k, []) if isinstance(v, dict) and v.get("amount")]
        if vals:
            out[k] = sorted(vals, key=lambda v: (v.get("unit") != "INR", -(v.get("amount") or 0)))
    awards, seen = [], set()
    for a in extra.get("award", []):
        if not isinstance(a, dict):
            continue
        key = (a.get("award_qid") or a.get("award"), a.get("year"), a.get("status"), a.get("to_qid") or a.get("to"))
        if key in seen:
            continue
        seen.add(key)
        awards.append({k: a[k] for k in ("award", "year", "status", "to") if a.get(k)})
    if awards:
        out["awards"] = sorted(awards, key=lambda a: (a.get("status") != "won", a.get("year") or 0, a["award"]))
    for k, key in (("based_on", "based_on"), ("remade_as", "remade_as")):
        vals = []
        for v in extra.get(k, []):
            if isinstance(v, dict) and v.get("qid") and all(x["qid"] != v["qid"] for x in vals):
                vals.append({"qid": v["qid"], "label": v.get("label"),
                             **({"film": by_qid[v["qid"]]} if v["qid"] in by_qid else {})})
        if vals:
            out[key] = vals
    links = {}
    for v in extra.get("link", []):
        if isinstance(v, dict) and v.get("site") and v.get("id"):
            links.setdefault(v["site"], v["id"])
    if links:
        out["links"] = links
    if extra.get("soundtrack_article"):
        out["soundtrack_article"] = extra["soundtrack_article"][0]
    return out


def _add_details_and_songs(store: Store, films: list[Film], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add each film's details and song counts to its row (in place); return one row per song."""
    from maestro import filmsongs

    by_qid = {m.qid: f.id for f in films for m in f.members if m.qid}
    by_id = {r["film_id"]: r for r in rows}
    for f in films:
        d = film_details(f, by_qid)
        r = by_id[f.id]
        awards = d.get("awards", [])
        inr = [b for b in d.get("box_office", []) if b.get("unit") == "INR"]
        r.update({
            "producers": "; ".join(d.get("prod", [])), "cast": "; ".join(d.get("cast", [])[:12]),
            "writers": "; ".join(d.get("wri", [])), "banner": "; ".join(d.get("banner", [])),
            "record_company": "; ".join(d.get("label", [])), "released": d.get("released", ""),
            "censor_date": (d.get("censor") or {}).get("date", ""),
            "box_office_inr": inr[0]["amount"] if inr else "",
            "awards_won": sum(1 for a in awards if a.get("status") == "won"),
            "awards_nominated": sum(1 for a in awards if a.get("status") == "nominated"),
            "based_on": "; ".join(b.get("label") or b["qid"] for b in d.get("based_on", [])),
            "remade_as": "; ".join(b.get("label") or b["qid"] for b in d.get("remade_as", [])),
            "languages": "; ".join(d.get("lang", [])),
            **{f"{k}_id": (d.get("links") or {}).get(k, "") for k in ("tmdb", "letterboxd", "indiancine")},
            "new_songs": "", "reused_songs": "",
        })
    songs = filmsongs.load_songs(store)
    years = {r["film_id"]: r["year"] for r in rows}
    titles = {r["film_id"]: r["title"] for r in rows}
    filmsongs.mark_reuse(songs, years, titles)
    out = []
    for fid, fs in songs.items():
        r = by_id.get(fid)
        if r is None:
            continue
        if any(x["src"] == "hfgk_online" for x in fs):
            fs = [x for x in fs if x["src"] == "hfgk_online"]
        if not r["song_count"]:
            r["song_count"] = len(fs)
        r["new_songs"] = sum(1 for x in fs if filmsongs.is_new_composition(x))
        r["reused_songs"] = sum(1 for x in fs if x.get("reuse")) or ""
        for x in fs:
            role = filmsongs.roles(x["sing"])
            u = x.get("reuse") or {}
            out.append({"film_id": fid, "film": r["title"], "year": r["year"], "n": x["n"], "title": x["title"],
                        "singers": "; ".join(x["sing"]), "lyricists": "; ".join(x["lyr"]),
                        "music_directors": "; ".join(x["md"]), "on_screen": "; ".join(x["screen"]),
                        "raga": "; ".join(x["raga"]), "singing": role["kind"] + (" + chorus" if role["chorus"]
                                                                                 and role["kind"] != "chorus" else ""),
                        "version": x["ver"] or "", "duration": x["dur"] or "",
                        "reuse": u.get("kind", ""), "reuse_of": f"{u.get('title') or ''} ({u.get('year') or ''})"
                        if u.get("title") else "", "source": x["src"]})
    out.sort(key=lambda r: (r["year"] or 9999, r["film"] or "", r["n"] or 0))
    return out


def films_snapshot(store: Store, with_songs: bool = True) -> dict[str, Any]:
    """The register as one JSON-ready dict for the canon explorer's Films view: every film's row with
    its flags (each with its status and rule) and its details (crew, banner, label, dates, awards,
    remakes, outside IDs), and, under "songs", every song by decade (`write_site` writes each decade to
    its own file, loaded when a film is opened). Publishable claims only: long song titles are their
    first line, so no lyric text past it."""
    from datetime import datetime, timezone

    from maestro import filmsongs

    norm = Normaliser.for_store(store)
    films = films_in_groups(store)
    era = _era(films)
    groups = Groups(store)
    by_qid = {m.qid: f.id for f in films for m in f.members if m.qid}
    songs = filmsongs.load_songs(store, groups) if with_songs else {}
    out = []
    decisions = load_decisions(store)
    for film in films:
        row = film_row(film)
        flags = resolve_flags(film, film_flags(film, era, norm), norm, decisions.get(film.id), era)
        apply_resolutions(row, flags)
        hfgk = film.one("hfgk_online")
        out.append({
            "id": film.id, "title": row["title"], "title_hi": row["title_hi"], "year": row["year"],
            "period": row["period"], "wikidata": row["wikidata"], "imdb": row["imdb"],
            "wikipedia": row["wikipedia"], "hfgk": row["hfgk_id"],
            "md": [x for x in row["music_directors"].split("; ") if x],
            "lyr": [x for x in row["lyricists"].split("; ") if x],
            "sing": [x for x in row["singers"].split("; ") if x],
            "dir": [x for x in row["directors"].split("; ") if x],
            "songs": row["song_count"],
            "src": [m.source for m in film.members if m.source],
            "alt": sorted({m.title for m in film.members if m.title and m.title != row["title"]}),
            "flags": flags,
            **film_details(film, by_qid),
            **({"songs_listed": hfgk.songs} if hfgk and hfgk.songs != hfgk.song_count else {}),
        })
    years = {f["id"]: f["year"] for f in out}
    titles = {f["id"]: f["title"] for f in out}
    filmsongs.mark_reuse(songs, years, titles)
    by_decade: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in out:
        fs = songs.get(f["id"], [])
        if not fs:
            continue
        # Songs from Wikipedia are only used where HFGK has none (HFGK lists every song 1931-1985).
        if any(x["src"] == "hfgk_online" for x in fs):
            fs = [x for x in fs if x["src"] == "hfgk_online"]
        if f["songs"] is None:
            f["songs"] = len(fs)
            if not f["md"]:
                f["md"] = list(dict.fromkeys(n for x in fs for n in x["md"]))
            if not f["lyr"]:
                f["lyr"] = list(dict.fromkeys(n for x in fs for n in x["lyr"]))
            if not f["sing"]:
                count = Counter(n for x in fs for n in x["sing"])
                f["sing"] = [n for n, _ in count.most_common()]
        f["new_songs"] = sum(1 for x in fs if filmsongs.is_new_composition(x))
        reused = [x for x in fs if x.get("reuse")]
        if reused:
            f["reused_songs"] = len(reused)
        f["song_src"] = "hfgk_online" if fs[0]["src"] == "hfgk_online" else fs[0]["src"]
        decade = f"{f['year'] // 10 * 10}s" if f["year"] else "undated"
        f["song_file"] = decade
        for x in fs:
            by_decade[decade].append(_song_out(f["id"], x))
    out.sort(key=lambda r: (r["year"] or 9999, (r["title"] or "").casefold()))
    d = store.pack.data["identity"]
    return {"format": 2, "register": {"slug": d["slug"], "name": d["name"]},
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "hfgk_years": list(era) if era else None, "films": out, "songs": dict(by_decade)}


def _song_out(film_id: int, x: dict[str, Any]) -> dict[str, Any]:
    from maestro import filmsongs

    r = filmsongs.roles(x["sing"])
    # "id" is the song's entity id in the register store: gem tags on register songs key on it.
    out = {"id": x["id"], "f": film_id, "n": x["n"], "t": x["title"], "s": x["sing"], "l": x["lyr"], "m": x["md"],
           "sc": x["screen"], "r": x["raga"], "d": x["dur"], "v": x["ver"], "k": r["kind"],
           "ch": r["chorus"], "u": x.get("reuse"), "p": x["part"], "src": x["src"]}
    return {k: v for k, v in out.items() if v not in (None, [], "", False)}


def _summary_md(s: dict[str, Any]) -> str:
    lines = ["# Hindi film register: summary", "",
             f"- Films: {s['films']:,} ({s['films_with_wikidata']:,} with a Wikidata item, "
             f"{s['films_with_hfgk']:,} in HFGK)",
             f"- HFGK films matched to Wikidata: {s['hfgk_matched_to_wikidata']:,} of {s['films_with_hfgk']:,}",
             f"- Songs (HFGK, {s['hfgk_years'][0]}-{s['hfgk_years'][1]}): {s['songs_hfgk']:,}" if s["hfgk_years"]
             else "- Songs: none from HFGK yet",
             f"- Open match reviews: {s['open_match_reviews']:,}", "", "## Flags", ""]
    lines += [f"- {k}: {v:,}" for k, v in s["flags"].items()]
    lines += ["", "## Films by decade", ""] + [f"- {k}: {v:,}" for k, v in s["by_decade"].items()]
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        if not rows:
            fh.write("")
            return
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def decide(store: Store, film_id: int, flag: str, value: str, note: str | None = None) -> dict[str, Any]:
    """Record the user's decision on one flag of one film (a `user` claim on the film's group, which
    replaces an earlier decision on the same flag). VALUE is the year, the music directors (separated by
    ';'), the title, or `ok` to accept the flag as it stands."""
    groups = Groups(store)
    root = groups.find(film_id)
    v: Any = value
    if flag == "year_mismatch":
        v = int(value)
    elif flag == "composer_mismatch":
        v = [x.strip() for x in value.split(";") if x.strip()]
    elif value.lower() == "ok":
        v = None
    with store.conn.transaction():
        for r in store.conn.execute(
                """SELECT c.id, c.entity_id, c.value FROM claim c WHERE c.field = 'register_decision'
                   AND c.status = 'active' AND c.source_id = 'user'""").fetchall():
            if groups.find(r["entity_id"]) == root and (r["value"] or {}).get("flag") == flag:
                store.conn.execute("UPDATE claim SET status = 'superseded' WHERE id = %s", (r["id"],))
        cid = store.upsert_claim(root, "register_decision", value={"flag": flag, "value": v, "note": note},
                                 source_id="user", kind="decided", extractor="maestro-films decide")
    return {"film": root, "flag": flag, "value": v, "claim": cid}


# ---- checking a maestro's canon films ---------------------------------------------------

def canon_films(canon: Store) -> list[Film]:
    """The canon's films: containers holding at least one canon unit, merged groups as one."""
    units = canon._canon_units_sql()
    ids = {r["ref_entity_id"] for r in canon.conn.execute(
        f"""WITH units AS ({units})
            SELECT DISTINCT m.ref_entity_id FROM claim m JOIN units u ON u.id = m.entity_id
            WHERE m.field = 'part_of' AND m.status = 'active'""", canon._canon_params()).fetchall()}
    groups = Groups(canon)
    roots = {groups.find(i) for i in ids}
    all_ids = {r["id"] for r in canon.conn.execute("SELECT id FROM entity WHERE type = 'container'").fetchall()
               if groups.find(r["id"]) in roots}
    records = load_films(canon, all_ids)
    out: dict[int, list[FilmRecord]] = defaultdict(list)
    for f in records.values():
        out[groups.find(f.id)].append(f)
    return [Film(root, ms) for root, ms in sorted(out.items())]


def check_canon(register: Store, canon: Store, out: Path) -> dict[str, Any]:
    """Compare a maestro's canon films with the register: matched by source xref (e.g. the same HFGK
    film id), else by title key and year. Each canon film is flagged where an independent source in
    the register disagrees with it."""
    norm = Normaliser.for_store(register)
    films = films_in_groups(register)
    era = _era(films)
    by_xref: dict[tuple[str, str], Film] = {}
    by_key: dict[str, list[Film]] = defaultdict(list)
    for f in films:
        for m in f.members:
            if m.source and m.xref:
                by_xref[(m.source, m.xref)] = f
            for k in _keys(m, norm):
                by_key[k].append(f)
    rows: list[dict[str, Any]] = []
    flag_counts: Counter = Counter()
    matched = 0
    for cf in canon_films(canon):
        reg = next((by_xref[(m.source, m.xref)] for m in cf.members if (m.source, m.xref) in by_xref), None)
        how = "xref" if reg else None
        if reg is None:
            year = cf.pick("year", ("hfgk_online",))
            cands = {f.id: f for m in cf.members for k in _keys(m, norm) for f in by_key.get(k, [])
                     if year is None or f.pick("year") is None or abs(f.pick("year") - year) <= 1}
            if len(cands) == 1:
                reg, how = next(iter(cands.values())), "title"
        title = cf.pick("title")
        base = {"canon_film_id": cf.id, "title": title, "year": cf.pick("year"),
                "canon_sources": "; ".join(sorted({m.source for m in cf.members if m.source}))}
        if reg is None:
            flag_counts["not_in_register"] += 1
            rows.append({**base, "register_film_id": "", "wikidata": "", "matched_by": "",
                         "flag": "not_in_register", "detail": "no register film with this source id or title and year"})
            continue
        matched += 1
        combined = Film(cf.id, [m for m in cf.members] + [m for m in reg.members if m.source != "hfgk_online"
                                                        or not any(c.source == "hfgk_online" for c in cf.members)])
        flags = film_flags(combined, era, norm)
        hfgk_reg = reg.one("hfgk_online")
        for m in cf.members:
            if m.source == "hfgk_online" and hfgk_reg and m.song_count is not None and hfgk_reg.song_count is not None \
                    and m.song_count != hfgk_reg.song_count:
                flags.append({"kind": "song_count_changed",
                              "detail": f"canon {m.song_count} songs, HFGK now {hfgk_reg.song_count}"})
        flags = [f for f in flags if f["kind"] not in ("not_in_hfgk",)]
        for f in flags or [{"kind": "", "detail": ""}]:
            if f["kind"]:
                flag_counts[f["kind"]] += 1
            rows.append({**base, "register_film_id": reg.id, "wikidata": reg.pick("qid", ("wikidata",)) or "",
                         "matched_by": how, "flag": f["kind"], "detail": f["detail"]})
    out.mkdir(parents=True, exist_ok=True)
    slug = canon.pack.slug
    _write_csv(out / f"canon-check-{slug}.csv", rows)
    canon_count = len({r["canon_film_id"] for r in rows})
    summary = {"maestro": slug, "canon_films": canon_count, "matched_to_register": matched,
               "flags": dict(flag_counts.most_common()),
               "films_with_any_flag": len({r["canon_film_id"] for r in rows if r["flag"]})}
    (out / f"canon-check-{slug}.json").write_text(json.dumps(summary, indent=2), "utf-8")
    return summary


# ---- planning enrichment crawls ---------------------------------------------------------
#
# Each enrichment reads films the register already has, in batches, so it queues its own jobs from the
# store: `maestro-films plan --what NAME` queues them, `maestro-crawl --pack ...` (no --seed) runs them.

def _wikidata_batches(store: Store, size: int) -> list[str]:
    from maestro.extractors.wikidata_film_details import details_url

    qids = sorted({r["v"] for r in store.conn.execute(
        """SELECT value #>> '{}' AS v FROM claim WHERE field = 'wikidata' AND source_id = 'wikidata'
           AND status = 'active'""").fetchall()}, key=lambda q: int(q[1:]))
    return [details_url(qids[i:i + size]) for i in range(0, len(qids), size)]


def _song_pages(store: Store, size: int) -> list[str]:
    """The English Wikipedia article of every film HFGK does not list (its songs come from HFGK)."""
    from maestro.extractors.wikipedia_film_songs import film_url

    out = []
    for film in sorted(films_in_groups(store), key=lambda f: -(f.pick("year") or 0)):
        if film.one("hfgk_online"):
            continue
        article = film.pick("article", ("wikidata", "wikipedia"))
        if article:
            out.append(film_url(article))
    return out


PLANS: dict[str, tuple[str, int, Any]] = {
    "wikidata": ("wikidata_film_details", 80, _wikidata_batches),
    "wikipedia_songs": ("wikipedia_film_songs", 1, _song_pages),
}


def plan(store: Store, what: str, batch: int | None = None) -> dict[str, Any]:
    extractor, default, urls = PLANS[what]
    queued = 0
    with store.conn.transaction():
        for url in urls(store, batch or default):
            job = store.enqueue_crawl(url, extractor, "enrich", priority=10 if what == "wikidata" else 5)
            queued += 0 if job.get("already_queued") else 1
    return {"what": what, "extractor": extractor, "queued": queued}


# ---- command line ---------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="maestro-films", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["match", "report", "check-canon", "decide", "plan"])
    p.add_argument("--what", choices=list(PLANS), help="plan: which enrichment to queue (then run maestro-crawl "
                                                        "with no --seed)")
    p.add_argument("--batch", type=int, default=0, help="plan: films per request (default per source)")
    p.add_argument("decision", nargs="*", help="decide: FILM_ID FLAG VALUE (e.g. 7298 year_mismatch 1935; "
                                                 "4566 composer_mismatch 'Khemchand Prakash'; 812 not_in_hfgk ok)")
    p.add_argument("--note", help="decide: why (kept with the decision)")
    p.add_argument("--pack", required=True, help="a register pack, e.g. maestros/hindi-films")
    p.add_argument("--dsn", help="register store (default $MAESTRO_DSN_<SLUG>)")
    p.add_argument("--out", type=Path, help="folder for report files (default <data dir>/<slug>/report)")
    p.add_argument("--canon", action="append", default=[], help="check-canon: maestros/<slug>=<dsn> (repeatable)")
    args = p.parse_args(argv)
    pack = load_pack(args.pack)
    if pack.data["identity"].get("kind") != "register":
        p.error(f"{args.pack} is not a register pack (identity.kind: register)")
    dsn = config.dsn_for(pack.slug, args.dsn)
    if not dsn:
        p.error(f"--dsn (or {config.dsn_env_name(pack.slug)}) is required")
    store = Store.connect(dsn, pack)
    store.migrate()
    store.sync_sources()
    out = args.out or (config.data_dir() / pack.slug / "report")
    if args.command == "match":
        result: Any = match(store).as_dict()
    elif args.command == "report":
        result = report(store, out)
    elif args.command == "plan":
        if not args.what:
            p.error("plan needs --what " + "|".join(PLANS))
        result = plan(store, args.what, args.batch or None)
    elif args.command == "decide":
        if len(args.decision) != 3:
            p.error("decide needs FILM_ID FLAG VALUE")
        fid, flag, value = args.decision
        result = decide(store, int(fid), flag, value, args.note)
    else:
        if not args.canon:
            p.error("check-canon needs --canon maestros/<slug>=<dsn>")
        result = []
        for spec in args.canon:
            folder, _, cdsn = spec.partition("=")
            cpack = load_pack(folder)
            canon = Store.connect(config.dsn_for(cpack.slug, cdsn or None), cpack)
            result.append(check_canon(store, canon, out))
            canon.close()
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
