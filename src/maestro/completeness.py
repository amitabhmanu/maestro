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

from collections import defaultdict
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

    rows = store.conn.execute(
        """SELECT c.entity_id, c.field, c.value, c.ref_entity_id, s.lineage, s.kind AS source_kind
           FROM claim c JOIN source s ON s.id = c.source_id
           WHERE c.entity_id = ANY(%s) AND c.status = 'active'
             AND c.field IN ('tier', 'year', 'part_of', 'credit', 'xref', 'title')""",
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
        if r["source_kind"] in ("structured", "semi", "prose"):
            u.lineages.add(r["lineage"])

    years = _container_years(store, groups)
    for u in units.values():
        if u.year is None and u.container is not None:
            u.year = years.get(u.container)
    return units


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
    stored: dict[int, set[int]] = defaultdict(set)
    for r in store.conn.execute(
        """SELECT c.entity_id, c.ref_entity_id FROM claim c JOIN entity e ON e.id = c.entity_id
           WHERE e.type = %s AND c.field = 'part_of' AND c.status = 'active'""",
        (store.pack.unit,),
    ).fetchall():
        stored[groups.find(r["ref_entity_id"])].add(groups.find(r["entity_id"]))
    mismatched = [
        {"container": cid, "label": labels[cid], "listed": n, "stored": len(stored.get(cid, ()))}
        for cid, n in listed.items() if len(stored.get(cid, ())) != n
    ]
    mismatched.sort(key=lambda m: -abs(m["listed"] - m["stored"]))
    return {"containers_with_count": len(listed), "mismatched": len(mismatched), "examples": mismatched[:limit]}


# ---- capture-recapture ------------------------------------------------------------

def chao1(observed: int, f1: int, f2: int) -> float:
    if f2 > 0:
        return observed + f1 * f1 / (2 * f2)
    return observed + f1 * (f1 - 1) / 2  # bias-corrected form when no unit is seen exactly twice


def estimate_tiers(store: Store, units: dict[int, Unit]) -> dict[str, Any]:
    out = {}
    for tier, spec in store.pack.tiers.items():
        tier_units = [u for u in units.values() if u.tier == tier]
        s = len(tier_units)
        lineages = sorted({lin for u in tier_units for lin in u.lineages})
        f1 = sum(1 for u in tier_units if len(u.lineages) == 1)
        f2 = sum(1 for u in tier_units if len(u.lineages) == 2)
        est: dict[str, Any] = {"label": spec["label"], "observed": s, "lineages": lineages, "f1": f1, "f2": f2,
                               "target_missing": spec["target_missing"]}
        if s == 0 or len(lineages) < 2:
            est["estimate"] = None
            est["note"] = "not estimable: needs units seen by at least two independent source lineages"
            est["meets_target"] = None
        else:
            total = chao1(s, f1, f2)
            est["estimate"] = round(total, 1)
            est["missing_share"] = round(1 - s / total, 4) if total else 0.0
            target = spec["target_missing"]
            est["meets_target"] = None if target == "report_only" else est["missing_share"] <= target
        out[tier] = est
    return out


def completeness_report(store: Store) -> dict[str, Any]:
    groups = Groups(store)
    norm = Normaliser.for_store(store)
    units = load_units(store, groups)
    return {
        "maestro": store.pack.slug,
        "canon_units": len(units),
        "tiers": estimate_tiers(store, units),
        "checksums": check_checksums(store, units, groups, norm),
        "containers": check_containers(store, groups) if store.pack.data["canon_model"]["container_has_full_listing"] else None,
        "open_reviews": {r["kind"]: r["n"] for r in store.conn.execute(
            "SELECT kind, count(*) AS n FROM review_item WHERE status = 'open' GROUP BY kind ORDER BY kind").fetchall()},
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [f"# Completeness report: {report['maestro']}", "", f"Canon units: {report['canon_units']}", "",
             "## Tiers", "", "| Tier | Observed | Lineages | f1 | f2 | Estimate | Missing | Target |",
             "|---|---|---|---|---|---|---|---|"]
    for tier, t in report["tiers"].items():
        miss = f"{t['missing_share']:.1%}" if t.get("missing_share") is not None else "n/a"
        lines.append(f"| {tier} | {t['observed']} | {', '.join(t['lineages']) or '-'} | {t['f1']} | {t['f2']} | "
                     f"{t['estimate'] if t['estimate'] is not None else 'n/a'} | {miss} | {t['target_missing']} |")
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
    if report["open_reviews"]:
        lines += ["", "## Open review items", ""] + [f"- {k}: {n}" for k, n in report["open_reviews"].items()]
    return "\n".join(lines) + "\n"
