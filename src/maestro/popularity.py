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

Scores are ranks within this maestro's canon, from 0 to 1 (shown as 0-100): a signal's score for a
unit is the share of the canon's units it beats on that signal (units without the signal score 0).
A facet takes its best signal. Popularity is 0.7 x the best facet plus 0.3 x the weighted mean of
the facets this canon has any data for, so one strong kind of evidence counts and agreement adds.

A **gem** is a unit with quality evidence and little reach. Reach is popularity without the esteem
facet. Quality is the best of: acclaim (the esteem facet, or the user's own pick), the team (the
best average popularity among the unit's collaborators' other units) and a hit beside it (the most
popular other unit in the same film, album or session). Gem score = quality x (1 - reach), for units
whose reach is under 0.5 and quality at least 0.6. Every gem lists its reasons.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from maestro.completeness import Unit, load_units
from maestro.pack import load_pack
from maestro.resolve import Groups, Normaliser, catalogue_key, fold, title_similarity, whole_title_similarity
from maestro.store import Store

FACETS = ("contemporary", "enduring", "penetration", "esteem")
DEFAULT_WEIGHTS = {"contemporary": 0.35, "enduring": 0.3, "penetration": 0.2, "esteem": 0.15}
DERIVED = ("reissues", "remakes")
PRESENCE_CAP = 0.4

GEM_MAX_REACH = 0.5
GEM_MIN_QUALITY = 0.6
TEAM_MIN_UNITS = 4  # a collaborator needs this many other units in the canon to vouch for a team
TEAM_SHRINK = 10  # pseudo-units of popularity 0 added to a collaborator's average
TEAM_WEIGHT = 0.75
SIBLING_WEIGHT = 0.65
REDISCOVERY_COVERAGE = 0.05  # contemporary signals must reach this share of the canon
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
    gem: float = 0.0
    gem_reasons: list[str] = field(default_factory=list)
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
OPENING_MATCH = 0.82    # opening-line likeness needed inside a matched container
CONTAINER_MATCH = 0.85


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

    def _in_container(self, cid: int | None, ckey: str) -> bool:
        if cid is None:
            return False
        k = (cid, ckey)
        if k not in self._cmatch:
            self._cmatch[k] = any(whole_title_similarity(t, ckey) >= CONTAINER_MATCH for t in self.ckeys.get(cid, ()))
        return self._cmatch[k]

    def match(self, m: dict[str, Any]) -> int | None:
        mkeys = _keys(self.norm, m["title"])
        if not mkeys:
            return None
        year = m.get("year")
        ckey = self.norm.container(m["container"]) if m.get("container") else None
        cands = []
        for uid, s in self.scored.items():
            u = s.unit
            if ckey:
                if not self._in_container(u.container, ckey):
                    continue
                sim = max(((title_similarity(a, b) if min(len(a), len(b)) >= 10 else whole_title_similarity(a, b))
                           for a in mkeys for b in self.ukeys[uid]), default=0.0)
                if sim < OPENING_MATCH:
                    continue
            else:
                sim = 0.0
                for a in mkeys:
                    for b in self.ukeys[uid]:
                        if abs(len(a) - len(b)) > 0.3 * max(len(a), len(b)) + 2:
                            continue
                        sim = max(sim, whole_title_similarity(a, b))
                if sim < TITLE_MATCH:
                    continue
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
    by_qid: dict[str, int] = {}
    for r in _unit_facts(store, members, ("title", "on_release", "catalogue", "pick", "film", "xref")):
        uid = members[r["entity_id"]]
        v = r["value"]
        if r["field"] in ("film", "xref") and isinstance(v, str) and (r["field"] == "film" or r["source_id"] == "wikidata"):
            q = re.search(r"\b(Q\d+)\b", v)
            if q:
                by_qid.setdefault(q.group(1), uid)
        elif r["field"] == "title" and isinstance(v, str):
            scored[uid].titles.add(v)
        elif r["field"] == "on_release" and isinstance(v, str):
            rel[uid].add(v)
        elif r["field"] == "catalogue" and isinstance(v, str):
            k = catalogue_key(v)
            if k:
                cat[uid].add(k)
        elif r["field"] == "pick" and r["source_id"] == "user" and v is not False:
            picks.add(uid)

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
                add(uid, Evidence(sig, 1, f"listed by {sig}", sig, cap=PRESENCE_CAP))

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
                          m["source_id"], v.get("url") or m["page"], float(v.get("cap") or 1.0)))

    universe = list(scored)
    signals = sorted({sig for s in scored.values() for sig in s.signals})
    caps = {sig: min((e.cap for s in scored.values() for e in s.evidence if e.signal == sig), default=1.0)
            for sig in signals}
    for sig in signals:
        ranks = _rank_scores({u: s.signals.get(sig, 0.0) for u, s in scored.items()}, universe)
        for u, x in ranks.items():
            if x:
                scored[u].signal_scores[sig] = x * caps[sig]
    present = [f for f in FACETS if any(facet_of.get(sig) == f for sig in signals)]
    reach_facets = [f for f in present if f != "esteem"]
    for s in scored.values():
        for sig, x in s.signal_scores.items():
            f = facet_of[sig]
            s.facets[f] = max(s.facets.get(f, 0.0), x)
        s.popularity = _combine(s.facets, present, weights)
        s.reach = _combine(s.facets, reach_facets, weights)
        s.picked = s.unit.id in picks
        s.facet_of = facet_of

    gems = _score_gems(scored, maestro, present, pack.container, set(pack.data["roles"]["in_scope"]))

    return {
        "maestro": pack.slug,
        "signals": {sig: {"facet": facet_of[sig], "units": sum(1 for s in scored.values() if sig in s.signals)}
                    for sig in signals},
        "facets_present": present,
        "gems": {**gems, "found": sum(1 for s in scored.values() if s.gem),
                 "needs": None if gems["reach_known"] else
                 f"hit lists (charts, countdowns) that cover at least {REDISCOVERY_COVERAGE:.0%} of the canon; "
                 f"this canon's cover {gems['contemporary_coverage']:.1%}, so only your picks and acclaimed "
                 "units can be gems"},
        "listed_without_data": sorted(set(facet_of) - set(signals)),
        "unmatched_mentions": unmatched,
        "ignored_mentions": dict(ignored),
        "units": scored,
    }


