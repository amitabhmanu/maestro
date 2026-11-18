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
FILM_ROLES = ("composer", "lyricist", "director")


# ---- names ---------------------------------------------------------------------

def _native_key(text: str | None) -> str:
    """A Devanagari (or other script) title folded for matching: nukta and chandrabindu dropped."""
    t = unicodedata.normalize("NFC", text or "")
    t = t.replace("़", "").replace("ँ", "ं")
    return re.sub(r"[\s\-–—.,!?'\"]+", "", t)


_JOINT = re.compile(r"\s*(?:\s-\s|–|—|&|/|\+|,|\band\b)\s*")


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
    ta = [_name_words(x, norm) for x in a]
    tb = [_name_words(x, norm) for x in b]
    for x in ta:
        for y in tb:
            if x and "".join(x) == "".join(y):  # "Master Muhammad" / "Master Mohammed"
                return True
            short, long_ = (x, y) if len(x) <= len(y) else (y, x)
            if short and (len(short) >= 2 or len(short[0]) >= 4) and all(w in long_ for w in short):
                return True
    return False


_HONORIFICS = {"master", "mastr", "pandit", "pt", "pandi", "ustad", "ustaad", "shri", "sri", "shree", "late", "dr",
               "mr", "md", "seth"}


def _name_words(name: str, norm: Normaliser | None = None) -> list[str]:
    """A person's name as consonant skeletons of its words, titles and footnote marks dropped."""
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)|\d+", " ", name or "")
    words = (norm.person(text) if norm else fold(text)).replace("-", " ").split()
    out = [skeleton(w) for w in words if w not in _HONORIFICS]
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
             AND c.field IN ('xref', 'title', 'first_line', 'title_hi', 'title_native', 'year', 'period',
                             'wikidata', 'imdb', 'wikipedia', 'song_count', 'credit', 'credit_unlinked')
           ORDER BY c.id""", params).fetchall()
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

@dataclass
class MatchStats:
    by_article: int = 0
    by_title: int = 0
    queued: int = 0
    unmatched: Counter = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {"merged_by_article": self.by_article, "merged_by_title": self.by_title,
                "queued_for_review": self.queued, "unmatched": dict(self.unmatched)}


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
                if clear and best >= (0 if len(scored) == 1 else 2):
                    if join(top, f, f"film {f.title!r} ({f.year}) / {top.title!r} ({top.year}): "
                                     f"same title key, score {best}"):
                        stats.by_title += 1
                else:
                    subjects = [groups.find(f.id), *(groups.find(a.id) for _, a in scored[:4])]
                    if queue_review(store, "film_match", subjects, {
                            "film": {"title": f.title, "year": f.year, "source": f.source},
                            "candidates": [{"title": a.title, "year": a.year, "source": a.source, "qid": a.qid,
                                            "score": s} for s, a in scored[:4]]}, priority=2):
                        stats.queued += 1
        for f in films.values():
            if f.source and f.source != "wikidata" and "wikidata" not in groups.sources(f.id):
                stats.unmatched[f.source] += 1
    return stats


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
    for film in films:
        row = film_row(film)
        flags = film_flags(film, era, norm)
        items = sorted({i for m in film.members for i in open_matches.get(groups.find(m.id), [])}
                       | {i for m in film.members for i in open_matches.get(m.id, [])})
        if items:
            flags.append({"kind": "match_review", "detail": f"review items {items}"})
        row["flags"] = "; ".join(sorted({f["kind"] for f in flags}))
        rows.append(row)
        songs += row["song_count"] or 0
        by_decade[(row["year"] // 10 * 10) if row["year"] else "undated"] += 1
        combos[row["sources"]] += 1
        for f in flags:
            flag_counts[f["kind"]] += 1
            if f["kind"] == "year_mismatch":
                severity[f["severity"]] += 1
            review.append({"film_id": film.id, "title": row["title"], "year": row["year"],
                           "wikidata": row["wikidata"], "hfgk_id": row["hfgk_id"], "flag": f["kind"],
                           "severity": f.get("severity", ""), "detail": f["detail"]})
    rows.sort(key=lambda r: (r["year"] or 9999, (r["title"] or "").casefold()))
    review.sort(key=lambda r: (r["flag"], r["year"] or 9999, (r["title"] or "").casefold()))
    _write_csv(out / "films.csv", rows)
    _write_csv(out / "review.csv", review)
    hfgk_films = sum(1 for f in films if f.by_source("hfgk_online"))
    summary = {
        "films": len(films),
        "films_with_wikidata": sum(1 for f in films if f.by_source("wikidata")),
        "films_with_hfgk": hfgk_films,
        "hfgk_matched_to_wikidata": sum(1 for f in films if f.by_source("hfgk_online") and f.by_source("wikidata")),
        "hfgk_years": list(era) if era else None,
        "songs_hfgk": songs,
        "by_decade": {str(k): v for k, v in sorted(by_decade.items(), key=lambda kv: str(kv[0]))},
        "by_sources": dict(combos.most_common()),
        "flags": dict(flag_counts.most_common()),
        "year_mismatch_severity": dict(severity),
        "open_match_reviews": store.conn.execute(
            "SELECT count(*) AS n FROM review_item WHERE status = 'open' AND kind = 'film_match'").fetchone()["n"],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), "utf-8")
    (out / "summary.md").write_text(_summary_md(summary), "utf-8")
    return summary


def films_snapshot(store: Store) -> dict[str, Any]:
    """The register as one JSON-ready dict for the canon explorer's Films view: every film's row
    with its flags. Film titles and names only: no song titles, so no lyric text."""
    from datetime import datetime, timezone

    norm = Normaliser.for_store(store)
    films = films_in_groups(store)
    era = _era(films)
    out = []
    for film in films:
        row = film_row(film)
        flags = film_flags(film, era, norm)
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
            **({"songs_listed": hfgk.songs} if hfgk and hfgk.songs != hfgk.song_count else {}),
        })
    out.sort(key=lambda r: (r["year"] or 9999, (r["title"] or "").casefold()))
    d = store.pack.data["identity"]
    return {"format": 1, "register": {"slug": d["slug"], "name": d["name"]},
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "hfgk_years": list(era) if era else None, "films": out}


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


# ---- command line ---------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="maestro-films", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["match", "report", "check-canon"])
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
