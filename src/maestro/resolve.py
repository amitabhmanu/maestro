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
import json
import re
import unicodedata
from collections import Counter, defaultdict
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
TIE_MARGIN = 0.03  # two candidates this close (a song and its reprise) always go to review


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


_PART_WORDS = {"pt", "part"}


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
        return " ".join(w for w in self._words(title).split() if not w.isdigit() and w not in _PART_WORDS)

    def container(self, title: str) -> str:
        key = self.title(title)
        return self.containers.get(key, key)


# ---- groups and review queue ---------------------------------------------------

class Groups:
    """Union-find over active same_as claims, so merged records count once."""

    def __init__(self, store: Store):
        self.parent: dict[int, int] = {}
        self.src: dict[int, set[str]] = {}
        # Sources whose ids do not mean distinct things (MusicBrainz keeps a recording per release
        # version): two of their records may still be one unit.
        self.loose = {s["id"] for s in store.pack.data.get("sources", []) if s.get("distinct_ids") is False}
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
        return self.find(a) != self.find(b) and not ((self.sources(a) & self.sources(b)) - self.loose)

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
        shared_strict = (set(keep["sources"]) & set(other["sources"])) - groups.loose
        if shared_strict and shared_role:
            store.merge_entities(keep["id"], other["id"],
                                 f"same name {evidence.get('key')!r} and role {sorted(shared_role)} in one source")
            groups.union(keep["id"], other["id"])
            stats.merged[kind] += 1
            continue
        if shared_strict:
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
    # Containers dated to different days (two recording sessions at one studio) are different.
    dates = {r["entity_id"]: r["d"] for r in store.conn.execute(
        """SELECT DISTINCT ON (entity_id) entity_id, value #>> '{}' AS d FROM claim
           WHERE field = 'date' AND status = 'active' ORDER BY entity_id, id""").fetchall()}
    for (key, year), block in blocks.items():
        if key:
            dated = [r for r in block if r["id"] in dates]
            apart = {tuple(sorted((a["id"], b["id"]))) for a in dated for b in dated
                     if a["id"] != b["id"] and dates[a["id"]] != dates[b["id"]]}
            _merge_or_queue(store, groups, distinct | apart, stats, "container", block, {"key": key, "year": year})

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


TITLE_LETTERS = 32  # compare at most this many letters: the opening line, not the whole verse
MIN_TITLE_LETTERS = 10


def title_similarity(a: str, b: str) -> float:
    """How alike two normalised titles are, comparing their openings letter by letter.

    One source may give the first line and another the whole opening, often after a spoken or sung
    lead-in ("O Sss ...", "Duniya Chala-Chali Ka Mela ... Do Din Ki Zindagi Hai"), so each title's
    opening is also tried against the other from each later word. Letters are compared, not words,
    because transliterations split words and spell vowels differently ("rang lo" / "ranglo",
    "jaadoogar" / "jadugar"). At most TITLE_LETTERS letters are compared.
    """
    wa, wb = a.split(), b.split()
    if min(len(wa), len(wb)) < 2:
        return 1.0 if wa == wb else 0.0
    best = 0.0
    for x, y in ((wa, wb), (wb, wa)):
        sx = _squash("".join(x))
        for j in range(len(y)):
            sy = _squash("".join(y[j:]))
            n = min(len(sx), len(sy), TITLE_LETTERS)
            if n < MIN_TITLE_LETTERS:
                break
            best = max(best, _prefix_similarity(sx[:n], sy) - (0.02 if j else 0.0))
            if best >= 0.999:
                return best
    return best


def catalogue_key(number: str) -> str | None:
    """A release's catalogue number reduced for matching: letters and digits only, upper case,
    without a trailing letter suffix ("BCD 16953 AR" and "BCD-16953" -> "BCD16953")."""
    key = re.sub(r"[^0-9A-Z]", "", fold(number).upper())
    key = re.sub(r"(?<=\d)[A-Z]+$", "", key)
    return key if re.search(r"\d", key) and len(key) >= 3 else None