def _score_gems(scored: dict[int, Scored], maestro: int, present: list[str], container_kind: str,
                own_roles: set[str]) -> dict[str, Any]:
    """Four kinds of gem, each a quality and the reach it is measured against:

    * acclaimed: the esteem facet or the user's pick, against reach (popularity without esteem);
    * rediscovered: the enduring facet (reissues, articles) against the contemporary one, i.e.
      never a hit at the time but compilers and writers keep coming back to it. Only when the
      contemporary signals cover at least REDISCOVERY_COVERAGE of the canon, so that missing from
      them means something (a list of a few #1s says nothing about the rest);
    * great team: the collaborators' other units are popular, against reach;
    * in a hit's shadow: the most popular other unit in the same film, album or score (not a
      recording session, whose tracks share only a date), against reach.
    """
    if not present:
        return {"reach_known": False, "contemporary_coverage": 0.0}
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
    contemporary_cover = sum(1 for s in scored.values() if s.facets.get("contemporary")) / max(1, len(scored))
    # "Little reach" means something only when the hit lists are deep enough: a canon whose charts
    # name a handful of #1s cannot tell a forgotten song from a hit nobody has listed yet.
    reach_known = "contemporary" in present and contemporary_cover >= REDISCOVERY_COVERAGE
    rediscovery = reach_known and "enduring" in present

    for uid, s in scored.items():
        options: list[tuple[float, str]] = []  # (gem score, reason)
        acclaim = 1.0 if s.picked else s.facets.get("esteem", 0.0)
        if acclaim >= GEM_MIN_QUALITY and s.reach < GEM_MAX_REACH:
            notes = [e.note for e in s.evidence if facet_of_evidence(s, e) == "esteem"]
            why = "your pick" if s.picked else "acclaimed: " + "; ".join(notes[:2])
            options.append((acclaim * (1 - s.reach), why))
        if rediscovery:
            cont, end = s.facets.get("contemporary", 0.0), s.facets.get("enduring", 0.0)
            if end >= REDISCOVERY_MIN and cont < GEM_MAX_REACH:
                notes = [e.note for e in s.evidence if e.signal in s.signal_scores
                         and facet_of_evidence(s, e) == "enduring"]
                options.append((end * (1 - cont), "never a hit, but kept alive: " + "; ".join(notes[:2])))
        team = TEAM_WEIGHT * team_rank.get(uid, 0.0) if reach_known else 0.0
        if team >= GEM_MIN_QUALITY and s.reach < GEM_MAX_REACH and team_raw[uid][1]:
            options.append((team * (1 - s.reach), "team:" + ",".join(map(str, team_raw[uid][1]))))
        sib, sib_id = 0.0, None
        for o in by_container.get(s.unit.container, ()) if s.unit.container is not None else ():
            if o != uid and scored[o].popularity > sib:
                sib, sib_id = scored[o].popularity, o
        if reach_known and SIBLING_WEIGHT * sib >= GEM_MIN_QUALITY and s.reach < GEM_MAX_REACH and sib_id is not None:
            options.append((SIBLING_WEIGHT * sib * (1 - s.reach), f"sibling:{sib_id}"))
        if not options:
            continue
        options.sort(reverse=True)
        # More than one kind of evidence, and any esteem at all (a curator wrote about it), lift a gem.
        s.gem = min(1.0, options[0][0] + 0.05 * (len(options) - 1) + 0.1 * s.facets.get("esteem", 0.0))
        s.gem_reasons = [why for _, why in options]
        if not s.evidence:
            s.gem_reasons.append("no chart, award, article or reissue found")
    return {"reach_known": reach_known, "contemporary_coverage": round(contemporary_cover, 4)}


