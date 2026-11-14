"""Pilot: can intrinsic, opinion-free signals find Rafi's rare gems?

Reads a canon explorer snapshot (``maestro-explore`` output) and scores every
Hindi film song (tier A) on metadata signals measured against Rafi's own norm,
then checks whether those signals rank the expert-named gems above ordinary
songs. No crawling, no audio, no lyrics: titles are only used as keys.

    python maestros/rafi/pilots/intrinsic_gems.py SNAPSHOT.json [--out DIR]

Writes ``report.md`` (AUC tables) and ``candidates.csv`` (top unpopular songs by
the combined score) into ``--out``.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import random
from pathlib import Path

RAFI = 6  # person id of Mohammed Rafi in the snapshot


def load(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return d["units"], d["containers"], d["people"]


def facets(u):
    return set(u.get("pop", {}).get("facets", {}))


def signals_of(u):
    return {e["signal"] for e in u.get("pop", {}).get("evidence", [])}


def rarity(count):
    """Higher for things Rafi did rarely."""
    return 1.0 / math.log(1 + max(count, 1))


def build_features(units, containers):
    a = [u for u in units if u["tier"] == "A" and u.get("year")]
    first = lambda u, role: (u["credits"].get(role) or [None])[0]
    comp = collections.Counter(first(u, "composer") for u in a)
    lyr = collections.Counter(first(u, "lyricist") for u in a)
    team = collections.Counter((first(u, "composer"), first(u, "lyricist")) for u in a)
    costar = collections.Counter(s for u in a for s in u["credits"]["singer"] if s != RAFI)
    actor = collections.Counter(p for u in a for p in (u.get("on_screen") or [])[:1])
    film_load = collections.Counter(u["container"] for u in a)
    per_year = collections.Counter(u["year"] for u in a)
    banner = collections.Counter(
        (containers.get(str(u["container"])) or {}).get("banner", [None])[0] for u in a
    )
    subject = collections.Counter(
        (containers.get(str(u["container"])) or {}).get("subject", [None])[0] for u in a
    )
    comp_years = collections.defaultdict(list)
    for u in a:
        comp_years[first(u, "composer")].append(u["year"])
    peak_year = max(per_year.values())

    rows = []
    for u in a:
        c = containers.get(str(u["container"])) or {}
        k = first(u, "composer")
        ys = sorted(comp_years[k])
        span = (ys[-1] - ys[0]) or 1
        others = [s for s in u["credits"]["singer"] if s != RAFI]
        act = (u.get("on_screen") or [None])[0]
        f = {
            "solo": 1.0 if not others else 0.0,
            "composer_rare": rarity(comp[k]) if k else None,
            "lyricist_rare": rarity(lyr[first(u, "lyricist")]) if first(u, "lyricist") else None,
            "team_rare": rarity(team[(k, first(u, "lyricist"))]) if k and first(u, "lyricist") else None,
            "costar_rare": (sum(rarity(costar[s]) for s in others) / len(others)) if others else None,
            "actor_rare": rarity(actor[act]) if act else None,
            "banner_rare": rarity(banner[c.get("banner", [None])[0]]) if c.get("banner") else None,
            "subject_rare": rarity(subject[c.get("subject", [None])[0]]) if c.get("subject") else None,
            "few_rafi_songs_in_film": rarity(film_load[u["container"]]) if u["container"] else None,
            "early_in_composer_career": 1 - (u["year"] - ys[0]) / span if k and len(ys) > 4 else None,
            "lean_year_for_rafi": 1 - per_year[u["year"]] / peak_year,
        }
        rows.append((u, f))
    return rows


RARITY = [
    "composer_rare", "lyricist_rare", "team_rare", "costar_rare", "actor_rare",
    "banner_rare", "subject_rare", "few_rafi_songs_in_film",
]


def percentile_ranks(rows, name):
    vals = sorted(f[name] for _, f in rows if f[name] is not None)
    out = {}
    for u, f in rows:
        v = f[name]
        if v is None:
            continue
        lo = _bisect(vals, v, left=True)
        hi = _bisect(vals, v, left=False)
        out[u["id"]] = (lo + hi) / 2 / len(vals)
    return out


def _bisect(a, x, left):
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x or (not left and a[mid] == x):
            lo = mid + 1
        else:
            hi = mid
    return lo


def auc(pos, neg):
    """Probability that a random positive outscores a random negative (ties = 1/2)."""
    if not pos or not neg:
        return None
    neg_s = sorted(neg)
    tot = 0.0
    for p in pos:
        tot += _bisect(neg_s, p, True) + (_bisect(neg_s, p, False) - _bisect(neg_s, p, True)) / 2
    return tot / (len(pos) * len(neg))


def strat_auc(pos, neg, key):
    """AUC comparing only within the same stratum (decade x solo), pooled."""
    by = collections.defaultdict(lambda: ([], []))
    for s, v in pos:
        by[s][0].append(v)
    for s, v in neg:
        by[s][1].append(v)
    num = den = 0.0
    for p, n in by.values():
        if p and n:
            num += auc(p, n) * len(p) * len(n)
            den += len(p) * len(n)
    return num / den if den else None


def boot_ci(pos, neg, n=400, seed=7):
    rng = random.Random(seed)
    xs = []
    for _ in range(n):
        p = [rng.choice(pos) for _ in pos]
        q = [rng.choice(neg) for _ in neg]
        xs.append(auc(p, q))
    xs.sort()
    return xs[int(0.05 * n)], xs[int(0.95 * n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshot")
    ap.add_argument("--out", default="data/pilots/intrinsic")
    args = ap.parse_args()
    units, containers, people = load(args.snapshot)
    rows = build_features(units, containers)
    names = list(rows[0][1])
    pct = {n: percentile_ranks(rows, n) for n in names}
    for u, f in rows:
        r = [pct[n][u["id"]] for n in RARITY if u["id"] in pct[n]]
        f["atypicality"] = sum(r) / len(r) if r else None
    pct["atypicality"] = percentile_ranks(rows, "atypicality")
    names.append("atypicality")

    gem = lambda u: bool(u.get("pop", {}).get("gem"))
    low = lambda u: not (facets(u) & {"contemporary", "enduring"})
    expert = lambda u: bool(signals_of(u) & {"songsofyore", "expert_posts"})
    hit = lambda u: "contemporary" in facets(u)
    stratum = lambda u: ((u["year"] // 10) * 10, len(u["credits"]["singer"]) == 1)
    lists = collections.defaultdict(set)
    for u, _ in rows:
        for e in u.get("pop", {}).get("evidence", []):
            if e.get("url") and e["signal"] == "songsofyore":
                lists[e["note"]].add(u["id"])

    groups = {
        "gems vs other unpopular songs": (
            [u for u, _ in rows if gem(u)],
            [u for u, _ in rows if low(u) and not gem(u) and not expert(u)],
        ),
        "any expert-listed song vs unlisted songs": (
            [u for u, _ in rows if expert(u) and not gem(u)],
            [u for u, _ in rows if not expert(u)],
        ),
        "hits vs other songs (sanity check)": (
            [u for u, _ in rows if hit(u)],
            [u for u, _ in rows if not hit(u)],
        ),
    }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    lines = ["# Intrinsic gem pilot: Rafi, Hindi film songs (tier A)", ""]
    lines.append(f"{len(rows)} songs scored. AUC 0.5 = no better than chance; above 0.5 = the signal "
                 "ranks the first group higher. 'Matched' compares only songs of the same decade and "
                 "the same solo/duet status, which removes the lists' own era and solo bias.")
    for title, (pos, neg) in groups.items():
        lines += ["", f"## {title} ({len(pos)} vs {len(neg)})", "",
                  "| signal | AUC | 90% CI | matched AUC |", "|---|---|---|---|"]
        for n in names:
            p = [pct[n][u["id"]] for u in pos if u["id"] in pct[n]]
            q = [pct[n][u["id"]] for u in neg if u["id"] in pct[n]]
            if len(p) < 5:
                continue
            a = auc(p, q)
            lo, hi = boot_ci(p, q)
            ps = [(stratum(u), pct[n][u["id"]]) for u in pos if u["id"] in pct[n]]
            qs = [(stratum(u), pct[n][u["id"]]) for u in neg if u["id"] in pct[n]]
            m = strat_auc(ps, qs, stratum)
            lines.append(f"| {n} | {a:.2f} | {lo:.2f}-{hi:.2f} | {m:.2f} |")

    lines += ["", "## Each expert list vs unpopular unlisted songs (atypicality and composer rarity)", "",
              "| list | songs | atypicality AUC | composer_rare AUC |", "|---|---|---|---|"]
    neg = [u for u, _ in rows if low(u) and not expert(u)]
    for note, ids in sorted(lists.items(), key=lambda kv: -len(kv[1])):
        pos = [u for u, _ in rows if u["id"] in ids]
        row = []
        for n in ("atypicality", "composer_rare"):
            p = [pct[n][u["id"]] for u in pos if u["id"] in pct[n]]
            q = [pct[n][u["id"]] for u in neg if u["id"] in pct[n]]
            row.append(auc(p, q))
        lines.append(f"| {note.split(': ', 1)[-1][:80]} | {len(pos)} | {row[0]:.2f} | {row[1]:.2f} |")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    name = lambda i: people.get(str(i), {}).get("name", "") if i else ""
    cand = sorted(
        ((u, f) for u, f in rows if low(u) and not expert(u) and u["id"] in pct["atypicality"]),
        key=lambda uf: -pct["atypicality"][uf[0]["id"]],
    )[:200]
    with open(out / "candidates.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["unit", "title", "year", "film", "composer", "lyricist", "solo", "atypicality_pct"])
        for u, f in cand:
            c = containers.get(str(u["container"])) or {}
            w.writerow([u["id"], u["title"], u["year"], c.get("title", ""),
                        name((u["credits"].get("composer") or [None])[0]),
                        name((u["credits"].get("lyricist") or [None])[0]),
                        int(f["solo"]), round(pct["atypicality"][u["id"]], 3)])
    print((out / "report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