def matrix_key(matrix: str) -> str | None:
    """A master's matrix number reduced for matching: letters and digits only, upper case
    ("E0-VB-4267" and "E0VB-4267" -> "E0VB4267"; "D4AB-0519" -> "D4AB519"). A trailing take or remake number ("-2") makes the
    key None: it names one take, which may not be the master another source lists."""
    if re.search(r"\d-\d{1,2}$", matrix.strip()):
        return None
    key = re.sub(r"[^0-9A-Z]", "", fold(matrix).upper())
    key = re.sub(r"(?<=[A-Z])0+(?=\d)", "", key)  # "D4AB-0519" and "D4-AB-519"
    return key if re.search(r"\d{3}", key) else None


def _merge_by_matrix(store, groups, distinct, stats, unit_recs) -> None:
    kinds = {r["id"]: r for r in unit_recs}
    by_key: dict[str, list[int]] = defaultdict(list)
    for r in store.conn.execute(
        """SELECT c.entity_id, c.value #>> '{}' AS v FROM claim c JOIN entity e ON e.id = c.entity_id
           WHERE e.type = %s AND c.field = 'matrix' AND c.status = 'active' ORDER BY c.entity_id""",
        (store.pack.unit,)).fetchall():
        key = matrix_key(r["v"] or "")
        if key and r["entity_id"] in kinds and r["entity_id"] not in by_key[key]:
            by_key[key].append(r["entity_id"])
    for key, ids in sorted(by_key.items()):
        for other in ids[1:]:
            a, b = groups.find(ids[0]), groups.find(other)
            if a == b or tuple(sorted((a, b))) in distinct or not groups.can_join(a, b):
                continue
            keep, gone = sorted((a, b))
            store.merge_entities(keep, gone, f"same matrix {key}")
            groups.union(keep, gone)
            stats.merged["unit"] += 1


def _catalogues_of_units(store: Store) -> dict[int, set[str]]:
    out: dict[int, set[str]] = defaultdict(set)
    for r in store.conn.execute(
        """SELECT c.entity_id, c.value #>> '{}' AS v FROM claim c JOIN entity e ON e.id = c.entity_id
           WHERE e.type = %s AND c.field = 'catalogue' AND c.status = 'active'""", (store.pack.unit,)
    ).fetchall():
        key = catalogue_key(r["v"] or "")
        if key:
            out[r["entity_id"]].add(key)
    return out


def resolve_units(store: Store, norm: Normaliser, stats: ResolveStats) -> None:
    """Units with alike titles in one context: the same container (a film, a session) or, for
    records with no shared container, the same release catalogue number (a release database
    such as MusicBrainz knows which issue a recording is on, not which session made it)."""
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
            by_container[container].extend((key, r) for r in block)
    for container, entries in by_container.items():
        _match_titles(store, groups, distinct, stats, entries, {"container": container})

    # The same matrix number (a master's ledger number) from different sources is the same recording.
    _merge_by_matrix(store, groups, distinct, stats, unit_recs)

    # Then the same catalogue number: each issue is a context of its own.
    by_catalogue: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    catalogues = _catalogues_of_units(store)
    for r in unit_recs:
        key = norm.title(r["label"])
        if key:
            for cat in catalogues.get(r["id"], ()):
                by_catalogue[cat].append((key, r))
    for cat, entries in sorted(by_catalogue.items()):
        if len({s for _k, r in entries for s in r["sources"]}) > 1:
            _match_titles(store, groups, distinct, stats, entries, {"catalogue": cat})

    if store.pack.data["canon_model"].get("match_titles_across_containers"):
        if _has_kind_containers(store):
            _match_floating_by_title(store, norm, groups, distinct, stats, unit_recs, container_of)
        else:
            _match_uncontained_by_title(store, norm, groups, distinct, stats, unit_recs)


def _has_kind_containers(store: Store) -> bool:
    return store.conn.execute(
        """SELECT EXISTS (SELECT 1 FROM claim WHERE field = 'container_kind' AND status = 'active'
             AND value #>> '{}' = %s) AS x""", (store.pack.data["canon_model"]["container"],)).fetchone()["x"]


YEAR_SLACK = 2  # a work's year differs between sources (composition, premiere, publication)