def facet_of_evidence(s: Scored, e: Evidence) -> str | None:
    return s.facet_of.get(e.signal)


def ordered_evidence(s: Scored) -> list[Evidence]:
    """Strongest first: by facet (charts and awards before reissues), then by how well it ranks."""
    order = {f: i for i, f in enumerate(FACETS)}
    return sorted(s.evidence, key=lambda e: (order.get(s.facet_of.get(e.signal), 9),
                                             -round(s.signal_scores.get(e.signal, 0), 2), -e.strength))


def unit_summary(s: Scored) -> dict[str, Any] | None:
    """The JSON a snapshot carries for one unit (None when nothing is known)."""
    if not s.evidence and not s.gem and not s.picked:
        return None
    out: dict[str, Any] = {
        "score": round(100 * s.popularity),
        "facets": {f: round(100 * x) for f, x in s.facets.items() if x},
        "evidence": [{"signal": e.signal, "note": e.note, "source": e.source, **({"url": e.url} if e.url else {})}
                     for e in ordered_evidence(s)],
    }
    if s.picked:
        out["picked"] = True
    if s.gem:
        out["gem"] = {"score": round(100 * s.gem), "reasons": s.gem_reasons}
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


# ---- CLI --------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="maestro-score", description="Popularity and rare-gem scores for one maestro's canon.")
    p.add_argument("--pack", default=os.environ.get("MAESTRO_PACK"), required=os.environ.get("MAESTRO_PACK") is None)
    p.add_argument("--dsn", default=os.environ.get("MAESTRO_DSN"), required=os.environ.get("MAESTRO_DSN") is None)
    p.add_argument("--top", type=int, default=20, help="how many popular units and gems to list")
    p.add_argument("--pick", type=int, nargs="+", metavar="UNIT", help="record the user's pick (quality evidence for gems)")
    p.add_argument("--json", action="store_true", help="print every scored unit as JSON")
    p.add_argument("--include-private", action="store_true")
    args = p.parse_args(argv)
    store = Store.connect(args.dsn, load_pack(args.pack))
    try:
        if args.pick:
            for uid in args.pick:
                store.upsert_claim(uid, "pick", value=True, source_id="user", kind="decided")
            store.conn.commit()
            print(f"recorded {len(args.pick)} pick(s)")
        res = score_canon(store, include_private=args.include_private)
        labels = {r["id"]: r["label"] for r in store.conn.execute("SELECT id, label FROM entity").fetchall()}
    finally:
        store.close()
    units: dict[int, Scored] = res["units"]
    if args.json:
        print(json.dumps({str(u): unit_summary(s) for u, s in units.items() if unit_summary(s)}, ensure_ascii=False))
        return 0
    print(f"{res['maestro']}: signals {json.dumps(res['signals'])}")
    if res["gems"].get("needs"):
        print(f"gems are limited: they need {res['gems']['needs']}")
    if res["listed_without_data"]:
        print(f"listed in the pack, no data yet: {', '.join(res['listed_without_data'])}")
    ranked = sorted(units.values(), key=lambda s: -s.popularity)
    print(f"\nMost popular ({args.top}):")
    for s in ranked[:args.top]:
        print(f"  {round(100 * s.popularity):3d}  {s.unit.label} ({s.unit.year})  "
              f"[{'; '.join(e.note for e in ordered_evidence(s)[:3])}]")
    gems = sorted((s for s in units.values() if s.gem), key=lambda s: -s.gem)
    print(f"\nGems ({len(gems)} found, top {args.top}):")
    for s in gems[:args.top]:
        print(f"  {round(100 * s.gem):3d}  {s.unit.label} ({s.unit.year})  "
              f"{'; '.join(resolve_reasons(s.gem_reasons, labels, labels))}")
    if res["unmatched_mentions"]:
        print(f"\nMentions matching no unit ({len(res['unmatched_mentions'])}): possible gaps")
        for m in res["unmatched_mentions"][:args.top]:
            print(f"  {m['title']} ({m.get('year')}, {m.get('container') or '-'}): {m['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
