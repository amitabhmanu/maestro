"""Completeness engine (design doc, component 6): how much of the canon is still missing?

Three independent checks, all read-only:

1. Checksums: published totals from the pack (`checksums` with a `filter`) compared with the canon.
2. Container check: for containers with a full listing, the listed unit count (`<unit>_count` claim)
   against the units actually stored in the container.
3. Capture-recapture per tier: units seen by one source lineage (f1) and by two (f2) give the Chao1
   estimate S + f1^2 / (2 f2) of the true total. Sources in one lineage copy each other, so only
   distinct lineages count. With a single lineage there is no estimate.

The stopping rule: a tier is done when its estimated missing share is below the pack's `target_missing`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from maestro.resolve import Groups, Normaliser
from maestro.store import Store

DEFAULT_TOLERANCE = 0.02


@dataclass
class Unit:
    id: int
    label: str
    tier: str | None = None
    year: int | None = None
    container: int | None = None
    credits: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))  # role -> person roots
    lineages: set[str] = field(default_factory=set)
    tier_inherited: bool = False
    tier_default: bool = False  # tier taken from the pack's default for the unit's source
    sources: set[str] = field(default_factory=set)
    live: bool = False
    pending_match: bool = False  # one of several same-title units, the choice is in review


def load_units(store: Store, groups: Groups | None = None) -> dict[int, Unit]:
    """Canon units with tier, year (own, else container's), container, credits and lineages.

    Claims on records merged into a unit count for that unit; people are reduced to their merge roots.
    """
    groups = groups or Groups(store)
    params = store._canon_params()
    units = {r["id"]: Unit(r["id"], r["label"])
             for r in store.conn.execute(store._canon_units_sql(), params).fetchall()}
    # Every record that belongs to a canon unit's group (the unit plus duplicates merged into it).
    members: dict[int, int] = {}
    for r in store.conn.execute(
        "SELECT id FROM entity WHERE type = %s", (store.pack.unit,)
    ).fetchall():
        root = groups.find(r["id"])
        if root in units:
            members[r["id"]] = root
    ids = list(members)

    # A record counts as a sighting of the unit only if it says the maestro is on it: a source
    # that lists songs without singers can confirm a film's song list but cannot find a missing song.
    crediting = {r["entity_id"] for r in store.conn.execute(
        """SELECT DISTINCT entity_id FROM claim WHERE entity_id = ANY(%(ids)s) AND field = 'credit'
             AND status = 'active' AND ref_entity_id = %(maestro)s AND value ->> 'role' = ANY(%(roles)s)""",
        {"ids": ids, "maestro": params["maestro"], "roles": params["roles"]}).fetchall()}
    rows = store.conn.execute(
        """SELECT c.entity_id, c.field, c.value, c.ref_entity_id, c.source_id, s.lineage, s.kind AS source_kind
           FROM claim c JOIN source s ON s.id = c.source_id
           WHERE c.entity_id = ANY(%s) AND c.status = 'active'
             AND c.field IN ('tier', 'year', 'part_of', 'credit', 'xref', 'title', 'live')""",
        (ids,),
    ).fetchall()
    for r in rows:
        u = units[members[r["entity_id"]]]
        f = r["field"]
        if f == "tier" and u.tier is None:
            u.tier = r["value"]
        elif f == "year" and isinstance(r["value"], int) and u.year is None:
            u.year = r["value"]
        elif f == "part_of" and u.container is None:
            u.container = groups.find(r["ref_entity_id"])
        elif f == "credit" and r["ref_entity_id"] is not None:
            u.credits[(r["value"] or {}).get("role")].add(groups.find(r["ref_entity_id"]))
        elif f == "live" and r["value"] is True:
            u.live = True
        if f == "title":
            u.sources.add(r["source_id"])
        if r["source_kind"] in ("structured", "semi", "prose") and r["entity_id"] in crediting:
            u.lineages.add(r["lineage"])

    years = _container_years(store, groups)
    for u in units.values():
        if u.year is None and u.container is not None:
            u.year = years.get(u.container)
    # A unit no source has given a tier takes its container's, when the container's other units
    # agree on one (a soundtrack recording found only on MusicBrainz, in a film the register covers).
    tiers_in: dict[int, set[str]] = defaultdict(set)
    for u in units.values():
        if u.container is not None and u.tier is not None:
            tiers_in[u.container].add(u.tier)
    for u in units.values():
        if u.tier is None and u.container is not None and len(tiers_in.get(u.container, ())) == 1:
            u.tier = next(iter(tiers_in[u.container]))
            u.tier_inherited = True
    # Still no tier: the pack may give one per source (a release database lists only issued
    # recordings, so its units are commercially issued, or live when the release says so).
    defaults = {s["id"]: s for s in store.pack.data.get("sources", []) if s.get("default_tier")}
    for u in units.values():
        if u.tier is None:
            for sid in sorted(u.sources & defaults.keys()):
                src = defaults[sid]
                u.tier = src.get("live_tier", src["default_tier"]) if u.live else src["default_tier"]
                u.tier_default = True
                break
    _settle_pending_matches(store, groups, units)
    return units


def _settle_pending_matches(store: Store, groups: Groups, units: dict[int, Unit]) -> None:
    """A floating unit waiting in review between several same-title units (a song recorded more
    than once) is one of them, not a new unit: for the estimate it is set aside and its lineages are
    lent to the first candidate of the same tier that lacks them, so it counts as a recapture, not
    as a find."""
    cands: dict[int, list[int]] = defaultdict(list)
    for r in store.conn.execute(
        """SELECT subject_ids, evidence FROM review_item
           WHERE status = 'open' AND kind = 'unit_same_title' AND evidence ? 'floating' ORDER BY id"""
    ).fetchall():
        f = groups.find(r["evidence"]["floating"])
        for sid in r["subject_ids"]:
            c = groups.find(sid)
            if c != f and c in units:
                cands[f].append(c)
    for f, cs in sorted(cands.items()):
        u = units.get(f)
        if u is None or u.pending_match or not u.tier_default:
            continue  # only a unit with no tier of its own (known from one source alone) is set aside
        u.pending_match = True
        for c in cs:
            if not units[c].pending_match and units[c].tier == u.tier and not u.lineages <= units[c].lineages:
                units[c].lineages |= u.lineages
                break


def _container_years(store: Store, groups: Groups) -> dict[int, int]:
    out: dict[int, int] = {}
    for r in store.conn.execute(
        """SELECT c.entity_id, (c.value #>> '{}')::int AS y FROM claim c JOIN entity e ON e.id = c.entity_id
           WHERE e.type = 'container' AND c.field = 'year' AND c.status = 'active'
             AND jsonb_typeof(c.value) = 'number' ORDER BY c.id"""
    ).fetchall():
        out.setdefault(groups.find(r["entity_id"]), r["y"])
    return out


# ---- checksums ------------------------------------------------------------------

def _people_named(store: Store, groups: Groups, norm: Normaliser, name: str) -> set[int]:
    key = norm.person(name)
    return {groups.find(r["id"]) for r in store.conn.execute(
        "SELECT id, label FROM entity WHERE type = 'person'").fetchall() if norm.person(r["label"]) == key}


def select_units(units: dict[int, Unit], filt: dict[str, Any], people: set[int] | None = None) -> list[Unit]:
    out = []
    for u in units.values():
        if u.pending_match:
            continue
        if "tiers" in filt and u.tier not in filt["tiers"]:
            continue
        if "years" in filt and (u.year is None or not filt["years"][0] <= u.year <= filt["years"][1]):
            continue
        if "contained" in filt and (u.container is not None) != filt["contained"]:
            continue
        if "collaborator" in filt and not (u.credits.get(filt["collaborator"]["role"], set()) & (people or set())):
            continue
        out.append(u)
    return out


def breakdown(selected: list[Unit], spec: dict[str, Any]) -> dict[str, int]:
    out = {name: 0 for name in spec["buckets"]}
    for u in selected:
        n = len(u.credits.get(spec["role"], ()))
        for name, (lo, hi) in spec["buckets"].items():
            if n >= lo and (hi is None or n <= hi):
                out[name] += 1
                break
    return out


def check_checksums(store: Store, units: dict[int, Unit], groups: Groups, norm: Normaliser) -> list[dict[str, Any]]:
    results = []
    for cs in store.pack.data.get("checksums", []):
        res: dict[str, Any] = {"scope": cs["scope"], "published": cs["total"], "source": cs["source"]}
        filt = cs.get("filter")
        if filt is None:
            res["status"] = "unchecked"
            results.append(res)
            continue
        people = None
        if "collaborator" in filt:
            people = _people_named(store, groups, norm, filt["collaborator"]["name"])
            res["collaborator_records"] = len(people)
        selected = select_units(units, filt, people)
        res["observed"] = len(selected)
        res["difference"] = len(selected) - cs["total"]
        tol = cs.get("tolerance", DEFAULT_TOLERANCE)
        rel = abs(res["difference"]) / cs["total"] if cs["total"] else (0 if not selected else 1)
        res["status"] = "match" if rel <= tol else ("over" if res["difference"] > 0 else "under")
        if cs.get("breakdown_by") and cs.get("breakdown"):
            got = breakdown(selected, cs["breakdown_by"])
            res["breakdown"] = {k: {"published": v, "observed": got.get(k, 0)} for k, v in cs["breakdown"].items()}
        results.append(res)
    return results


# ---- container check -------------------------------------------------------------

def check_containers(store: Store, groups: Groups, limit: int = 50) -> dict[str, Any]:
    """Containers whose listed unit count differs from the units stored in them (after merges)."""
    count_field = f"{store.pack.unit}_count"
    listed: dict[int, int] = {}
    labels: dict[int, str] = {}
    for r in store.conn.execute(
        """SELECT c.entity_id, e.label, (c.value #>> '{}')::int AS n FROM claim c JOIN entity e ON e.id = c.entity_id
           WHERE e.type = 'container' AND c.field = %s AND c.status = 'active'
             AND jsonb_typeof(c.value) = 'number' ORDER BY c.id""",
        (count_field,),
    ).fetchall():
        root = groups.find(r["entity_id"])
        listed[root] = max(listed.get(root, 0), r["n"])
        labels.setdefault(root, r["label"])
    listing_source = {groups.find(r["entity_id"]): r["source_id"] for r in store.conn.execute(
        "SELECT entity_id, source_id FROM claim WHERE field = %s AND status = 'active'", (count_field,)).fetchall()}
    # Units the listing source itself placed there (other sources' extra records are the outside check below).
    stored: dict[int, set[int]] = defaultdict(set)
    for r in store.conn.execute(
        """SELECT c.entity_id, c.ref_entity_id, c.source_id FROM claim c JOIN entity e ON e.id = c.entity_id
           WHERE e.type = %s AND c.field = 'part_of' AND c.status = 'active'""",
        (store.pack.unit,),
    ).fetchall():
        cont = groups.find(r["ref_entity_id"])
        if listing_source.get(cont) == r["source_id"]:
            stored[cont].add(groups.find(r["entity_id"]))
    mismatched = [
        {"container": cid, "label": labels[cid], "listed": n, "stored": len(stored.get(cid, ()))}
        for cid, n in listed.items() if len(stored.get(cid, ())) != n
    ]
    mismatched.sort(key=lambda m: -abs(m["listed"] - m["stored"]))

    # Outside check of the listings: records from other sources placed in a listed container that
    # match none of the listing source's records there. Only containers holding canon units count.
    canon_containers = {groups.find(r["ref_entity_id"]) for r in store.conn.execute(
        f"""WITH units AS ({store._canon_units_sql()})
            SELECT DISTINCT c.ref_entity_id FROM claim c JOIN units u ON u.id = c.entity_id
            WHERE c.field = 'part_of' AND c.status = 'active'""", store._canon_params()).fetchall()}
    unit_sources: dict[int, set[str]] = defaultdict(set)
    placed: list[tuple[int, int, str, str]] = []
    for r in store.conn.execute(
        """SELECT c.entity_id, c.ref_entity_id, c.source_id, e.label FROM claim c JOIN entity e ON e.id = c.entity_id
           WHERE e.type = %s AND c.field = 'part_of' AND c.status = 'active'""", (store.pack.unit,)).fetchall():
        root = groups.find(r["entity_id"])
        unit_sources[root].add(r["source_id"])
        placed.append((root, groups.find(r["ref_entity_id"]), r["source_id"], r["label"]))
    outside: dict[str, dict[str, Any]] = {}
    seen_units: set[tuple[int, str]] = set()
    for root, cont, src, label in placed:
        lister = listing_source.get(cont)
        if cont not in canon_containers or lister is None or src == lister or (root, src) in seen_units:
            continue
        seen_units.add((root, src))
        o = outside.setdefault(src, {"in_listed_containers": 0, "matched": 0, "unmatched": 0, "examples": []})
        o["in_listed_containers"] += 1
        if lister in unit_sources[root]:
            o["matched"] += 1
        else:
            o["unmatched"] += 1
            if len(o["examples"]) < limit:
                o["examples"].append({"label": label, "container": labels.get(cont, cont)})
    return {"containers_with_count": len(listed), "mismatched": len(mismatched), "examples": mismatched[:limit],
            "outside_check": outside}


# ---- capture-recapture ------------------------------------------------------------

def chao1(observed: int, f1: int, f2: int) -> float:
    if f2 > 0:
        return observed + f1 * f1 / (2 * f2)
    return observed + f1 * (f1 - 1) / 2  # bias-corrected form when no unit is seen exactly twice


def chapman(n1: int, n2: int, m: int) -> float:
    """Two-list capture-recapture (Chapman's bias-corrected Lincoln-Petersen)."""
    return (n1 + 1) * (n2 + 1) / (m + 1) - 1


def estimate_tiers(store: Store, units: dict[int, Unit]) -> dict[str, Any]:
    """Estimated totals per tier. Two lineages: Chapman; three or more: Chao1.

    Both assume the sources find units independently. Sources that favour the same (popular) units
    make the estimate too low, so read it as a floor.
    """
    out = {}
    for tier, spec in store.pack.tiers.items():
        tier_units = [u for u in units.values() if u.tier == tier and not u.pending_match]
        s = len(tier_units)
        lineages = sorted({lin for u in tier_units for lin in u.lineages})
        f1 = sum(1 for u in tier_units if len(u.lineages) == 1)
        f2 = sum(1 for u in tier_units if len(u.lineages) == 2)
        est: dict[str, Any] = {"label": spec["label"], "observed": s, "lineages": lineages, "f1": f1, "f2": f2,
                               "inherited_tier": sum(1 for u in tier_units if u.tier_inherited),
                               "default_tier": sum(1 for u in tier_units if u.tier_default),
                               "pending_match": sum(1 for u in units.values() if u.tier == tier and u.pending_match),
                               "by_lineage": {lin: sum(1 for u in tier_units if lin in u.lineages) for lin in lineages},
                               "target_missing": spec["target_missing"], "method": None}
        if s == 0 or len(lineages) < 2:
            est["estimate"] = None
            est["note"] = "not estimable: needs units seen by at least two independent source lineages"
            est["meets_target"] = None
            out[tier] = est
            continue
        if len(lineages) == 2:
            a, b = lineages
            n1, n2 = est["by_lineage"][a], est["by_lineage"][b]
            m = sum(1 for u in tier_units if {a, b} <= u.lineages)
            if m == 0:
                est.update(estimate=None, meets_target=None, method="chapman",
                           note="not estimable: the two lineages share no units yet")
                out[tier] = est
                continue
            total, est["method"] = chapman(n1, n2, m), "chapman"
        else:
            total, est["method"] = chao1(s, f1, f2), "chao1"
        est["estimate"] = round(total, 1)
        est["missing_share"] = round(max(0.0, 1 - s / total), 4) if total else 0.0
        target = spec["target_missing"]
        est["meets_target"] = None if target == "report_only" else est["missing_share"] <= target
        out[tier] = est
    unplaced = [u for u in units.values() if u.tier is None]
    out["unassigned"] = {"observed": len(unplaced),
                         "by_lineage": dict(sorted(Counter(lin for u in unplaced for lin in u.lineages).items())),
                         "in_container": sum(1 for u in unplaced if u.container is not None)}
    return out


def completeness_report(store: Store) -> dict[str, Any]:
    groups = Groups(store)
    norm = Normaliser.for_store(store)
    units = load_units(store, groups)
    return {
        "maestro": store.pack.slug,
        "unit_word": store.pack.unit,
        "canon_units": sum(1 for u in units.values() if not u.pending_match),
        "pending_match": sum(1 for u in units.values() if u.pending_match),
        "tiers": estimate_tiers(store, units),
        "checksums": check_checksums(store, units, groups, norm),
        "containers": check_containers(store, groups) if store.pack.data["canon_model"]["container_has_full_listing"] else None,
        "open_reviews": {r["kind"]: r["n"] for r in store.conn.execute(
            "SELECT kind, count(*) AS n FROM review_item WHERE status = 'open' GROUP BY kind ORDER BY kind").fetchall()},
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Completeness report: {report['maestro']}", "", f"Canon units: {report['canon_units']}"
             + (f" (plus {report['pending_match']} awaiting a same-title choice in review, counted as recaptures)"
                if report.get("pending_match") else ""), "",
             "## Tiers", "", "| Tier | Observed | Lineages | f1 | f2 | Estimate | Missing | Target |",
             "|---|---|---|---|---|---|---|---|"]
    for tier, t in report["tiers"].items():
        if tier == "unassigned":
            continue
        miss = f"{t['missing_share']:.1%}" if t.get("missing_share") is not None else "n/a"
        lines.append(f"| {tier} | {t['observed']} | {', '.join(t['lineages']) or '-'} | {t['f1']} | {t['f2']} | "
                     f"{t['estimate'] if t['estimate'] is not None else 'n/a'} | {miss} | {t['target_missing']} |")
    un = report["tiers"].get("unassigned")
    if un and un["observed"]:
        lines += ["", f"Units with no tier: {un['observed']} ({un['in_container']} in a container the registers do not "
                  f"cover; by lineage: {', '.join(f'{k} {v}' for k, v in un['by_lineage'].items())}). "
                  "They need placing before the estimates can count them."]
    lines += ["", "Estimates: two lineages use Chapman's estimator, three or more Chao1. Both assume the sources "
              "are independent; sources that favour the same popular units make the estimate a floor."]
    lines += ["", "## Published counts", "", "| Scope | Published | Observed | Difference | Status |", "|---|---|---|---|---|"]
    for c in report["checksums"]:
        lines.append(f"| {c['scope']} | {c['published']} | {c.get('observed', '-')} | "
                     f"{c.get('difference', '-')} | {c['status']} |")
        for k, b in (c.get("breakdown") or {}).items():
            lines.append(f"| &nbsp;&nbsp;{k} | {b['published']} | {b['observed']} | {b['observed'] - b['published']} | |")
    if report["containers"] is not None:
        cc = report["containers"]
        lines += ["", "## Containers", "",
                  f"{cc['mismatched']} of {cc['containers_with_count']} containers hold a different number of units "
                  "than their listing says."]
        for m in cc["examples"][:20]:
            lines.append(f"- {m['label']} (#{m['container']}): listed {m['listed']}, stored {m['stored']}")
        for src, o in cc.get("outside_check", {}).items():
            share = o["matched"] / o["in_listed_containers"] if o["in_listed_containers"] else 0
            lines += ["", f"Outside check by {src}: {o['in_listed_containers']} of its {report['unit_word']}s fall in "
                      f"listed containers holding canon units; {o['matched']} ({share:.1%}) match a listed one, "
                      f"{o['unmatched']} do not (a listing gap or a title the matcher missed). Examples:"]
            lines += [f"- {e['label']} ({e['container']})" for e in o["examples"][:10]]
    if report["open_reviews"]:
        lines += ["", "## Open review items", ""] + [f"- {k}: {n}" for k, n in report["open_reviews"].items()]
    return "\n".join(lines) + "\n"