def _match_uncontained_by_title(store, norm, groups, distinct, stats, unit_recs) -> None:
    """No source places units in containers (a composer's works with no catalogue): units from
    different sources whose whole titles match are merged when each is the other's only candidate
    and their years, where both are known, are at most YEAR_SLACK apart. Other matches go to review.
    A unit of a `*_recording` kind only matches another recording, never a work."""
    years = _years(store)
    recordings = {groups.find(r["entity_id"]) for r in store.conn.execute(
        """SELECT entity_id FROM claim WHERE field = 'work_kind' AND status = 'active'
             AND value #>> '{}' LIKE '%%\\_recording' ESCAPE '\\'""").fetchall()}
    keys: dict[int, set[str]] = defaultdict(set)
    year: dict[int, int] = {}
    for r in unit_recs:
        g = groups.find(r["id"])
        keys[g] |= _title_keys(norm, r["label"])
        if r["id"] in years:
            year[g] = min(year.get(g, years[r["id"]]), years[r["id"]])

    def candidates(g):
        out = []
        for c in keys:
            if c == g or groups.find(c) != c or not groups.can_join(g, c) or tuple(sorted((g, c))) in distinct:
                continue
            if (g in recordings) != (c in recordings):
                continue  # a recording (kind *_recording) is not the work it records
            if g in year and c in year and abs(year[g] - year[c]) > YEAR_SLACK:
                continue
            sim = max((whole_title_similarity(x, y) for x in keys[g] for y in keys[c]), default=0.0)
            if sim >= WHOLE_TITLE_RATIO:
                out.append((sim, c))
        return out

    for g0 in sorted(keys):
        g = groups.find(g0)
        if g != g0:
            continue
        cands = candidates(g)
        if len(cands) == 1 and [c for _s, c in candidates(cands[0][1])] == [g]:
            sim, c = cands[0]
            keep, other = sorted((g, c))
            store.merge_entities(keep, other, f"title {sorted(keys[g])[0]!r} ~ {sorted(keys[c])[0]!r} ({sim:.2f}): "
                                              "each the other's only match, different sources")
            groups.union(keep, other)
            keys[keep] = keys.pop(other) | keys[keep]
            if other in year:
                year[keep] = min(year.get(keep, year[other]), year.pop(other))
            stats.merged["unit"] += 1
        else:
            for sim, c in cands:
                if queue_review(store, "unit_same_title", [g, c],
                                {"labels": [sorted(keys[g])[0], sorted(keys[c])[0]], "similarity": round(sim, 3),
                                 "years": [year.get(g), year.get(c)]}, priority=0):
                    stats.queued["unit"] += 1


def _release_years(store: Store) -> dict[int, int]:
    return {r["entity_id"]: r["y"] for r in store.conn.execute(
        """SELECT entity_id, min((value #>> '{}')::int) AS y FROM claim
           WHERE field = 'release_year' AND status = 'active' AND jsonb_typeof(value) = 'number'
           GROUP BY entity_id""").fetchall()}


WHOLE_TITLE_RATIO = 0.9  # canon-wide matching compares whole titles: no context narrows the field


def whole_title_similarity(a: str, b: str) -> float:
    """How alike two whole normalised titles are, ignoring a leading article and spelling-level
    differences ("The Rovin' Gambler" / "Roving Gambler"). Unlike `title_similarity`, a title that
    only opens another ("Turn the World Around" / "... the Other Way") does not score high."""
    def core(t: str) -> str:
        words = t.split()
        if len(words) > 1 and words[0] in ("the", "a", "an"):
            words = words[1:]
        return _squash("".join(words))
    x, y = core(a), core(b)
    if not x or not y:
        return 0.0
    return 1.0 if x == y else SequenceMatcher(None, x, y, autojunk=False).ratio()


def _title_keys(norm: Normaliser, label: str) -> set[str]:
    """The normalised title, and the same with any bracketed part kept ("The Richest Man (in the
    World)" is also "the richest man in the world")."""
    return {k for k in (norm.title(label), norm.title(re.sub(r"[()\[\]]", " ", label))) if k}


