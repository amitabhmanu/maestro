"""Popularity and rare-gem scoring (design doc: popularity and gem scoring), read from the finished canon.

Evidence comes in two kinds:

* **Mentions.** A source that ranks or honours songs (a chart, an award list, an encyclopedia
  article) names them in its own words. Extractors record each as a `mention` claim on the
  maestro's entity, through `record_mention`: the signal name, the title and year as the source
  gives them, the film or album when it names one, a `strength` (higher means more popular) and a
  short `note` for people. Mentions add no units to the canon, so they never distort the
  completeness estimates; they are matched to units when scores are computed, and a mention that
  matches no unit is reported (a charted song missing from the canon is a gap).
* **Derived signals**, computed from claims the canon already holds: `reissues` (distinct releases
  and catalogue numbers a unit appears on: compilations keep the hits alive) and `remakes` (other
  recordings the maestro made of the same title: artists re-cut their hits).

The pack's `popularity_signals` puts each signal in one of four facets: contemporary (charts and
awards at the time), enduring (reissues, articles, streams), penetration (covers, reuse) and
esteem (critics, curated lists, awards for quality). A signal not listed in the pack is ignored.

Reach (popularity without expert esteem, 0 to 1, shown as 0-100) and the low-popularity label are
one measure: a unit is popular (reach 0.5 or more) when any signal says so (a chart, award, cover or
article; a count such as YouTube views, Last.fm listeners or releases above its cut-off, see
`count_thresholds`), else low (reach under 0.5). Within each half, a signal's score is the unit's
rank among the units that have that signal in the same half, so a unit without a signal is unknown
for it, not beaten. Counts add up over a unit's evidence (views over its uploads, listeners over
its Last.fm tracks; each upload or track once). A facet takes its best signal; reach is 0.7 x the
best facet plus 0.3 x the weighted mean of the facets this canon has any data for, kept in its half.
Popularity (with esteem) adds the experts' word the same way, for ordering leads.

A **gem** (rare gem) is a good unit that never became popular. Low popularity is the first, hard
filter (`low_popularity`); within that set, quality comes only from expert gem lists that pass
their purity test (see `_expert_quality`) or the user's own pick, never from popularity. Units with no expert word but other signs of worth (a strong team, a hit beside
them, reissued but never a hit) are **leads**, listed separately. The user's picks and "not a gem"
marks calibrate how much each expert voice counts. Every gem and lead lists its reasons.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from maestro.completeness import Unit, load_units
from maestro.pack import load_pack
from maestro.resolve import (TITLE_LETTERS, Groups, Normaliser, WholeTitleIndex, _prefix_similarity, _squash,
                             catalogue_key, fold, skeleton,
                             title_similarity, whole_title_similarity)
from maestro.store import Store

FACETS = ("contemporary", "enduring", "penetration", "esteem")
DEFAULT_WEIGHTS = {"contemporary": 0.35, "enduring": 0.3, "penetration": 0.2, "esteem": 0.15}
DERIVED = ("reissues", "remakes")
PRESENCE_CAP = 0.4

GEM_MAX_REACH = 0.5
GEM_MIN_QUALITY = 0.6
FRAMING = {"gem": 1.0, "praise": 0.6, "listed": 0.25}  # how an expert speaks of a unit
LOW_POP_REISSUES = 2  # a unit on at most this many releases or catalogue numbers is never popular for it
COUNT_SIGNAL_SHARE = 0.2  # an enduring signal naming more of the canon than this is a count (see count_thresholds)
COUNT_MIN_DISTINCT = 20  # ... as is one whose strengths take at least this many values (listeners, views)
COUNT_POPULAR_QUANTILE = 0.9  # on a count signal, a unit is popular in the top 10% of the units it names
# ... or at these counts, whichever is lower: a source that only holds the well-known uploads (label
# channels) makes its own top 10% far too high. A pack sets its own in `popularity_signals.popular_at`.
POPULAR_AT = {"youtube": 1_000_000, "lastfm": 100}
LOW_BAND = 0.5  # reach under this is low popularity, at or over it popular (see `_banded`)
LIST_PURITY_MIN = 0.9  # a gem list counts only if this share of its units are low-popularity
LIST_INTENTS = ("gem_list", "best_of", "themed")
SELECTIVE_SHARE = 0.01  # a voice naming this share of the canon or less has its full say
VOICE_MAX = 0.9  # no single voice makes a gem certain
CALIBRATION_MIN_PICKS = 10  # picks needed before voices are weighted by agreement with the user
TEAM_MIN_UNITS = 4  # a collaborator needs this many other units in the canon to vouch for a team
TEAM_SHRINK = 10  # pseudo-units of popularity 0 added to a collaborator's average
TEAM_WEIGHT = 0.75
SIBLING_WEIGHT = 0.65
REDISCOVERY_COVERAGE = 0.05  # contemporary signals must reach this share of the canon
REDISCOVERY_CAPTURE = 0.4  # ... and name this share of its most enduring units, for "never a hit"
REDISCOVERY_MIN = 0.85
SHADOW_CONTAINERS = {"film", "album", "score", "show", "release"}


# ---- writing mentions (used by extractors) -------------------------------------------

def record_mention(ctx, source_id: str, signal: str, title: str, *, strength: float, note: str,
                   year: int | None = None, container: str | None = None, extractor: str | None = None,
                   cap: float | None = None, **detail: Any) -> int:
    """Record that `source_id` names `title` (by this maestro) with popularity evidence `signal`.

    `strength` orders units within one signal (a chart peak of 1 is stronger than 40: pass 101 - peak).
    `cap` (0-1) limits what the signal can score: weak evidence that many units share (released as
    a single) should not rank like a chart hit. `detail` keeps anything else worth showing (peak,
    rank, award, qid, url). Values must be JSON."""
    store = ctx.store
    value = {"signal": signal, "title": title, "strength": strength, "note": note,
             **({"year": year} if year else {}), **({"container": container} if container else {}),
             **({"cap": cap} if cap is not None else {}),
             **{k: v for k, v in detail.items() if v not in (None, "", [])}}
    return store.upsert_claim(store.maestro_entity(), "mention", value=value, source_id=source_id,
                              extractor=extractor, extractor_version="1", raw_page_id=ctx.raw_page_id)


def names_maestro(store: Store, text: str) -> bool:
    """Whether a credit line ("Mohammed Rafi, Geeta Dutt") names the maestro under any listed variant."""
    ident = store.pack.data["identity"]
    folded = f" {fold(text)} "
    return any(f" {fold(n)} " in folded for n in [ident["name"], *ident.get("variants", [])] if fold(n))


# ---- reading the canon ------------------------------------------------------------------

@dataclass
class Evidence:
    signal: str
    strength: float
    note: str
    source: str
    url: str | None = None
    cap: float = 1.0
    expert: str | None = None  # esteem: the voice (an author) when a source carries several
    framing: str | None = None  # esteem: gem, praise or listed (see FRAMING)


@dataclass
class Scored:
    unit: Unit
    titles: set[str] = field(default_factory=set)
    evidence: list[Evidence] = field(default_factory=list)
    signals: dict[str, float] = field(default_factory=dict)  # signal -> raw strength (best)
    signal_scores: dict[str, float] = field(default_factory=dict)
    facets: dict[str, float] = field(default_factory=dict)
    popularity: float = 0.0
    reach: float = 0.0
    picked: bool = False
    quality: float = 0.0  # expert opinion, 0-1 (1 for the user's pick)
    gem: float = 0.0
    gem_reasons: list[str] = field(default_factory=list)
    lead: float = 0.0  # worth a listen, no expert word yet
    lead_reasons: list[str] = field(default_factory=list)
    low: bool = True  # low popularity (`low_popularity`): the user's gem tags show as rare gems
    parts: dict[str, int] = field(default_factory=dict)  # count signal -> uploads or tracks added up
    facet_of: dict[str, str] = field(default_factory=dict, repr=False)


def _facet_of(pack_data: dict[str, Any]) -> dict[str, str]:
    out = {}
    for facet, names in (pack_data.get("popularity_signals") or {}).items():
        if facet in FACETS:
            for n in names:
                out[n] = facet
    return out


def _weights(pack_data: dict[str, Any]) -> dict[str, float]:
    w = dict(DEFAULT_WEIGHTS)
    w.update((pack_data.get("popularity_signals") or {}).get("weights") or {})
    return w


def _unit_records(store: Store, groups: Groups, units: dict[int, Unit]) -> dict[int, int]:
    members = {}
    for r in store.conn.execute("SELECT id FROM entity WHERE type = %s", (store.pack.unit,)).fetchall():
        root = groups.find(r["id"])
        if root in units:
            members[r["id"]] = root
    return members


def _unit_facts(store: Store, members: dict[int, int], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    return store.conn.execute(
        """SELECT entity_id, field, value, source_id FROM claim
           WHERE entity_id = ANY(%s) AND status = 'active' AND field = ANY(%s)""",
        (list(members), list(fields))).fetchall()


def _container_titles(store: Store, groups: Groups) -> dict[int, set[str]]:
    out: dict[int, set[str]] = defaultdict(set)
    for r in store.conn.execute(
        """SELECT e.id, e.label, c.value FROM entity e
           LEFT JOIN claim c ON c.entity_id = e.id AND c.field = 'title' AND c.status = 'active'
           WHERE e.type = 'container'""").fetchall():
        root = groups.find(r["id"])
        out[root].add(r["label"])
        if isinstance(r["value"], str):
            out[root].add(r["value"])
    return out


def _mentions(store: Store, include_private: bool) -> list[dict[str, Any]]:
    vis = ("publishable", "private") if include_private else ("publishable",)
    groups = Groups(store)
    maestro = groups.find(store.maestro_entity())
    ids = [r["id"] for r in store.conn.execute("SELECT id FROM entity WHERE type = 'person'").fetchall()
           if groups.find(r["id"]) == maestro]
    return store.conn.execute(
        """SELECT c.id, c.value, c.source_id, p.url AS page FROM claim c
           LEFT JOIN raw_page p ON p.id = c.raw_page_id
           WHERE c.entity_id = ANY(%s) AND c.field = 'mention' AND c.status = 'active'
             AND c.visibility = ANY(%s) ORDER BY c.id""", (ids, list(vis))).fetchall()


# ---- matching mentions to units ---------------------------------------------------------

TITLE_MATCH = 0.9       # whole-title likeness needed when the mention names no film or album
OPENING_ALONE = 0.92    # opening-line likeness needed when it names none and no whole title matches
OPENING_ALONE_LETTERS = 12  # ... for a mention title at least this long (squashed letters)
OPENING_ALONE_LEAD = 0.05   # ... and ahead of any candidate in another film or album by this much
OPENING_MATCH = 0.82    # opening-line likeness needed inside a matched container
CONTAINER_MATCH = 0.85


_FROM_TAIL = re.compile(r"""\s*(?:[,\-–—:]\s*)?[\(\[\{]?\s*From\s+(?:the\s+(?:film|movie)\s+)?['"“”‘’]{0,2}"""
                        r"""([^'"“”‘’\)\]\}]+?)['"“”‘’]{0,2}\s*[\)\]\}]?\s*$""", re.I)
_VERSION_TAIL = re.compile(r"\s*(?:[-–—]\s*|[\(\[]\s*)?(?:revival|remaster(?:ed)?|version\s*\d*|pt\.?\s*\d+|part\s*\d+"
                           r"|(?:male|female)(?:\s+vocals?)?|sad|happy|jhankar)\s*[\)\]]?\s*$", re.I)


def mention_titles(title: str, container: str | None) -> list[tuple[str, str | None]]:
    """The ways a stream or upload title may name a song, best first, as (title, film or album).

    Uploads and stream tracks wrap the song's opening line in their own words: 'Title - From "Film"',
    "Title, From ''Film''", 'Title {From Film}', 'Title -- Singer -- Film', 'Film: Title', a site's
    '::www.site::' tag, or a version note ('Pt. 2', '(Male Vocals)', '- Revival'). Older mention
    claims were stored before the extractors learnt some of these, so they are read again here."""
    t = re.sub(r"::\s*www\.[^:]*::|\bwww\.\S+", " ", title or "").strip()
    out: list[tuple[str, str | None]] = []
    m = _FROM_TAIL.search(t)
    if m and m.start() > 0:
        container = container or m.group(1).strip()
        t = t[:m.start()].strip(" -,")
    for _ in range(2):
        t = _VERSION_TAIL.sub("", t).strip(" -,")
    parts = [p.strip() for p in re.split(r"\s+--\s+", t) if p.strip()]
    if len(parts) >= 2:
        out.append((parts[0], container or parts[-1]))
    out.append((t, container))
    m = re.match(r"^([^:]{3,40})\s*::?\s*(.{8,})$", t)
    if m:
        out.append((m.group(2).strip(), container or m.group(1).strip()))
    if t != (title or "").strip() or container:
        out.append((title, None))
    seen, uniq = set(), []
    for x in out:
        if x[0] and x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _keys(norm: Normaliser, title: str) -> set[str]:
    return {k for k in (norm.title(title), norm.title(re.sub(r"[()\[\]]", " ", title))) if k}


class MentionMatcher:
    """Finds the canon unit a mention names. With a film or album name, the unit must be in a
    matching container and its opening line must match; otherwise the whole title must match. Of
    several recordings of one title, the one made in or closest before the mention's year wins."""

    def __init__(self, scored: dict[int, Scored], norm: Normaliser, ctitles: dict[int, set[str]]):
        self.scored, self.norm = scored, norm
        self.ukeys = {uid: sorted({k for t in s.titles for k in _keys(norm, t)}) for uid, s in scored.items()}
        self.ckeys = {cid: {norm.container(t) for t in ts} - {""} for cid, ts in ctitles.items()}
        self._cmatch: dict[tuple[int, str], bool] = {}
        # Tens of thousands of stream mentions meet a canon of thousands: titles are indexed by length
        # with cheap bounds before the full comparison, and units by container.
        self.windex = WholeTitleIndex({uid: set(ks) for uid, ks in self.ukeys.items()})
        self.by_container: dict[int, list[int]] = defaultdict(list)
        for uid, s in scored.items():
            if s.unit.container is not None:
                self.by_container[s.unit.container].append(uid)
        self._cunits: dict[str, list[int]] = {}
        self.by_opening: dict[str, list[int]] = defaultdict(list)  # first consonants -> units
        for uid, ks in self.ukeys.items():
            for k in {skeleton(k)[:4] for k in ks}:
                if len(k) == 4:
                    self.by_opening[k].append(uid)

    def _container_units(self, ckey: str) -> list[int]:
        if ckey not in self._cunits:
            self._cunits[ckey] = [uid for cid, uids in self.by_container.items()
                                  if self._in_container(cid, ckey) for uid in uids]
        return self._cunits[ckey]

    def _in_container(self, cid: int | None, ckey: str) -> bool:
        if cid is None:
            return False
        k = (cid, ckey)
        if k not in self._cmatch:
            self._cmatch[k] = any(whole_title_similarity(t, ckey) >= CONTAINER_MATCH for t in self.ckeys.get(cid, ()))
        return self._cmatch[k]

    def match(self, m: dict[str, Any]) -> int | None:
        """The unit a mention names: its title as given, then the readings `mention_titles` finds, and
        last the opening line alone (no film named, no whole title alike)."""
        for title, container in mention_titles(m["title"], m.get("container")):
            uid = self._match({**m, "title": title, "container": container})
            if uid is None and container:
                uid = self._match({**m, "title": title, "container": None})
            if uid is not None:
                return uid
        return self._match_opening(m)

    def _match_opening(self, m: dict[str, Any]) -> int | None:
        best: dict[int, float] = {}
        for title, _c in mention_titles(m["title"], m.get("container")):
            for a in _keys(self.norm, title):
                if len(_squash(a.replace(" ", ""))) < OPENING_ALONE_LETTERS:
                    continue
                sa = _squash(a.replace(" ", ""))[:TITLE_LETTERS]
                for uid in self.by_opening.get(skeleton(a)[:4], ()):
                    # Openings only: with no film to vouch for it, a later line is too weak a match.
                    sim = max(_prefix_similarity(sa, _squash(b.replace(" ", "")), OPENING_ALONE) for b in self.ukeys[uid])
                    if sim >= OPENING_ALONE and sim > best.get(uid, 0.0):
                        best[uid] = sim
        if not best:
            return None
        ranked = sorted(best.items(), key=lambda kv: (-kv[1], self.scored[kv[0]].unit.tier or "Z", kv[0]))
        top, sim = ranked[0]
        where = self.scored[top].unit.container
        for uid, other in ranked[1:]:
            if (where is None or self.scored[uid].unit.container != where) and sim - other < OPENING_ALONE_LEAD:
                return None  # two films' songs open alike: say nothing rather than guess
        return top

    def _match(self, m: dict[str, Any]) -> int | None:
        mkeys = _keys(self.norm, m["title"])
        if not mkeys:
            return None
        year = m.get("year")
        ckey = self.norm.container(m["container"]) if m.get("container") else None
        if ckey:
            pool = []
            for uid in self._container_units(ckey):
                sim = max(((title_similarity(a, b, OPENING_MATCH) if min(len(a), len(b)) >= 10 else whole_title_similarity(a, b))
                           for a in mkeys for b in self.ukeys[uid]), default=0.0)
                if sim >= OPENING_MATCH:
                    pool.append((uid, sim))
        else:
            pool = list(self.windex.matches(set(mkeys), TITLE_MATCH).items())
        cands = []
        for uid, sim in pool:
            u = self.scored[uid].unit
            if year and u.year:
                gap = (year - u.year) if u.year <= year else 3 * (u.year - year)
            else:
                gap = 50
            cands.append((-min(sim, 0.97), gap, -sim, u.tier or "Z", uid))
        if not cands:
            return None
        cands.sort()
        if year and not ckey and cands[0][1] > 15:  # the only same-title unit is from another era
            return None
        return cands[0][-1]


# ---- scoring ----------------------------------------------------------------------------

def _rank_scores(values: dict[int, float], universe: list[int]) -> dict[int, float]:
    """Share of the canon each unit beats on one signal (mid-rank for ties); 0 without the signal."""
    n = len(universe)
    if not n:
        return {}
    vals = sorted(values.get(u, 0.0) for u in universe)
    out = {}
    import bisect
    for u in universe:
        v = values.get(u, 0.0)
        if v <= 0:
            out[u] = 0.0
            continue
        lo, hi = bisect.bisect_left(vals, v), bisect.bisect_right(vals, v)
        out[u] = (lo + (hi - lo) / 2) / n
    return out


def _combine(facets: dict[str, float], present: list[str], weights: dict[str, float]) -> float:
    if not present:
        return 0.0
    vals = [facets.get(f, 0.0) for f in present]
    wsum = sum(weights.get(f, 0.0) for f in present) or 1.0
    mean = sum(facets.get(f, 0.0) * weights.get(f, 0.0) for f in present) / wsum
    return 0.7 * max(vals) + 0.3 * mean


def _popular_at(pack_data: dict[str, Any]) -> dict[str, float]:
    out = dict(POPULAR_AT)
    out.update((pack_data.get("popularity_signals") or {}).get("popular_at") or {})
    return out


def _count_signals(scored: dict[int, Scored], facet_of: dict[str, str]) -> set[str]:
    """Enduring signals that are counts, not lists (see `count_thresholds`); reissues always are."""
    n = max(1, len(scored))
    out = set()
    for sig in {x for s in scored.values() for x in s.signals}:
        if facet_of.get(sig) != "enduring":
            continue
        vals = [s.signals[sig] for s in scored.values() if sig in s.signals]
        if sig == "reissues" or len(vals) / n > COUNT_SIGNAL_SHARE or len(set(vals)) >= COUNT_MIN_DISTINCT:
            out.add(sig)
    return out


def _sum_counts(scored: dict[int, Scored], facet_of: dict[str, str]) -> set[str]:
    """Add up each count signal over a unit's evidence: a song uploaded twice, or listed by Last.fm under
    three spellings, has the views or listeners of all of them. Each upload or track counts once (by
    its url, else its note). Reissues are already a count of distinct releases. Returns the count
    signals."""
    counts = _count_signals(scored, facet_of)
    for s in scored.values():
        for sig in counts - {"reissues"}:
            if sig not in s.signals:
                continue
            seen: dict[str, float] = {}
            for e in s.evidence:
                if e.signal == sig:
                    k = e.url or e.note
                    seen[k] = max(seen.get(k, 0.0), e.strength)
            s.signals[sig] = sum(seen.values())
            s.parts[sig] = len(seen)
    return counts


def _popular_by(s: Scored, sig: str, thresholds: dict[str, float]) -> bool:
    """Whether this one signal makes the unit popular (the rule `low_popularity` applies)."""
    if sig not in s.signals:
        return False
    if sig in thresholds:
        return s.signals[sig] > thresholds[sig]
    if sig == "reissues":
        return s.signals[sig] > LOW_POP_REISSUES
    return s.facet_of.get(sig) in ("contemporary", "penetration", "enduring")


def _banded(scored: dict[int, Scored], signals: list[str], facet_of: dict[str, str],
            thresholds: dict[str, float]) -> None:
    """Each signal's score for each unit, in the unit's half: units the signal makes popular score
    0.5-1 by their rank among those units; other units that have the signal score 0-0.5 by their rank
    among those. Units without the signal get nothing for it (unknown, not beaten). Esteem (expert
    word) is no reach: it scores the share of the whole canon a unit beats, as before."""
    universe = list(scored)
    caps = {sig: min((e.cap for s in scored.values() for e in s.evidence if e.signal == sig), default=1.0)
            for sig in signals}
    for sig in signals:
        if facet_of.get(sig) == "esteem":
            for u, x in _rank_scores({u: s.signals.get(sig, 0.0) for u, s in scored.items()}, universe).items():
                if x:
                    scored[u].signal_scores[sig] = x * caps[sig]
            continue
        holders = [u for u, s in scored.items() if sig in s.signals]
        high = [u for u in holders if _popular_by(scored[u], sig, thresholds)]
        hs = set(high)
        for group, base in ((high, LOW_BAND), ([u for u in holders if u not in hs], 0.0)):
            for u, x in _rank_among({u: scored[u].signals[sig] for u in group}).items():
                scored[u].signal_scores[sig] = base + ((1 - LOW_BAND) * x if base else LOW_BAND * 0.999 * x) * caps[sig]


def _rank_among(values: dict[int, float]) -> dict[int, float]:
    """Each unit's rank among these units only: the share of them it equals or beats (the strongest 1)."""
    import bisect
    vals = sorted(values.values())
    n = len(vals)
    return {u: bisect.bisect_right(vals, v) / n for u, v in values.items()}


def _band_combine(s: Scored, facets: list[str], weights: dict[str, float]) -> float:
    """Combine facets (as `_combine`) within the unit's half, so a low unit's reach stays under 0.5 and
    a popular one's at 0.5 or over: the label and the number never disagree."""
    if not facets:
        return 0.0
    local = {}
    for f in facets:
        v = s.facets.get(f, 0.0)
        if f == "esteem":
            local[f] = v
        elif s.low:
            local[f] = v / LOW_BAND
        else:
            local[f] = max(0.0, (v - LOW_BAND) / (1 - LOW_BAND))
    x = min(_combine(local, facets, weights), 0.999)
    return x * LOW_BAND if s.low else LOW_BAND + (1 - LOW_BAND) * x


def popular_because(s: Scored, thresholds: dict[str, float]) -> list[str]:
    """In plain words, the signals that make a unit popular (empty for a low-popularity unit)."""
    out = []
    for sig in sorted(s.signals, key=lambda g: FACETS.index(s.facet_of[g]) if s.facet_of.get(g) in FACETS else 9):
        if not _popular_by(s, sig, thresholds):
            continue
        if sig in thresholds and sig != "reissues":
            n = s.parts.get(sig, 1)
            out.append(f"{s.signals[sig]:,.0f} {sig.replace('_', ' ')}"
                       + (f" over {n} uploads or tracks" if n > 1 else "")
                       + f" (popular above {thresholds[sig]:,.0f})")
        elif sig == "reissues":
            out.append(f"on {s.signals[sig]:.0f} releases or catalogue numbers "
                       f"(popular above {thresholds.get(sig, LOW_POP_REISSUES):.0f})")
        else:
            best = max((e for e in s.evidence if e.signal == sig), key=lambda e: e.strength, default=None)
            out.append(best.note if best else sig)
    return out


def score_canon(store: Store, include_private: bool = False) -> dict[str, Any]:
    pack = store.pack
    norm = Normaliser.for_store(store)
    groups = Groups(store)
    units = load_units(store, groups)
    members = _unit_records(store, groups, units)
    scored = {uid: Scored(u, {u.label}) for uid, u in units.items()}
    facet_of = _facet_of(pack.data)
    weights = _weights(pack.data)
    maestro = groups.find(store.maestro_entity())

    rel: dict[int, set[str]] = defaultdict(set)
    cat: dict[int, set[str]] = defaultdict(set)
    picks: set[int] = set()
    not_gems: set[int] = set()
    by_qid: dict[str, int] = {}
    for r in _unit_facts(store, members, ("title", "first_line", "on_release", "catalogue", "pick", "film", "xref")):
        uid = members[r["entity_id"]]
        v = r["value"]
        if r["field"] in ("film", "xref") and isinstance(v, str) and (r["field"] == "film" or r["source_id"] == "wikidata"):
            q = re.search(r"\b(Q\d+)\b", v)
            if q:
                by_qid.setdefault(q.group(1), uid)
        elif r["field"] in ("title", "first_line") and isinstance(v, str):
            scored[uid].titles.add(v)  # first_line: mentions quoting lyrics keep only their first line
        elif r["field"] == "on_release" and isinstance(v, str):
            rel[uid].add(v)
        elif r["field"] == "catalogue" and isinstance(v, str):
            k = catalogue_key(v)
            if k:
                cat[uid].add(k)
        elif r["field"] == "pick" and r["source_id"] == "user":
            (not_gems if v is False else picks).add(uid)

    def add(uid: int, ev: Evidence) -> None:
        s = scored[uid]
        s.evidence.append(ev)
        s.signals[ev.signal] = max(s.signals.get(ev.signal, 0.0), ev.strength)

    if "reissues" in facet_of:
        for uid in scored:
            n = max(len(rel[uid]), len(cat[uid]))
            if n:
                add(uid, Evidence("reissues", n, f"on {n} release{'s' if n != 1 else ''} or catalogue numbers",
                                  "canon"))
    if "remakes" in facet_of:
        by_title: dict[str, list[int]] = defaultdict(list)
        for uid, s in scored.items():
            k = re.sub(r"^(the|a|an) ", "", norm.title(s.unit.label))
            if k:
                by_title[k].append(uid)
        for k, ids in by_title.items():
            if len(ids) > 1:
                for uid in ids:
                    add(uid, Evidence("remakes", len(ids) - 1,
                                      f"recorded {len(ids)} times by the maestro", "canon"))

    # A signal named after one of the pack's sources means "this source lists the unit" (a blog
    # that writes about songs it chose): weak evidence, capped at PRESENCE_CAP.
    pack_sources = {x["id"] for x in pack.data.get("sources", [])}
    for sig in sorted(set(facet_of) & pack_sources):
        for uid, sc in scored.items():
            if sig in sc.unit.sources:
                add(uid, Evidence(sig, 1, f"listed by {sig}", sig, cap=PRESENCE_CAP, framing="listed"))

    matcher = MentionMatcher(scored, norm, _container_titles(store, groups))
    unmatched = []
    ignored: dict[str, int] = defaultdict(int)
    for m in _mentions(store, include_private):
        v = m["value"]
        if v.get("signal") not in facet_of:
            ignored[v.get("signal")] += 1
            continue
        uid = by_qid.get(v.get("qid")) if v.get("qid") else None
        if uid is None and v.get("kind") == "container":
            continue  # a film or album: evidence about it, not about one of its songs
        if uid is None:
            uid = matcher.match(v)
            for alt in v.get("alt_titles") or []:
                if uid is not None:
                    break
                uid = matcher.match({**v, "title": alt})
        if uid is None:
            unmatched.append({"signal": v["signal"], "title": v["title"], "year": v.get("year"),
                              "container": v.get("container"), "note": v.get("note"), "source": m["source_id"]})
            continue
        add(uid, Evidence(v["signal"], float(v.get("strength") or 1), v.get("note") or v["signal"],
                          m["source_id"], v.get("url") or m["page"], float(v.get("cap") or 1.0),
                          v.get("expert"), v.get("framing")))

    signals = sorted({sig for s in scored.values() for sig in s.signals})
    counts = _sum_counts(scored, facet_of)
    thresholds = count_thresholds(scored, facet_of, _popular_at(pack.data), counts)
    for s in scored.values():
        s.facet_of = facet_of
        s.low = low_popularity(s, thresholds)
        s.picked = s.unit.id in picks
    present = [f for f in FACETS if any(facet_of.get(sig) == f for sig in signals)]
    reach_facets = [f for f in present if f != "esteem"]
    _banded(scored, signals, facet_of, thresholds)
    for s in scored.values():
        for sig, x in s.signal_scores.items():
            f = facet_of[sig]
            s.facets[f] = max(s.facets.get(f, 0.0), x)
        s.reach = _band_combine(s, reach_facets, weights)
        s.popularity = _band_combine(s, present, weights)

    gems = _score_gems(scored, maestro, present, pack.container, set(pack.data["roles"]["in_scope"]),
                       facet_of, picks, not_gems, load_lists(pack), thresholds)

    return {
        "maestro": pack.slug,
        "signals": {sig: {"facet": facet_of[sig], "units": sum(1 for s in scored.values() if sig in s.signals)}
                    for sig in signals},
        "facets_present": present,
        "gems": {**gems, "found": sum(1 for s in scored.values() if s.gem),
                 "leads": sum(1 for s in scored.values() if s.lead),
                 "needs": None if any(s.gem for s in scored.values()) else
                 "expert gem lists (lists that claim their units are good and overlooked, with at least "
                 f"{LIST_PURITY_MIN:.0%} low-popularity units), or your own picks of low-popularity units"},
        "low_rule": {"count_thresholds": thresholds, "low": sum(1 for s in scored.values() if s.low),
                     "rule": "no chart, award or hit list, no cover or reuse, no article, and no count above its "
                             f"cut-off: the top {1 - COUNT_POPULAR_QUANTILE:.0%} of the units it names, or a fixed "
                             "count where the pack or POPULAR_AT sets one, whichever is lower; views and listeners "
                             f"add up over uploads and tracks (releases: never popular at {LOW_POP_REISSUES} or fewer)",
                     "band": LOW_BAND},
        "listed_without_data": sorted(set(facet_of) - set(signals)),
        "unmatched_mentions": unmatched,
        "ignored_mentions": dict(ignored),
        "units": scored,
    }


TWIN_TITLE = 0.9  # opening-line likeness for a popularity twin (see `queue_twins`)


def queue_twins(store: Store, res: dict[str, Any]) -> list[dict[str, Any]]:
    """Queue for review the units that only loose sources list (sources whose ids do not mean distinct
    songs, such as MusicBrainz) and that open like a unit from another lineage of the same or a near
    year. Such a twin splits a song's popularity evidence, and a gem tag on the wrong copy shows a
    known song as low. Pairs already decided distinct, and pairs a source keeps apart, are skipped.
    The items are ordinary `unit_near_title` reviews (`maestro-review`); returns those newly queued."""
    from maestro.resolve import _distinct_pairs, queue_review
    groups = Groups(store)
    loose_lineages = {x.get("lineage") or x["id"] for x in store.pack.data.get("sources", [])
                      if x.get("distinct_ids") is False}
    if not loose_lineages:
        return []
    scored: dict[int, Scored] = res["units"]
    norm = Normaliser.for_store(store)
    keys = {uid: {k for t in s.titles for k in _keys(norm, t)} for uid, s in scored.items()}
    by_opening: dict[str, list[int]] = defaultdict(list)
    for uid, ks in keys.items():
        for k in {skeleton(k)[:4] for k in ks}:
            if len(k) == 4:
                by_opening[k].append(uid)
    distinct = _distinct_pairs(store)
    out = []
    for uid, s in scored.items():
        if not s.unit.lineages or not s.unit.lineages <= loose_lineages:
            continue
        best: tuple[float, int] | None = None
        for a in keys[uid]:
            for o in by_opening.get(skeleton(a)[:4], ()):
                os_ = scored[o]
                if o == uid or os_.unit.lineages <= loose_lineages:
                    continue
                if s.unit.year and os_.unit.year and abs(s.unit.year - os_.unit.year) > 1:
                    continue
                if tuple(sorted((uid, o))) in distinct or not groups.can_join(uid, o):
                    continue
                sim = max(title_similarity(a, b, TWIN_TITLE) for b in keys[o])
                if sim >= TWIN_TITLE and (best is None or sim > best[0]):
                    best = (sim, o)
        if best is None:
            continue
        sim, o = best
        item = {"labels": [s.unit.label, scored[o].unit.label], "years": [s.unit.year, scored[o].unit.year],
                "similarity": round(sim, 3), "found_by": "maestro-score --queue-twins (popularity twin)"}
        if queue_review(store, "unit_near_title", [uid, o], item, priority=1):
            out.append({"units": [uid, o], **item})
    return out


def load_lists(pack) -> dict[str, dict[str, str]]:
    """The pack's expert lists and their intent (`popularity_signals.lists`, a TSV of url, intent, note).

    Intent is a person's reading of the list's title and introduction: `gem_list` claims its units
    are good and overlooked; `best_of` claims they are good (and need not be overlooked); `themed`
    gathers units by a theme (an actor, a year, a raga) and claims nothing about quality."""
    rel = (pack.data.get("popularity_signals") or {}).get("lists")
    out: dict[str, dict[str, str]] = {}
    if not rel:
        return out
    path = pack.path.parent / rel
    for line in path.read_text("utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        url, intent = cols[0].strip(), cols[1].strip()
        if intent not in LIST_INTENTS:
            raise ValueError(f"{path}: unknown list intent {intent!r} for {url}")
        out[url] = {"intent": intent, "note": cols[2].strip() if len(cols) > 2 else ""}
    return out


def low_popularity(s: Scored, medians: dict[str, float] | None = None) -> bool:
    """Positive and absent evidence agree that few people know the unit: no chart, award or hit list
    (contemporary), no cover or reuse (penetration), no article (an enduring list), and, for count
    signals (releases, Last.fm listeners, YouTube views; see `count_thresholds`), outside the top of
    the units they name."""
    medians = medians or {}
    for sig, strength in s.signals.items():
        f = s.facet_of.get(sig)
        if sig == "reissues" and sig not in medians:
            if strength > LOW_POP_REISSUES:
                return False
        elif sig in medians:
            if strength > medians[sig]:
                return False
        elif f in ("contemporary", "penetration", "enduring"):
            return False
    return True


def count_thresholds(scored: dict[int, Scored], facet_of: dict[str, str],
                     popular_at: dict[str, float] | None = None, counts: set[str] | None = None) -> dict[str, float]:
    """Enduring signals that are counts, not lists, with the strength above which a unit counts as
    popular. A signal is a count when it names more than COUNT_SIGNAL_SHARE of the canon or its
    strengths take at least COUNT_MIN_DISTINCT values: a few Last.fm listeners say nothing, being in
    the top (COUNT_POPULAR_QUANTILE) of the units it names does, or reaching `popular_at` (YouTube
    views, Last.fm listeners: see POPULAR_AT), whichever is lower. An enduring signal that is neither
    (an encyclopedia article) is a list: being on it is enough. Reissues (releases and catalogue
    numbers) are always a count: old film songs land on many compilations, so only the top of the
    canon is popular for it, and never a unit on LOW_POP_REISSUES releases or fewer."""
    out = {}
    popular_at = POPULAR_AT if popular_at is None else popular_at
    for sig in (_count_signals(scored, facet_of) if counts is None else counts):
        vals = sorted(s.signals[sig] for s in scored.values() if sig in s.signals)
        if not vals:
            continue
        top = vals[min(len(vals) - 1, int(len(vals) * COUNT_POPULAR_QUANTILE))]
        if sig == "reissues":
            out[sig] = max(float(LOW_POP_REISSUES), top)
        else:
            out[sig] = min(top, float(popular_at[sig])) if sig in popular_at else top
    return out


def _voice_of(e: Evidence) -> str:
    return e.expert or e.source


def _expert_quality(scored: dict[int, Scored], facet_of: dict[str, str], picks: set[int],
                    lists: dict[str, dict[str, str]],
                    thresholds: dict[str, float] | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, float]]:
    """Quality from expert gem lists: each unit's mentions in qualifying lists, voice by voice.

    Only `gem_list` lists count (see `load_lists`), and only when at least LIST_PURITY_MIN of the
    units they name are low-popularity: a list that slips in hits is listing favourites, not gems.
    Best-of and themed lists, and lists nobody has classified yet, are reported but never count.
    A voice is one expert (the list's author). Several lists from one voice count once. A voice that
    names a large share of the canon says little about any one unit, so its word is scaled by its
    selectivity; from CALIBRATION_MIN_PICKS user picks on, also by how often it names them.
    Independent voices agreeing multiply: quality = 1 - prod(1 - word) over the voices.
    """
    n = max(1, len(scored))
    medians = count_thresholds(scored, facet_of) if thresholds is None else thresholds
    per_list: dict[str, dict[str, Any]] = {}
    for uid, s in scored.items():
        for e in s.evidence:
            if facet_of.get(e.signal) != "esteem" or not e.url or not e.expert:
                continue  # source presence ("listed by" a blog) is not a list
            L = per_list.setdefault(e.url, {"voice": _voice_of(e), "title": e.note, "units": set(),
                                             "intent": lists.get(e.url, {}).get("intent", "unclassified")})
            L["units"].add(uid)
    report: dict[str, Any] = {}
    named: dict[str, set[int]] = defaultdict(set)
    for url, L in per_list.items():
        low = sum(1 for u in L["units"] if low_popularity(scored[u], medians))
        purity = low / len(L["units"])
        counts = L["intent"] == "gem_list" and purity >= LIST_PURITY_MIN
        report[url] = {"voice": L["voice"], "title": L["title"], "intent": L["intent"], "units": len(L["units"]),
                       "low_popularity": low, "purity": round(purity, 3), "counts": counts}
        if counts:
            named[L["voice"]] |= L["units"]
    voices: dict[str, dict[str, Any]] = {}
    for v, ids in named.items():
        share = len(ids) / n
        sel = max(0.05, min(1.0, math.log(1 / share) / math.log(1 / SELECTIVE_SHARE))) if share < 1 else 0.05
        weight, agree = 1.0, None
        if len(picks) >= CALIBRATION_MIN_PICKS:
            hit = len(ids & picks)
            agree = hit / len(picks)
            # Lift over the voice's base rate, smoothed, kept within [0.5, 2].
            weight = max(0.5, min(2.0, ((hit + 1) / (len(picks) * share + 1)) ** 0.5))
        voices[v] = {"units": len(ids), "selectivity": round(sel, 3), "weight": round(weight, 3),
                     **({"names_picks": round(agree, 3)} if agree is not None else {})}
    for uid, s in scored.items():
        miss = 1.0
        for v, ids in named.items():
            if uid in ids:
                miss *= 1 - min(VOICE_MAX, voices[v]["selectivity"] * voices[v]["weight"])
        s.quality = 1 - miss
    for uid in picks:
        if uid in scored:
            scored[uid].quality = 1.0
    return voices, report, medians


def _score_gems(scored: dict[int, Scored], maestro: int, present: list[str], container_kind: str,
                own_roles: set[str], facet_of: dict[str, str], picks: set[int], not_gems: set[int],
                lists: dict[str, dict[str, str]], thresholds: dict[str, float] | None = None) -> dict[str, Any]:
    """A gem is a unit experts single out that few people know.

    Quality comes only from expert opinion (`_expert_quality`) or the user's own pick; popularity is
    never evidence of quality. Reach (popularity without esteem) is a filter: a gem's reach must be
    under GEM_MAX_REACH. Gem score = quality, less a little for reach, so between equally praised
    units the less known comes first. A unit the user marked "not a gem" never is one.

    Leads are units worth a listen but with no expert word yet, each with its reason:
    * great team: the collaborators' other units are popular;
    * in a hit's shadow: the most popular other unit in the same film, album or score (not a
      recording session, whose tracks share only a date);
    * rediscovered: reissued or written about again and again, but never on the hit lists (only when
      the lists span the unit's year, cover REDISCOVERY_COVERAGE of those years' units and name
      REDISCOVERY_CAPTURE of the most enduring ones).
    """
    voices, list_report, medians = _expert_quality(scored, facet_of, picks, lists, thresholds)
    counting = {url for url, r in list_report.items() if r["counts"]}
    for uid, s in scored.items():
        # Low popularity is the first, hard filter: a rare gem is a good unit few people know.
        if uid in not_gems or not low_popularity(s, medians) or s.quality < GEM_MIN_QUALITY:
            continue
        s.gem = s.quality
        if s.picked:
            s.gem_reasons.append("your pick")
        s.gem_reasons += list(dict.fromkeys(e.note for e in s.evidence if e.url in counting))[:3]

    if not present:
        return {"reach_known": False, "contemporary_coverage": 0.0, "voices": voices, "lists": list_report}
    by_person: dict[int, list[int]] = defaultdict(list)
    for uid, s in scored.items():
        for role, ids in s.unit.credits.items():
            if role in own_roles:  # co-singers on a duet are company, not the team that made it
                continue
            for p in ids:
                if p != maestro and uid not in by_person[p][-1:]:
                    by_person[p].append(uid)
    totals = {p: sum(scored[u].popularity for u in us) for p, us in by_person.items()}
    by_container: dict[int, list[int]] = defaultdict(list)
    if container_kind in SHADOW_CONTAINERS:
        for uid, s in scored.items():
            if s.unit.container is not None:
                by_container[s.unit.container].append(uid)

    # Team: the two strongest collaborators (a composer and a lyricist, say), each by the average
    # popularity of their other units, shrunk towards 0 for small bodies of work.
    team_raw: dict[int, tuple[float, list[int]]] = {}
    for uid, s in scored.items():
        avgs = []
        for p in {p for role, ids in s.unit.credits.items() if role not in own_roles for p in ids}:
            if p == maestro or len(by_person[p]) - 1 < TEAM_MIN_UNITS:
                continue
            avgs.append(((totals[p] - s.popularity) / (len(by_person[p]) - 1 + TEAM_SHRINK), p))
        avgs.sort(reverse=True)
        top = avgs[:2]
        team_raw[uid] = (sum(a for a, _ in top) / 2, [p for a, p in top if a > 0])
    team_rank = _rank_scores({u: v[0] for u, v in team_raw.items()}, list(scored))
    # The hit lists speak only for the years they span (a countdown that began in 1953 says nothing
    # about a 1949 song), so coverage and "little reach" are judged among units from those years
    # (and units of unknown year, which may be from them).
    charted_years = [s.unit.year for s in scored.values() if s.facets.get("contemporary") and s.unit.year]
    era = (min(charted_years), max(charted_years)) if charted_years else None
    in_era = {u for u, s in scored.items() if not s.unit.year or (era and era[0] <= s.unit.year <= era[1])}
    contemporary_cover = (sum(1 for u in in_era if scored[u].facets.get("contemporary")) / len(in_era)
                          if in_era else 0.0)
    # "Little reach" means something only when the hit lists are deep enough: a canon whose charts
    # name a handful of #1s cannot tell a forgotten song from a hit nobody has listed yet.
    reach_known = "contemporary" in present and contemporary_cover >= REDISCOVERY_COVERAGE
    # "Never a hit" also needs hit lists long enough to hold most hits: of the units compilers and
    # writers keep coming back to, the lists must name at least REDISCOVERY_CAPTURE. A yearly top 30
    # shared by every singer of the day misses most of a prolific singer's lasting songs.
    lasting = [u for u in in_era if scored[u].facets.get("enduring", 0.0) >= REDISCOVERY_MIN]
    capture = sum(1 for u in lasting if scored[u].facets.get("contemporary")) / len(lasting) if lasting else 0.0
    rediscovery = reach_known and "enduring" in present and capture >= REDISCOVERY_CAPTURE

    for uid, s in scored.items():
        if s.gem or uid in not_gems:
            continue
        known = reach_known and uid in in_era
        options: list[tuple[float, str]] = []  # (lead score, reason)
        if rediscovery and known:
            cont, end = s.facets.get("contemporary", 0.0), s.facets.get("enduring", 0.0)
            if end >= REDISCOVERY_MIN and cont < GEM_MAX_REACH:
                notes = [e.note for e in s.evidence if e.signal in s.signal_scores
                         and s.facet_of.get(e.signal) == "enduring"]
                options.append((end * (1 - cont), "never a hit, but kept alive: " + "; ".join(notes[:2])))
        team = TEAM_WEIGHT * team_rank.get(uid, 0.0) if known else 0.0
        if team >= GEM_MIN_QUALITY and s.reach < GEM_MAX_REACH and team_raw[uid][1]:
            options.append((team * (1 - s.reach), "team:" + ",".join(map(str, team_raw[uid][1]))))
        sib, sib_id = 0.0, None
        for o in by_container.get(s.unit.container, ()) if s.unit.container is not None else ():
            if o != uid and scored[o].popularity > sib:
                sib, sib_id = scored[o].popularity, o
        if known and SIBLING_WEIGHT * sib >= GEM_MIN_QUALITY and s.reach < GEM_MAX_REACH and sib_id is not None:
            options.append((SIBLING_WEIGHT * sib * (1 - s.reach), f"sibling:{sib_id}"))
        if not options:
            continue
        options.sort(reverse=True)
        s.lead = min(1.0, options[0][0] + 0.05 * (len(options) - 1))
        s.lead_reasons = [why for _, why in options]
    return {"reach_known": reach_known, "contemporary_coverage": round(contemporary_cover, 4),
            "hit_list_years": list(era) if era else None, "rediscovery": rediscovery,
            "lasting_captured": round(capture, 4), "voices": voices, "lists": list_report}


def ordered_evidence(s: Scored) -> list[Evidence]:
    """Strongest first: by facet (charts and awards before reissues), then by how well it ranks.

    Two sources giving the same fact (a #1 read from Wikipedia and from a full countdown) show once."""
    order = {f: i for i, f in enumerate(FACETS)}
    out, seen = [], set()
    for e in sorted(s.evidence, key=lambda e: (order.get(s.facet_of.get(e.signal), 9),
                                               -round(s.signal_scores.get(e.signal, 0), 2), -e.strength)):
        if (e.signal, e.note) not in seen:
            seen.add((e.signal, e.note))
            out.append(e)
    return out


def unit_summary(s: Scored, thresholds: dict[str, float] | None = None) -> dict[str, Any] | None:
    """The JSON a snapshot carries for one unit (None when nothing is known). With the canon's count
    thresholds, a popular unit also says why (`popular_because`)."""
    if not s.evidence and not s.gem and not s.picked and not s.lead:
        return None
    out: dict[str, Any] = {
        "score": round(100 * s.popularity),
        "reach": round(100 * s.reach),  # popularity without expert esteem
        "low": s.low,  # low popularity (reach under 50): a unit the user tags as a gem is then a rare gem
        "facets": {f: round(100 * x) for f, x in s.facets.items() if x},
        "evidence": [{"signal": e.signal, "note": e.note, "source": e.source, **({"url": e.url} if e.url else {})}
                     for e in ordered_evidence(s)],
    }
    if thresholds is not None and not s.low:
        out["popular_because"] = popular_because(s, thresholds)
    if s.picked:
        out["picked"] = True
    if s.quality:
        out["quality"] = round(100 * s.quality)
    if s.gem:
        out["gem"] = {"score": round(100 * s.gem), "reasons": s.gem_reasons}
    if s.lead:
        out["lead"] = {"score": round(100 * s.lead), "reasons": s.lead_reasons}
    return out


def resolve_reasons(reasons: list[str], people: dict[int, str], titles: dict[int, str]) -> list[str]:
    out = []
    for r in reasons:
        if r.startswith("team:"):
            names = [people.get(int(x), "a collaborator") for x in r[5:].split(",") if x]
            out.append(f"made with {' and '.join(names)}, whose other work is popular")
        elif r.startswith("sibling:"):
            out.append(f"overshadowed by “{titles.get(int(r[8:]), '?')}” from the same "
                       "film, album or session")
        else:
            out.append(r)
    return out


def explorer_tags(path: str, slug: str) -> list[int]:
    """Unit ids of one maestro's gem tags in a file the canon explorer exported (or a gem-tag ledger)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != "maestro-gem-tags/1":
        raise SystemExit(f"{path}: not a gem tag export from the canon explorer")
    return [int(t["unit"]) for t in data.get("tags", [])
            if t.get("maestro") == slug and not t.get("untagged_seen_at")]  # a ledger keeps untagged rows


# ---- CLI --------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="maestro-score", description="Popularity and rare-gem scores for one maestro's canon.")
    p.add_argument("--pack", default=os.environ.get("MAESTRO_PACK"), required=os.environ.get("MAESTRO_PACK") is None)
    p.add_argument("--dsn", help="default $MAESTRO_DSN_<SLUG>, then $MAESTRO_DSN (.env is read)")
    p.add_argument("--top", type=int, default=20, help="how many popular units and gems to list")
    p.add_argument("--pick", type=int, nargs="+", metavar="UNIT", help="record the user's pick: a gem (also calibrates the experts)")
    p.add_argument("--not-gem", type=int, nargs="+", metavar="UNIT", help="record that the user says a unit is not a gem")
    p.add_argument("--picks-file", metavar="FILE",
                   help="record as picks the gem tags exported from the canon explorer (JSON) for this maestro")
    p.add_argument("--json", action="store_true", help="print every scored unit as JSON")
    p.add_argument("--queue-twins", action="store_true",
                   help="queue for review units only loose sources (MusicBrainz) list that open like another "
                        "unit of a near year: twins split a song's popularity (decide with maestro-review)")
    p.add_argument("--include-private", action="store_true")
    args = p.parse_args(argv)
    from maestro import config

    pack = load_pack(args.pack)
    dsn = config.dsn_for(pack.slug, args.dsn)
    if not dsn:
        p.error(f"--dsn (or {config.dsn_env_name(pack.slug)} or MAESTRO_DSN) is required")
    if args.picks_file:
        args.pick = (args.pick or []) + explorer_tags(args.picks_file, pack.slug)
    store = Store.connect(dsn, pack)
    try:
        if args.pick:
            known = {r["id"] for r in store.conn.execute(
                "SELECT id FROM entity WHERE id = ANY(%s)", (args.pick,)).fetchall()}
            if set(args.pick) - known:
                p.error(f"no such unit(s) in this store: {sorted(set(args.pick) - known)} "
                        "(tags hold the unit ids of the snapshot they were made on)")
            for uid in args.pick:
                store.upsert_claim(uid, "pick", value=True, source_id="user", kind="decided")
            store.conn.commit()
            print(f"recorded {len(args.pick)} pick(s)")
        if args.not_gem:
            for uid in args.not_gem:
                store.upsert_claim(uid, "pick", value=False, source_id="user", kind="decided")
            store.conn.commit()
            print(f"recorded {len(args.not_gem)} not-a-gem mark(s)")
        res = score_canon(store, include_private=args.include_private)
        if args.queue_twins:
            with store.exclusive():
                twins = queue_twins(store, res)
                store.conn.commit()
            print(f"queued {len(twins)} popularity twin(s) for review (maestro-review)")
            for t in twins[:args.top]:
                print(f"  {t['units']}: {t['labels'][0]} ({t['years'][0]}) ~ {t['labels'][1]} ({t['years'][1]})")
        labels = {r["id"]: r["label"] for r in store.conn.execute("SELECT id, label FROM entity").fetchall()}
    finally:
        store.close()
    units: dict[int, Scored] = res["units"]
    if args.json:
        print(json.dumps({str(u): unit_summary(s) for u, s in units.items() if unit_summary(s)}, ensure_ascii=False))
        return 0
    print(f"{res['maestro']}: signals {json.dumps(res['signals'])}")
    if res["gems"].get("needs"):
        print(f"no gems yet: they need {res['gems']['needs']}")
    if res["listed_without_data"]:
        print(f"listed in the pack, no data yet: {', '.join(res['listed_without_data'])}")
    ranked = sorted(units.values(), key=lambda s: -s.reach)
    print(f"\nMost popular ({args.top}):")
    for s in ranked[:args.top]:
        print(f"  {round(100 * s.reach):3d}  {s.unit.label} ({s.unit.year})  "
              f"[{'; '.join(e.note for e in ordered_evidence(s)[:3])}]")
    gems = sorted((s for s in units.values() if s.gem), key=lambda s: -s.gem)
    print(f"\nGems ({len(gems)} found, top {args.top}):")
    for s in gems[:args.top]:
        print(f"  {round(100 * s.gem):3d}  {s.unit.label} ({s.unit.year})  "
              f"{'; '.join(resolve_reasons(s.gem_reasons, labels, labels))}")
    voices = res["gems"].get("voices") or {}
    if voices:
        print("\nExpert voices (units named, selectivity, weight from your picks):")
        for v, x in sorted(voices.items(), key=lambda kv: -kv[1]["selectivity"]):
            print(f"  {v}: {x['units']} units, selectivity {x['selectivity']}, weight {x['weight']}"
                  + (f", names {x['names_picks']:.0%} of your picks" if "names_picks" in x else ""))
    leads = sorted((s for s in units.values() if s.lead), key=lambda s: -s.lead)
    print(f"\nLeads, worth a listen but no expert word yet ({len(leads)}, top {args.top}):")
    for s in leads[:args.top]:
        print(f"  {round(100 * s.lead):3d}  {s.unit.label} ({s.unit.year})  "
              f"{'; '.join(resolve_reasons(s.lead_reasons, labels, labels))}")
    if res["unmatched_mentions"]:
        print(f"\nMentions matching no unit ({len(res['unmatched_mentions'])}): possible gaps")
        for m in res["unmatched_mentions"][:args.top]:
            print(f"  {m['title']} ({m.get('year')}, {m.get('container') or '-'}): {m['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
