"""The orchestrator loop: resolve, measure, plan, crawl, and again, until every tier meets its target.

Each round resolves entities, runs the completeness report and plans from it. The plan has two
parts. **Jobs** are what workers can do unaided: sweeps a pack's sources declare (a URL template
over years or pages) that have not been fetched yet, and listings of containers whose stored units
fall short of the count their source gives. **Needs** are what only a person, or new code, can supply:
a tier that one source lineage alone sees, registers for a short tier that are in print or cannot be
crawled, open review items, units with no tier. The loop queues the jobs within the pack's
per-round budget, runs them, and stops when the targets are met, when a round finds nothing left to
crawl (the needs then say what would move the estimates), or after the rounds it was given.

Nothing here is maestro-specific: what to sweep and how to address a container come from the pack.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlparse

from maestro.completeness import completeness_report
from maestro.resolve import resolve
from maestro.store import Store

# ---- planning ---------------------------------------------------------------------

def sweep_urls(source: dict[str, Any]) -> list[str]:
    """The URLs a source's `sweep` names: its template filled with every year in `years` or every
    page from 1 to `pages`."""
    sweep = source.get("sweep")
    if not sweep:
        return []
    url = sweep["url"]
    if "years" in sweep:
        a, b = sweep["years"]
        return [url.format(year=y) for y in range(a, b + 1)]
    if "pages" in sweep:
        step = sweep.get("page_size", 1)
        return [url.format(page=p, offset=(p - 1) * step) for p in range(1, sweep["pages"] + 1)]
    return [url]


def _fetched_or_queued(store: Store) -> set[tuple[str, str]]:
    return {(r["url"], r["extractor"]) for r in store.conn.execute(
        "SELECT DISTINCT url, extractor FROM job WHERE status IN ('queued', 'running', 'done')").fetchall()}


def _container_jobs(store: Store, report: dict[str, Any], seen: set[tuple[str, str]]) -> list[dict[str, Any]]:
    """Containers short of the units their listing source counts, refetched from that source when it
    says how to address one (`container_url`, a template over the container's id in that source)."""
    containers = report.get("containers") or {}
    short = [c for c in containers.get("examples", []) if c["stored"] < c["listed"]]
    if not short:
        return []
    by_source = {s["id"]: s for s in store.pack.data.get("sources", []) if s.get("container_url")}
    jobs = []
    for c in short:
        for r in store.conn.execute(
            """SELECT x.source_id, x.value #>> '{}' AS xid FROM claim x
               WHERE x.entity_id = %s AND x.field = 'xref' AND x.status = 'active'""", (c["container"],)).fetchall():
            src = by_source.get(r["source_id"])
            if src is None or not r["xid"]:
                continue
            url = src["container_url"].format(xref=r["xid"], xref_q=quote(r["xid"]))
            extractor = src.get("extractor") or src.get("sweep", {}).get("extractor") or src["id"]
            if (url, extractor) in seen:
                continue
            seen.add((url, extractor))
            jobs.append({"kind": "container_listing", "url": url, "extractor": extractor, "entity_id": c["container"],
                         "purpose": "container_sweep",
                         "why": f"{c['label']}: {c['stored']} of {c['listed']} listed units stored"})
    return jobs


def _host(url: str | None) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.") if url else ""


def plan(store: Store, report: dict[str, Any]) -> dict[str, Any]:
    """What the next round should do, from a completeness report."""
    pack = store.pack.data
    tiers = {t: v for t, v in report["tiers"].items() if t != "unassigned"}
    status = {}
    for t, v in tiers.items():
        status[t] = {"observed": v["observed"], "estimate": v.get("estimate"), "missing_share": v.get("missing_share"),
                     "target": v["target_missing"], "meets_target": v.get("meets_target")}
    open_tiers = [t for t, v in tiers.items() if v["target_missing"] != "report_only" and v.get("meets_target") is not True]

    seen = _fetched_or_queued(store)
    jobs: list[dict[str, Any]] = []
    for src in pack.get("sources", []):
        if src.get("access") not in ("api", "crawlable") or not src.get("sweep"):
            continue
        extractor = src["sweep"].get("extractor") or src.get("extractor") or src["id"]
        for url in sweep_urls(src):
            if (url, extractor) not in seen:
                seen.add((url, extractor))
                jobs.append({"kind": "sweep", "url": url, "extractor": extractor, "source": src["id"],
                             "purpose": src["sweep"].get("purpose", "spine"), "why": f"sweep of {src['id']}"})
    jobs += _container_jobs(store, report, seen)

    needs: list[dict[str, Any]] = []
    source_hosts = {_host(s.get("url")) for s in pack.get("sources", [])} - {""}
    for t in open_tiers:
        v = tiers[t]
        if v.get("estimate") is None:
            needs.append({"tier": t, "kind": "second_lineage",
                          "what": f"tier {t} ({v['label']}): {v.get('note', 'not estimable')}"})
        for part in v.get("strata", []):
            if len(part["lineages"]) < 2 and part["observed"]:
                needs.append({"tier": t, "kind": "single_lineage_years",
                              "what": f"tier {t}: {part['observed']} units only {', '.join(part['lineages'])} can see; "
                                      "a source covering those years would make them estimable"})
        for reg in pack.get("registers", []):
            if t not in reg.get("tiers", []):
                continue
            host = _host(reg.get("url"))
            if host and host in source_hosts:
                continue  # already read (crawled, or imported from pages a person saved)
            if reg["access"] in ("api", "crawlable"):
                needs.append({"tier": t, "kind": "extractor", "register": reg["name"],
                              "what": f"tier {t}: {reg['name']} is crawlable but no source reads it yet"})
            else:
                needs.append({"tier": t, "kind": "person", "register": reg["name"], "access": reg["access"],
                              "what": f"tier {t}: {reg['name']} ({reg['access']}) needs a person: "
                                      "saved pages, a scan or a typed list"})
    used = {r["source_id"] for r in store.conn.execute("SELECT DISTINCT source_id FROM claim").fetchall()}
    for src in pack.get("sources", []):
        if src.get("access") in ("api", "crawlable") and src["id"] not in used and not src.get("sweep"):
            needs.append({"kind": "extractor", "source": src["id"],
                          "what": f"source {src['id']} ({src.get('url', 'no url')}) is open to crawling but has "
                                  "no sweep or extractor yet, so it adds nothing"})
    unassigned = report["tiers"].get("unassigned", {}).get("observed", 0)
    if unassigned:
        needs.append({"kind": "untiered", "what": f"{unassigned} units have no tier, so no estimate counts them"})
    reviews = sum(report.get("open_reviews", {}).values())
    if reviews:
        needs.append({"kind": "review", "what": f"{reviews} open review items (maestro-review --export --grouped)"})
    return {"tiers": status, "open_tiers": open_tiers, "jobs": jobs, "needs": needs}


# ---- the loop ---------------------------------------------------------------------

def run_rounds(store: Store, fetcher: Any, *, rounds: int = 3, max_jobs: int | None = None,
               dry_run: bool = False) -> list[dict[str, Any]]:
    """Run up to `rounds` rounds and return one summary per round. `fetcher` is a
    `maestro.crawl.fetcher.Fetcher` (unused on a dry run)."""
    from maestro.crawl.runner import run_jobs

    budget = store.pack.data["budget"]["crawl_requests_per_round"]
    max_jobs = min(max_jobs or budget, budget)
    out: list[dict[str, Any]] = []
    for n in range(1, rounds + 1):
        if dry_run:
            # A dry run writes nothing: resolution's merges and review items are rolled back after planning.
            with store.conn.transaction(force_rollback=True):
                resolved = resolve(store).as_dict()
                report = completeness_report(store)
                p = plan(store, report)
        else:
            resolved = resolve(store).as_dict()
            report = completeness_report(store)
            p = plan(store, report)
        summary: dict[str, Any] = {"round": n, "at": datetime.now(UTC).isoformat(timespec="seconds"),
                                   "resolved": resolved, **p}
        out.append(summary)
        if not p["open_tiers"]:
            summary["stop"] = "every tier meets its target"
            break
        if dry_run:
            summary["stop"] = "dry run: planned only"
            break
        queued = 0
        for job in p["jobs"][:max_jobs]:
            store.enqueue_crawl(job["url"], job["extractor"], job.get("purpose", job["kind"]),
                                entity_id=job.get("entity_id"), priority=10)
            queued += 1
        waiting = store.conn.execute("SELECT count(*) AS n FROM job WHERE status = 'queued'").fetchone()["n"]
        if not waiting:
            summary["stop"] = "nothing left to crawl: the needs below are what would move the estimates"
            break
        summary["queued"] = queued
        summary["ran"] = run_jobs(store, fetcher, max_jobs=max_jobs).as_dict()
    return out


def render_markdown(slug: str, rounds: list[dict[str, Any]]) -> str:
    lines = [f"# Orchestrator run: {slug}", ""]
    for r in rounds:
        lines.append(f"## Round {r['round']} ({r['at']})")
        lines.append("")
        lines.append("| Tier | Observed | Estimate | Missing | Target | Meets |")
        lines.append("|---|---|---|---|---|---|")
        for t, v in r["tiers"].items():
            miss = f"{v['missing_share']:.1%}" if v.get("missing_share") is not None else "n/a"
            est = v["estimate"] if v["estimate"] is not None else "n/a"
            meets = {True: "yes", False: "no", None: "-"}[v["meets_target"]]
            lines.append(f"| {t} | {v['observed']} | {est} | {miss} | {v['target']} | {meets} |")
        lines.append("")
        lines.append(f"Jobs planned: {len(r['jobs'])}" + (f"; queued {r['queued']}, ran {json.dumps(r['ran'])}"
                                                           if "ran" in r else ""))
        for j in r["jobs"][:10]:
            lines.append(f"- {j['kind']}: {j['url']} ({j['why']})")
        if r["needs"]:
            lines.append("")
            lines.append("Needs:")
            lines += [f"- {x['what']}" for x in r["needs"]]
        if r.get("stop"):
            lines += ["", f"Stopped: {r['stop']}."]
        lines.append("")
    return "\n".join(lines)