def _match_floating_by_title(store, norm, groups, distinct, stats, unit_recs, container_of) -> None:
    """A unit no container of the canon's kind holds (a release database's recording, seen only on
    compilations) is matched to a contained unit with the same or a near title anywhere in the canon.
    It merges when exactly one such unit could be it: from another source, recorded no later than
    the floating unit was first released. Several candidates (a song recorded more than once) go to
    review, one pair each. A container counts when its `container_kind` is the pack's container."""
    kind = store.pack.data["canon_model"]["container"]
    of_kind = {r["entity_id"] for r in store.conn.execute(
        """SELECT entity_id FROM claim WHERE field = 'container_kind' AND status = 'active'
             AND value #>> '{}' = %s""", (kind,)).fetchall()}
    years, released = _years(store), _release_years(store)
    defaults = {x["id"]: x["default_tier"] for x in store.pack.data.get("sources", []) if x.get("default_tier")}
    tiers: dict[int, str] = {}
    for r in store.conn.execute(
        """SELECT entity_id, value #>> '{}' AS t FROM claim WHERE field = 'tier' AND status = 'active'"""
    ).fetchall():
        tiers.setdefault(groups.find(r["entity_id"]), r["t"])
    anchored: dict[int, str] = {}
    floating: dict[int, str] = {}
    keys: dict[int, set[str]] = {}
    first_release: dict[int, int] = {}
    unit_year: dict[int, int] = {}
    for r in unit_recs:
        key = norm.title(r["label"])
        if r["id"] in container_of and container_of[r["id"]] in of_kind and key:
            g = groups.find(r["id"])
            anchored.setdefault(g, key)
            keys.setdefault(g, _title_keys(norm, r["label"]))
            y = years.get(r["id"]) or years.get(container_of[r["id"]])
            if y:
                unit_year[g] = min(unit_year.get(g, y), y)
    for r in unit_recs:
        g, key = groups.find(r["id"]), norm.title(r["label"])
        if key and g not in anchored:
            floating.setdefault(g, key)
            keys.setdefault(g, _title_keys(norm, r["label"]))
        if r["id"] in released:
            first_release[g] = min(first_release.get(g, released[r["id"]]), released[r["id"]])
    for g0, key in sorted(floating.items()):
        g = groups.find(g0)
        if g in anchored:
            continue  # joined an anchored unit earlier in this pass
        cands = []
        for c, ckey in anchored.items():
            if groups.find(c) != c or not groups.can_join(g, c) or tuple(sorted((g, c))) in distinct:
                continue
            if c in unit_year and g in first_release and unit_year[c] > first_release[g]:
                continue  # recorded after the floating unit was already on a release
            sim = max(whole_title_similarity(x, y) for x in keys[g0] for y in keys[c])
            if sim >= WHOLE_TITLE_RATIO:
                cands.append((sim, c, ckey))
        # A release database's recording was issued: prefer candidates of its source's default tier
        # (issued takes) over unissued alternates of the same title.
        want = {defaults[x] for x in groups.sources(g) if x in defaults}
        if len(want) == 1 and any(tiers.get(c) in want for _s, c, _k in cands):
            cands = [x for x in cands if tiers.get(x[1]) in want]
        if len(cands) == 1:
            sim, c, ckey = cands[0]
            keep, other = sorted((g, c))
            store.merge_entities(keep, other, f"title {key!r} ~ {ckey!r} ({sim:.2f}): the only {kind} unit of that "
                                              f"title (first released {first_release.get(g, '?')})")
            groups.union(keep, other)
            anchored[keep] = anchored.pop(c)
            keys[keep] = keys.pop(c) | keys.get(g0, set())
            stats.merged["unit"] += 1
        else:
            for sim, c, ckey in cands:
                if queue_review(store, "unit_same_title", [g, c],
                                {"labels": [key, ckey], "floating": g, "similarity": round(sim, 3),
                                 "candidates": len(cands),
                                 "recorded": unit_year.get(c), "first_released": first_release.get(g)},
                                priority=0):
                    stats.queued["unit"] += 1


