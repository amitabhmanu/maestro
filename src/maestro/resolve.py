"""Entity resolution: decide which records are the same person, container or unit.

Stages (design doc, component 5), cheapest first:

1. Normalise names and titles: Unicode folding, punctuation, and the pack's lexicons
   (`lexicon.transliteration`, `lexicon.people_aliases`, `lexicon.container_aliases`: TSV files of
   `variant<TAB>canonical`, relative to the pack folder).
2. Block: only records that share a normalised key (and, for units, a resolved container) are compared.
3. Decide: records from *different* sources with the same key are merged automatically (a reversible
   `same_as` claim). Records that one source keeps apart (two ids in the same source) are never merged
   automatically: the pair goes to the review queue. So do near matches (a name contained in another,
   a title within a small edit distance).

Every decision is a claim; nothing is deleted. A reviewer's "distinct" decision is a `distinct_from`
claim that stops the pair being queued again.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from maestro.store import Store

NEAR_TITLE_RATIO = 0.88  # container titles at or above this go to review
REVIEW_TITLE_RATIO = 0.8  # unit titles in one container at or above this are compared
AUTO_TITLE_RATIO = 0.93  # a mutual best match at or above this is merged
CLEAR_TITLE_RATIO = 0.85  # ... and so is one at or above this that leads every other candidate by CLEAR_MARGIN
CLEAR_MARGIN = 0.2


# ---- normalisation -------------------------------------------------------------

def fold(text: str) -> str:
    """Lower-case, strip accents and punctuation, collapse spaces. Keeps letters of any script."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("‘", "'").replace("’", "'").replace("`", "'")
    text = re.sub(r"['\"]", "", text.lower())
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def load_lexicon(pack_dir: Path, rel: str | None) -> dict[str, str]:
    """A `variant<TAB>canonical` table, keyed by the folded variant. Missing files give an empty table."""
    if not rel:
        return {}
    path = Path(pack_dir) / rel
    if not path.is_file():
        return {}
    table = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if len(row) >= 2 and row[0].strip() and not row[0].startswith("#"):
                table[fold(row[0])] = fold(row[1])
    return table


@dataclass
class Normaliser:
    translit: dict[str, str] = field(default_factory=dict)
    people: dict[str, str] = field(default_factory=dict)
    containers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def for_store(cls, store: Store) -> "Normaliser":
        lex = store.pack.data.get("lexicon", {})
        pack_dir = store.pack.path.parent
        return cls(
            translit=load_lexicon(pack_dir, lex.get("transliteration")),
            people=load_lexicon(pack_dir, lex.get("people_aliases")),
            containers=load_lexicon(pack_dir, lex.get("container_aliases")),
        )

    def _words(self, text: str) -> str:
        return " ".join(self.translit.get(w, w) for w in fold(text).split())

    def person(self, name: str) -> str:
        key = fold(name)
        return self.people.get(key, self._words(key))

    def title(self, title: str) -> str:
        # Drop a trailing language or version tag such as "(Telugu)" or "[2]".
        title = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*$", "", title or "")
        # Bare numbers are repeat marks ("Arati-2") or part numbers, not words of the title.
        return " ".join(w for w in self._words(title).split() if not w.isdigit())

    def container(self, title: str) -> str:
        key = self.title(title)
        return self.containers.get(key, key)


# ---- groups and review queue ---------------------------------------------------

class Groups:
    """Union-find over active same_as claims, so merged records count once."""

    def __init__(self, store: Store):
        self.parent: dict[int, int] = {}
        self.src: dict[int, set[str]] = {}
        for r in store.conn.execute(
            "SELECT entity_id, ref_entity_id FROM claim WHERE field = 'same_as' AND status = 'active'"
        ).fetchall():
            self.union(r["ref_entity_id"], r["entity_id"])

    def find(self, x: int) -> int:
        root = x
        while self.parent.get(root, root) != root:
            root = self.parent[root]
        while self.parent.get(x, x) != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, keep: int, other: int) -> None:
        a, b = self.find(keep), self.find(other)
        if a != b:
            self.parent[b] = a
            if b in self.src:
                self.src.setdefault(a, set()).update(self.src.pop(b))

    def note_sources(self, records: list[dict]) -> None:
        """Remember which sources each record comes from, so a group knows all its members' sources."""
        for r in records:
            self.src.setdefault(self.find(r["id"]), set()).update(r["sources"])

    def sources(self, x: int) -> set[str]:
        return self.src.get(self.find(x), set())

    def can_join(self, a: int, b: int) -> bool:
        """Two groups may be merged automatically only if no source lists records in both:
        a source that keeps two records apart knows they are different."""
        return self.find(a) != self.find(b) and self.sources(a).isdisjoint(self.sources(b))

    def members(self, ids: list[int]) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for i in ids:
            out[self.find(i)].append(i)
        return out


def _distinct_pairs(store: Store) -> set[tuple[int, int]]:
    rows = store.conn.execute(
        "SELECT entity_id, ref_entity_id FROM claim WHERE field = 'distinct_from' AND status = 'active'"
    ).fetchall()
    return {tuple(sorted((r["entity_id"], r["ref_entity_id"]))) for r in rows}


def queue_review(store: Store, kind: str, subject_ids: list[int], evidence: dict[str, Any], priority: int = 0) -> bool:
    """Add a review item unless the same one exists. Returns True when a new item was queued."""
    row = store.conn.execute(
        """INSERT INTO review_item (kind, subject_ids, evidence, priority)
           VALUES (%s, %s, %s, %s) ON CONFLICT (kind, subject_ids) DO NOTHING RETURNING id""",
        (kind, sorted(subject_ids), Jsonb(evidence), priority),
    ).fetchone()
    return row is not None


def open_reviews(store: Store, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
    return store.conn.execute(
        """SELECT id, kind, subject_ids, evidence, priority FROM review_item
           WHERE status = 'open' AND (%(kind)s::text IS NULL OR kind = %(kind)s)
           ORDER BY priority DESC, id LIMIT %(limit)s""",
        {"kind": kind, "limit": limit},
    ).fetchall()


def decide_review(store: Store, item_id: int, decision: str, note: str | None = None, keep_id: int | None = None) -> int | None:
    """Record a reviewer's decision. `merge` merges the subjects into `keep_id` (default: the first);
    `distinct` marks them as different; `dismiss` closes the item with no claim. Returns the claim id."""
    item = store.conn.execute("SELECT * FROM review_item WHERE id = %s", (item_id,)).fetchone()
    if item is None:
        raise ValueError(f"no review item {item_id}")
    if item["status"] != "open":
        raise ValueError(f"review item {item_id} is already {item['status']}")
    ids = list(item["subject_ids"])
    claim_id = None
    reason = f"review {item_id}: {note or decision}"
    if decision == "merge":
        keep = keep_id if keep_id is not None else ids[0]
        if keep not in ids:
            raise ValueError(f"keep_id {keep} is not one of {ids}")
        for other in ids:
            if other != keep:
                claim_id = store.merge_entities(keep, other, reason, source_id="user")
        status = "decided"
    elif decision == "distinct":
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                claim_id = store.upsert_claim(a, "distinct_from", ref_entity_id=b, value={"reason": reason},
                                              source_id="user", kind="decided")
        status = "decided"
    elif decision == "dismiss":
        status = "dismissed"
    else:
        raise ValueError("decision must be merge, distinct or dismiss")
    store.conn.execute(
        """UPDATE review_item SET status = %s, decision = %s, note = %s, decided_claim_id = %s, decided_at = now()
           WHERE id = %s""",
        (status, decision, note, claim_id, item_id),
    )
    return claim_id


# ---- resolution passes --------------------------------------------------------

@dataclass
class ResolveStats:
    merged: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    queued: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def as_dict(self) -> dict[str, dict[str, int]]:
        return {"merged": dict(self.merged), "queued": dict(self.queued)}


def _records(store: Store, type_: str) -> list[dict[str, Any]]:
    """Entities of a type with the source(s) that identify them (from xref claims)."""
    return store.conn.execute(
        """SELECT e.id, e.label,
                  coalesce(array_agg(DISTINCT x.source_id) FILTER (WHERE x.source_id IS NOT NULL), '{}') AS sources
           FROM entity e
           LEFT JOIN claim x ON x.entity_id = e.id AND x.field = 'xref' AND x.status = 'active'
           WHERE e.type = %s GROUP BY e.id, e.label ORDER BY e.id""",
        (type_,),
    ).fetchall()


def _merge_or_queue(store, groups, distinct, stats, kind, recs, evidence, priority=1, roles=None):
    """Within one block: merge records that come from different sources; queue same-source pairs.

    With `roles` (people only), a same-source pair credited in a shared role is merged too: one
    database listing the same full name twice in the same role is a duplicate entry far more often
    than two namesakes (decided by the user for Rafi's HFGK data, 2026-11-12). Merges are reversible.
    """
    roots = {}
    for r in recs:
        roots.setdefault(groups.find(r["id"]), r)
    recs = list(roots.values())
    if len(recs) < 2:
        return
    keep = recs[0]
    for other in recs[1:]:
        pair = tuple(sorted((keep["id"], other["id"])))
        if pair in distinct:
            continue
        shared_role = roles is not None and set(roles.get(keep["id"], {})) & set(roles.get(other["id"], {}))
        if set(keep["sources"]) & set(other["sources"]) and shared_role:
            store.merge_entities(keep["id"], other["id"],
                                 f"same name {evidence.get('key')!r} and role {sorted(shared_role)} in one source")
            groups.union(keep["id"], other["id"])
            stats.merged[kind] += 1
            continue
        if set(keep["sources"]) & set(other["sources"]):
            if queue_review(store, f"same_{kind}_same_source", list(pair),
                            {**evidence, "labels": [keep["label"], other["label"]]}, priority):
                stats.queued[kind] += 1
            continue
        if groups.src and not groups.can_join(keep["id"], other["id"]):
            if queue_review(store, f"same_{kind}_conflict", list(pair),
                            {**evidence, "labels": [keep["label"], other["label"]]}, priority):
                stats.queued[kind] += 1
            continue
        store.merge_entities(keep["id"], other["id"],
                             f"same normalised {kind} key {evidence.get('key')!r} from different sources")
        groups.union(keep["id"], other["id"])
        stats.merged[kind] += 1


def _role_counts(store: Store) -> dict[int, dict[str, int]]:
    """For each person: how many units credit them, per role."""
    out: dict[int, dict[str, int]] = defaultdict(dict)
    for r in store.conn.execute(
        """SELECT c.ref_entity_id AS pid, c.value ->> 'role' AS role, count(DISTINCT c.entity_id) AS n
           FROM claim c JOIN entity e ON e.id = c.entity_id AND e.type = %s
           WHERE c.field = 'credit' AND c.status = 'active' GROUP BY 1, 2""",
        (store.pack.unit,),
    ).fetchall():
        out[r["pid"]][r["role"]] = r["n"]
    return out


_JOINT = re.compile(r"\s*(\+|&|/|\band\b)\s*|[^\s]-[^\s]")


def _is_joint_credit(label: str) -> bool:
    """A credit naming two or more people ("Hasrat Jaipuri + Neeraj", "Sardar Malik-Premnath")."""
    return bool(_JOINT.search(label or ""))


def resolve_people(store: Store, norm: Normaliser, stats: ResolveStats) -> None:
    groups, distinct = Groups(store), _distinct_pairs(store)
    maestro = store.maestro_entity()
    recs = [r for r in _records(store, "person") if r["id"] != maestro]
    blocks: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        blocks[norm.person(r["label"])].append(r)
    roles = _role_counts(store)
    for key, block in blocks.items():
        if key:
            _merge_or_queue(store, groups, distinct, stats, "person", block, {"key": key}, roles=roles)

    # Near matches: a one- or two-word name contained in a longer one ("naushad" / "naushad ali"),
    # between people credited in the same role. These always go to review.
    by_first: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for key, block in blocks.items():
        if key:
            by_first[key.split()[0]].append((key, block[0]))
    for first, entries in by_first.items():
        if len(first) < 3 or len(entries) < 2:
            continue
        for i, (ka, ra) in enumerate(entries):
            for kb, rb in entries[i + 1:]:
                short, long_ = sorted((ka.split(), kb.split()), key=len)
                if len(short) > 2 or long_[: len(short)] != short:
                    continue
                if _is_joint_credit(ra["label"]) or _is_joint_credit(rb["label"]):
                    continue  # "A + B" or "A-B" names two people, not a variant of A
                a, b = groups.find(ra["id"]), groups.find(rb["id"])
                if a == b or tuple(sorted((a, b))) in distinct:
                    continue
                shared = set(roles.get(ra["id"], {})) & set(roles.get(rb["id"], {}))
                if not shared:
                    continue
                units = sum(roles[ra["id"]].get(x, 0) + roles[rb["id"]].get(x, 0) for x in shared)
                if queue_review(store, "person_near_name", [a, b], {
                    "labels": [ra["label"], rb["label"]], "shared_roles": sorted(shared),
                    "units": {ra["label"]: roles[ra["id"]], rb["label"]: roles[rb["id"]]},
                }, priority=min(units, 1000)):
                    stats.queued["person"] += 1


def _containers_of_units(store: Store) -> dict[int, int]:
    return {r["entity_id"]: r["ref_entity_id"] for r in store.conn.execute(
        """SELECT DISTINCT ON (entity_id) entity_id, ref_entity_id FROM claim
           WHERE field = 'part_of' AND status = 'active' ORDER BY entity_id, id""").fetchall()}


def _years(store: Store) -> dict[int, int]:
    return {r["entity_id"]: int(r["y"]) for r in store.conn.execute(
        """SELECT DISTINCT ON (entity_id) entity_id, value #>> '{}' AS y FROM claim
           WHERE field = 'year' AND status = 'active' AND jsonb_typeof(value) = 'number'
           ORDER BY entity_id, id""").fetchall()}


def resolve_containers(store: Store, norm: Normaliser, stats: ResolveStats) -> None:
    """Containers (films, albums, sessions) from different sources.

    1. Same title and year: merge across sources, review within one.
    2. Across sources, a title whose spellings fold to the same key (`_squash`), with years at most
       one apart (a soundtrack can be dated by its release, a film by its certificate), or with no
       year on one side: merged when it is the only candidate.
    3. Across sources, a similar title (ratio >= NEAR_TITLE_RATIO) with years at most one apart and
       a single candidate: review.
    """
    groups, distinct = Groups(store), _distinct_pairs(store)
    years = _years(store)
    recs = _records(store, "container")
    groups.note_sources(recs)
    blocks: dict[tuple, list[dict]] = defaultdict(list)
    for r in recs:
        r["key"] = norm.container(r["label"])
        r["squashed"] = _squash(r["key"].replace(" ", ""))
        r["year"] = years.get(r["id"])
        blocks[(r["key"], r["year"])].append(r)
    for (key, year), block in blocks.items():
        if key:
            _merge_or_queue(store, groups, distinct, stats, "container", block, {"key": key, "year": year})

    by_squash: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        if r["squashed"]:
            by_squash[r["squashed"]].append(r)

    def close_years(a, b):
        return a["year"] is None or b["year"] is None or abs(a["year"] - b["year"]) <= 1

    def candidates(r, pool):
        return [o for o in pool if o is not r and close_years(r, o) and groups.can_join(o["id"], r["id"])]

    for r in recs:
        near = candidates(r, by_squash.get(r["squashed"], []))
        # Two films a year apart with one title are remakes; a third record cannot tell which one it is.
        if len(near) == 1 and len(candidates(near[0], by_squash[r["squashed"]])) == 1:
            a, b = sorted((groups.find(r["id"]), groups.find(near[0]["id"])))
            if (a, b) not in distinct and a != b:
                store.merge_entities(a, b, f"container titles {r['label']!r} / {near[0]['label']!r}, "
                                           "same spelling key, close years, different sources")
                groups.union(a, b)
                stats.merged["container"] += 1

    by_year: dict[int, list[dict]] = defaultdict(list)
    for r in recs:
        if r["year"] is not None and r["squashed"]:
            by_year[r["year"]].append(r)
    for r in recs:
        if r["year"] is None or not r["squashed"]:
            continue
        pool = [o for y in (r["year"] - 1, r["year"], r["year"] + 1) for o in by_year.get(y, [])]
        similar = [o for o in candidates(r, pool) if o["id"] > r["id"]
                   and SequenceMatcher(None, r["squashed"], o["squashed"]).ratio() >= NEAR_TITLE_RATIO]
        if len(similar) == 1:
            a, b = sorted((groups.find(r["id"]), groups.find(similar[0]["id"])))
            if a != b and (a, b) not in distinct and queue_review(
                    store, "container_near_title", [a, b],
                    {"labels": [r["label"], similar[0]["label"]], "years": [r["year"], similar[0]["year"]]}, priority=2):
                stats.queued["container"] += 1


_SQUASH = [("oo", "u"), ("ee", "i"), ("q", "k"), ("w", "v"), ("ph", "f"), ("aye", "ae"), ("ay", "ai"),
           ("ai", "e"), ("au", "o")]


def _squash(text: str) -> str:
    """A rough Latin-script key for Indic transliterations: doubled letters and common vowel spellings folded."""
    for a, b in _SQUASH:
        text = text.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", text)


def _prefix_similarity(sa: str, la: str) -> float:
    # The longer title's opening may be a few letters shorter or longer than the whole shorter one.
    return max(SequenceMatcher(None, sa, la[:n]).ratio() for n in range(max(1, len(sa) - 3), len(sa) + 4))


def title_similarity(a: str, b: str) -> float:
    """How alike two normalised titles are, comparing the shorter with the same length of the longer.

    One source may give the first line and another the whole opening, often after a spoken or sung
    lead-in ("O Sss ...", "Aae Hain Badee Door Se ... Mahafil Men Shama Chamakee"), so the shorter
    title is also tried against the longer one from each later word. Letters are compared, not words,
    because transliterations split words and spell vowels differently ("rang lo" / "ranglo",
    "jaadoogar" / "jadugar").
    """
    wa, wb = a.split(), b.split()
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    if len(short) < 2:
        return 1.0 if short == long_ else 0.0
    sa = _squash("".join(short))
    best = 0.0
    for i in range(0, len(long_) - len(short) + 1):
        best = max(best, _prefix_similarity(sa, _squash("".join(long_[i:]))) - (0.02 if i else 0.0))
        if best >= 0.999:
            break
    return best


def resolve_units(store: Store, norm: Normaliser, stats: ResolveStats) -> None:
    groups, distinct = Groups(store), _distinct_pairs(store)
    container_of = _containers_of_units(store)
    blocks: dict[tuple, list[dict]] = defaultdict(list)
    unit_recs = _records(store, store.pack.unit)
    groups.note_sources(unit_recs)
    for r in unit_recs:
        c = container_of.get(r["id"])
        blocks[(groups.find(c) if c else None, norm.title(r["label"]))].append(r)
    for (container, key), block in blocks.items():
        if key and container is not None:
            _merge_or_queue(store, groups, distinct, stats, "unit", block, {"key": key, "container": container})

    # Other titles inside one container, from different sources: a clear, mutual best match is
    # merged; a weaker one goes to review.
    by_container: dict[int, list[tuple[str, dict]]] = defaultdict(list)
    for (container, key), block in blocks.items():
        if key and container is not None:
            by_container[container].append((key, block[0]))
    for container, entries in by_container.items():
        scores: dict[tuple[int, int], float] = {}
        for i, (ka, ra) in enumerate(entries):
            for kb, rb in entries[i + 1:]:
                if not set(ra["sources"]).isdisjoint(rb["sources"]):
                    continue  # one source listing two titles in a container means two units
                sim = title_similarity(ka, kb)
                if sim < REVIEW_TITLE_RATIO:
                    continue
                scores[(i, entries.index((kb, rb)))] = sim
        ranked: dict[int, list[tuple[float, int]]] = defaultdict(list)  # per record: its candidates, best first
        for (i, j), sim in scores.items():
            ranked[i].append((sim, j))
            ranked[j].append((sim, i))
        for lst in ranked.values():
            lst.sort(reverse=True)

        def margin(x):  # how far the best candidate leads the next one (1.0 when there is no other)
            lst = ranked[x]
            return lst[0][0] - lst[1][0] if len(lst) > 1 else 1.0
        for (i, j), sim in sorted(scores.items(), key=lambda kv: -kv[1]):
            ra, rb = entries[i][1], entries[j][1]
            a, b = groups.find(ra["id"]), groups.find(rb["id"])
            if a == b or tuple(sorted((a, b))) in distinct:
                continue
            if not groups.can_join(a, b):
                continue  # one side's group already holds a record from the other's source
            mutual = ranked[i][0][1] == j and ranked[j][0][1] == i
            clear = sim >= AUTO_TITLE_RATIO or (sim >= CLEAR_TITLE_RATIO and min(margin(i), margin(j)) >= CLEAR_MARGIN)
            if mutual and clear:
                keep, other = sorted((a, b))
                store.merge_entities(keep, other, f"title {entries[i][0]!r} ~ {entries[j][0]!r} "
                                                  f"({sim:.2f}) in container {container}, different sources")
                groups.union(keep, other)
                stats.merged["unit"] += 1
            elif queue_review(store, "unit_near_title", [a, b],
                              {"labels": [ra["label"], rb["label"]], "container": container,
                               "similarity": round(sim, 3)}, priority=1):
                stats.queued["unit"] += 1


def resolve(store: Store) -> ResolveStats:
    """Run every resolution pass once. Safe to re-run: existing merges and reviews are kept."""
    norm = Normaliser.for_store(store)
    stats = ResolveStats()
    resolve_people(store, norm, stats)
    resolve_containers(store, norm, stats)
    resolve_units(store, norm, stats)
    close_settled_reviews(store)
    return stats


def close_settled_reviews(store: Store) -> int:
    """Close open review items whose subjects have since been merged into one entity."""
    groups = Groups(store)
    closed = 0
    for item in open_reviews(store, limit=100000):
        if len({groups.find(i) for i in item["subject_ids"]}) == 1:
            store.conn.execute(
                """UPDATE review_item SET status = 'decided', decision = 'merge', note = 'merged by a resolution rule',
                   decided_at = now() WHERE id = %s""", (item["id"],))
            closed += 1
    return closed