def _match_titles(store, groups, distinct, stats, entries, context) -> None:
    """Within one context, merge each clear, mutual best title match between records of different
    sources; queue weaker or tied ones for review."""
    # Scores between groups (records already merged count as one), keeping each pair's best.
    scores: dict[tuple[int, int], tuple[float, str, str, dict, dict]] = {}
    for i, (ka, ra) in enumerate(entries):
        for j in range(i + 1, len(entries)):
            kb, rb = entries[j]
            a, b = groups.find(ra["id"]), groups.find(rb["id"])
            if a == b or (set(ra["sources"]) & set(rb["sources"])) - groups.loose:
                continue  # one source listing two titles in a context means two units
            sim = title_similarity(ka, kb)
            pair = (min(a, b), max(a, b))
            if sim >= REVIEW_TITLE_RATIO and sim > scores.get(pair, (0.0,))[0]:
                scores[pair] = (sim, ka, kb, ra, rb)
    ranked: dict[int, list[tuple[float, int]]] = defaultdict(list)  # per group: its candidates, best first
    for (a, b), (sim, *_rest) in scores.items():
        ranked[a].append((sim, b))
        ranked[b].append((sim, a))
    for lst in ranked.values():
        lst.sort(reverse=True)

    def margin(x):  # how far the best candidate leads the next one (1.0 when there is no other)
        lst = ranked[x]
        return lst[0][0] - lst[1][0] if len(lst) > 1 else 1.0

    where = ", ".join(f"{k} {v}" for k, v in context.items())
    for (a0, b0), (sim, ka, kb, ra, rb) in sorted(scores.items(), key=lambda kv: -kv[1][0]):
        a, b = groups.find(a0), groups.find(b0)
        if a == b or tuple(sorted((a, b))) in distinct:
            continue
        if not groups.can_join(a, b):
            continue  # one side's group already holds a record from the other's source
        mutual = ranked[a0][0][1] == b0 and ranked[b0][0][1] == a0
        lead = min(margin(a0), margin(b0))
        clear = lead >= TIE_MARGIN and (sim >= AUTO_TITLE_RATIO or (sim >= CLEAR_TITLE_RATIO and lead >= CLEAR_MARGIN))
        if mutual and clear:
            keep, other = sorted((a, b))
            store.merge_entities(keep, other, f"title {ka!r} ~ {kb!r} ({sim:.2f}) in {where}, different sources")
            groups.union(keep, other)
            stats.merged["unit"] += 1
        elif queue_review(store, "unit_near_title", [a, b],
                          {"labels": [ra["label"], rb["label"]], **context, "similarity": round(sim, 3)},
                          priority=1):
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
    """Close open review items whose subjects have since been merged into one entity, and items about
    a floating unit that has since been matched to a unit with a container (the question "which one
    is it?" is answered; what is left would compare two other units nobody asked about)."""
    groups = Groups(store)
    contained = {groups.find(r["entity_id"]) for r in store.conn.execute(
        """SELECT DISTINCT p.entity_id FROM claim p JOIN claim k ON k.entity_id = p.ref_entity_id
           WHERE p.field = 'part_of' AND p.status = 'active' AND k.field = 'container_kind'
             AND k.status = 'active' AND k.value #>> '{}' = %s""",
        (store.pack.data["canon_model"]["container"],)).fetchall()}
    closed = 0
    for item in open_reviews(store, limit=100000):
        floating = (item["evidence"] or {}).get("floating")
        if len({groups.find(i) for i in item["subject_ids"]}) == 1:
            store.conn.execute(
                """UPDATE review_item SET status = 'decided', decision = 'merge', note = 'merged by a resolution rule',
                   decided_at = now() WHERE id = %s""", (item["id"],))
            closed += 1
        elif floating is not None and groups.find(floating) in contained:
            store.conn.execute(
                """UPDATE review_item SET status = 'dismissed', note = 'the floating unit was matched since',
                   decided_at = now() WHERE id = %s""", (item["id"],))
            closed += 1
    return closed


def export_reviews(store: Store, limit: int = 100000) -> list[dict[str, Any]]:
    """Open review items with what a reviewer needs to decide each one: for every subject (taken at
    its merge root) its label, type, sources, and a few facts (year, container, credits, other
    identifiers). Used to build a review page outside the store."""
    groups = Groups(store)
    items = open_reviews(store, limit)
    roots = {groups.find(i) for it in items for i in it["subject_ids"]}
    members: dict[int, list[int]] = defaultdict(list)
    for r in store.conn.execute("SELECT id FROM entity").fetchall():
        g = groups.find(r["id"])
        if g in roots:
            members[g].append(r["id"])
    ids = [i for g in roots for i in members[g]]
    ent = {r["id"]: r for r in store.conn.execute(
        "SELECT id, type, label FROM entity WHERE id = ANY(%s)", (ids,)).fetchall()}
    facts: dict[int, dict[str, Any]] = {g: {"sources": set(), "facts": defaultdict(set)} for g in roots}
    rows = store.conn.execute(
        """SELECT c.entity_id, c.field, c.value, c.source_id, r.label AS ref_label, c.ref_entity_id
           FROM claim c LEFT JOIN entity r ON r.id = c.ref_entity_id
           WHERE c.entity_id = ANY(%s) AND c.status = 'active'
             AND c.field IN ('xref', 'year', 'release_year', 'part_of', 'credit', 'matrix', 'catalogue', 'title',
                             'work_kind', 'role', 'issues', 'date', 'primary_performer', 'film')""", (ids,)).fetchall()
    maestro = store.maestro_entity()
    for r in rows:
        g = groups.find(r["entity_id"])
        f = facts[g]
        if r["source_id"] not in ("claude", "user"):
            f["sources"].add(r["source_id"])
        field, v = r["field"], r["value"]
        if field == "xref":
            continue
        if field == "part_of":
            f["facts"]["in"].add(r["ref_label"] or "")
        elif field == "credit":
            if r["ref_entity_id"] != maestro:
                f["facts"][(v or {}).get("role") or "credit"].add(r["ref_label"] or "")
        elif field == "title":
            if v != ent.get(r["entity_id"], {}).get("label"):
                f["facts"]["also titled"].add(str(v))
        else:
            f["facts"][field.replace("_", " ")].add(v if isinstance(v, (int, str)) else json.dumps(v, ensure_ascii=False))
    # People: how they are credited, and on what (a few examples with the container and year).
    for r in store.conn.execute(
        """SELECT c.ref_entity_id, c.value ->> 'role' AS role, count(*) AS n,
                  (array_agg(u.label ORDER BY u.id))[1:3] AS examples
           FROM claim c JOIN entity u ON u.id = c.entity_id
           WHERE c.ref_entity_id = ANY(%s) AND c.field = 'credit' AND c.status = 'active'
           GROUP BY 1, 2""", (ids,)).fetchall():
        g = groups.find(r["ref_entity_id"])
        f = facts[g]["facts"]
        f[f"credited as {r['role']}"].add(f"{r['n']} times, e.g. " + "; ".join(r["examples"]))
    out = []
    for it in items:
        subjects = []
        for sid in it["subject_ids"]:
            g = groups.find(sid)
            e = ent.get(g) or {"type": "?", "label": "?"}
            fx = {k: sorted(map(str, v))[:6] for k, v in sorted(facts[g]["facts"].items()) if v}
            subjects.append({"id": g, "type": e["type"], "label": e["label"], "sources": sorted(facts[g]["sources"]),
                             "records": len(members[g]), "facts": fx})
        out.append({"id": it["id"], "kind": it["kind"], "priority": it["priority"],
                    "evidence": it["evidence"], "subjects": subjects})
    return out


# ---- grouped review ---------------------------------------------------------------

def review_questions(store: Store, limit: int = 100000) -> list[dict[str, Any]]:
    """Open review items grouped into questions a reviewer can answer safely.

    Merges are transitive: answering "same" to A-B and to A-C also makes B the same as C, which nobody
    was asked. So items that share a subject (at its merge root) form one question, shown whole:
    `members` are the distinct subjects, `pairs` the items it covers. When every pair is about the
    same floating unit (a record with no container of its own matched against several candidates),
    the question holds just that unit and its candidates, and `floating` names it, so a page can
    ask "which one is it?" instead.
    """
    items = export_reviews(store, limit)
    groups = Groups(store)
    # A floating unit's candidates are a question of their own ("which one is it?"): picking one
    # merges the floater into it and leaves the candidates as they are, so it cannot chain.
    by_floater: dict[int, list[dict[str, Any]]] = defaultdict(list)
    others = []
    for it in items:
        f = it["evidence"].get("floating")
        ids = [s["id"] for s in it["subjects"]]
        if f is not None and len(ids) == 2 and groups.find(f) in ids:
            by_floater[groups.find(f)].append(it)
        else:
            others.append(it)
    out = [_question(its, floating=f) for f, its in by_floater.items()]
    items = others
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent.get(x, x) != x:
            x = parent[x]
        return x

    for it in items:
        a, *bs = [s["id"] for s in it["subjects"]]
        for b in bs:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
    comps: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        comps[find(it["subjects"][0]["id"])].append(it)
    out += [_question(its) for its in comps.values()]
    out.sort(key=lambda q: (-q["priority"], q["id"]))
    return out


def _question(its: list[dict[str, Any]], floating: int | None = None) -> dict[str, Any]:
    members: dict[int, dict[str, Any]] = {}
    for it in its:
        for s in it["subjects"]:
            members.setdefault(s["id"], s)
    return {
        "id": min(it["id"] for it in its),
        "kind": Counter(it["kind"] for it in its).most_common(1)[0][0],
        "priority": max(it["priority"] for it in its),
        "floating": floating,
        "members": sorted(members.values(), key=lambda s: s["id"]),
        "pairs": [{"item": it["id"], "kind": it["kind"], "subjects": [s["id"] for s in it["subjects"]],
                   "evidence": it["evidence"]} for it in sorted(its, key=lambda x: x["id"])],
    }


def apply_groups(store: Store, item_ids: list[int], groups: list[list[int]] | None,
                 note: str | None = None) -> dict[str, int]:
    """Record a reviewer's answer to one grouped question.

    `item_ids` are the question's review items; `groups` partitions their subjects (any record of a
    subject's merge group may stand for it) into sets of one entity each, or None to skip. Each item
    whose subjects share a group is merged, each other item is marked distinct, and members of a group
    no item connects are merged too. Refuses, writing nothing, when an answer would join two groups
    through merges already in the store or put entries already marked distinct into one group.
    """
    rows = store.conn.execute(
        "SELECT id, subject_ids, status FROM review_item WHERE id = ANY(%s) ORDER BY id", (item_ids,)).fetchall()
    missing = set(item_ids) - {r["id"] for r in rows}
    if missing:
        raise ValueError(f"no review items {sorted(missing)}")
    rows = [r for r in rows if r["status"] == "open"]
    stats: Counter[str] = Counter()
    if groups is None:
        for r in rows:
            decide_review(store, r["id"], "dismiss", note)
            stats["dismiss"] += 1
        return dict(stats)
    g = Groups(store)
    group_of: dict[int, int] = {}
    for n, members in enumerate(groups):
        for m in members:
            root = g.find(m)
            if group_of.get(root, n) != n:
                raise ValueError(f"entity {m} is already merged with an entity in another group")
            group_of[root] = n
    roots = {g.find(s) for r in rows for s in r["subject_ids"]}
    if not roots <= group_of.keys():
        raise ValueError(f"every subject needs a group; missing {sorted(roots - group_of.keys())}")
    for a, b in _distinct_pairs(store):
        ra, rb = g.find(a), g.find(b)
        if ra in group_of and rb in group_of and group_of[ra] == group_of[rb]:
            raise ValueError(f"entities {a} and {b} are marked distinct but share a group")
    size = Counter(g.find(x) for x in list(g.parent))
    for r in rows:
        subj = list(r["subject_ids"])
        if len({group_of[g.find(s)] for s in subj}) == 1:
            keep = max(subj, key=lambda s: (size[g.find(s)], -s))
            decide_review(store, r["id"], "merge", note, keep_id=keep)
            for s in subj:
                g.union(keep, s)
            stats["merge"] += 1
        else:
            decide_review(store, r["id"], "distinct", note)
            stats["distinct"] += 1
    for members in groups:
        keep = members[0]
        for m in members[1:]:
            if g.find(m) != g.find(keep):
                store.merge_entities(keep, m, f"grouped review: {note or 'same group'}", source_id="user")
                g.union(keep, m)
                stats["group_merge"] += 1
    return dict(stats)
