"""The user's gem tags from the canon explorer, kept durably.

The explorer keeps tags in its Artifact's `gem_tags` db collection (one document per tag, id
`<slug>~<unit id>`), or in the browser when served locally, and exports them as
`maestro-gem-tags/1` JSON. Songs in a film register (the explorer's Films view) are tagged too: their
tags carry the register's slug as `maestro`, the song's id in the register store as `unit`, the film's
title as `container` and its id as `film`, so they sit beside canon tags and never touch them. Neither outlives a lost Artifact or browser, so this module keeps a
**ledger**: one `maestro-gem-tags/1` file per maestro that only ever grows.

- `maestro-gem-tags merge --ledger FILE SOURCE...` folds new tags into the ledger. A SOURCE is an
  explorer export, another ledger, or a folder of `gem_tags` documents as Claude's ArtifactData tool
  saves them (`<out_dir>/gem_tags/*.json`). Tags are never dropped: with `--complete` (the source is
  the whole collection) a tag the explorer no longer holds is marked `untagged_seen_at`, and comes
  back to life if it is tagged again. A dated copy of every merge result can be kept with `--history`.
- `maestro-gem-tags record --pack ... --ledger FILE` writes the ledger into the canon store as the
  user's `pick` claims (source `user`, kind `decided`), the same claims `maestro-score --pick`
  makes. Each tag's title must still match its unit (ids belong to the snapshot the tag was made on),
  so a rebuilt canon never gets a pick on the wrong song. Untagged units have their tag pick
  superseded, never deleted.

- `maestro-gem-tags site --ledger FILE --out web/canon/gem-tags.json` writes the ledger's active tags
  as the public site's seed tags: the explorer shows them to every visitor when it has no Artifact
  tag store (Netlify, `maestro-explore --serve`), with each visitor's own browser tags on top.

The ledger is a valid `--picks-file` for `maestro-score` too.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

FORMAT = "maestro-gem-tags/1"
TAG_SOURCE = "explorer_gem_tag"  # extractor name on pick claims recorded from tags


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def key(tag: dict[str, Any]) -> str:
    return f"{tag['maestro']}~{int(tag['unit'])}"


def is_active(tag: dict[str, Any]) -> bool:
    return not tag.get("untagged_seen_at")


def read_source(path: str | Path) -> list[dict[str, Any]]:
    """Tags from an explorer export or ledger (JSON file) or a folder of gem_tags documents."""
    p = Path(path)
    if p.is_dir():
        docs = sorted(p.glob("*.json")) or sorted(p.glob("gem_tags/*.json"))
        tags = []
        for f in docs:
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict) and "maestro" in d and "unit" in d:
                tags.append(d)
        return tags
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("tags"), list):
        raise SystemExit(f"{path}: not a {FORMAT} file (explorer export or ledger)")
    return data["tags"]


def load_ledger(path: str | Path) -> dict[str, dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return {}
    return {key(t): t for t in read_source(p)}


def merge(ledger: dict[str, dict[str, Any]], incoming: Iterable[dict[str, Any]], *,
          complete_for: set[str] | None = None, now: str | None = None) -> dict[str, int]:
    """Fold incoming tags into the ledger in place; return counts of what changed.

    complete_for: maestros whose full tag set the incoming tags are; their ledger tags missing from
    it are marked untagged (kept, never removed).
    """
    now = now or _now()
    counts = {"added": 0, "retagged": 0, "updated": 0, "untagged": 0, "unchanged": 0}
    seen: set[str] = set()
    for t in incoming:
        if t.get("untagged_seen_at"):  # an untag recorded elsewhere (another ledger)
            k = key(t)
            if k in ledger and is_active(ledger[k]) and t["untagged_seen_at"] > ledger[k].get("tagged_at", ""):
                ledger[k]["untagged_seen_at"] = t["untagged_seen_at"]
                counts["untagged"] += 1
            elif k not in ledger:
                ledger[k] = dict(t)
                counts["added"] += 1
            continue
        k = key(t)
        seen.add(k)
        t = {**t, "unit": int(t["unit"])}
        old = ledger.get(k)
        if old is None:
            ledger[k] = {**t, "first_seen_at": now}
            counts["added"] += 1
            continue
        retag = not is_active(old)
        if retag:
            old.pop("untagged_seen_at", None)
            old.setdefault("history", []).append({"retagged_seen_at": now})
        # Keep the earliest tagging time; refresh descriptive fields the explorer filled in.
        if t.get("tagged_at") and (not old.get("tagged_at") or t["tagged_at"] < old["tagged_at"]):
            old["tagged_at"] = t["tagged_at"]
        changed = False
        for f in ("title", "year", "container", "rare", "reach"):
            if f in t and t[f] is not None and old.get(f) != t[f]:
                old[f] = t[f]
                changed = True
        counts["retagged" if retag else "updated" if changed else "unchanged"] += 1
    for k, t in ledger.items():
        if complete_for and t["maestro"] in complete_for and k not in seen and is_active(t):
            t["untagged_seen_at"] = now
            t.setdefault("history", []).append({"untagged_seen_at": now})
            counts["untagged"] += 1
    return counts


def ledger_json(ledger: dict[str, dict[str, Any]], now: str | None = None) -> str:
    tags = sorted(ledger.values(), key=lambda t: (t["maestro"], t.get("year") or 9999, t["unit"]))
    return json.dumps({
        "format": FORMAT,
        "kind": "ledger",
        "updated_at": now or _now(),
        "about": ("The user's gem tags from the canon explorer, merged by maestro-gem-tags. Never shrinks: "
                  "a tag removed in the explorer keeps its row with untagged_seen_at. Record them in a canon "
                  "store with: maestro-gem-tags record --pack maestros/<slug> --ledger <this file>"),
        "active": sum(1 for t in tags if is_active(t)),
        "tags": tags,
    }, ensure_ascii=False, indent=1) + "\n"


def write_ledger(path: str | Path, ledger: dict[str, dict[str, Any]], history: str | Path | None = None) -> None:
    now = _now()
    text = ledger_json(ledger, now)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    if history:
        h = Path(history)
        h.mkdir(parents=True, exist_ok=True)
        (h / f"{p.stem}-{now.replace(':', '').replace('-', '')}.json").write_text(text, encoding="utf-8")


# ---- the canon store --------------------------------------------------------------------

def _fold(s: str) -> str:
    from maestro.resolve import fold
    return fold(s or "")


def record(store, ledger: dict[str, dict[str, Any]], slug: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Write a maestro's ledger tags into the store as the user's pick claims."""
    tags = [t for t in ledger.values() if t["maestro"] == slug]
    ids = [int(t["unit"]) for t in tags]
    titles: dict[int, set[str]] = {}
    for r in store.conn.execute(
            "SELECT e.id, e.label, c.value FROM entity e LEFT JOIN claim c ON c.entity_id = e.id "
            "AND c.field IN ('title', 'first_line') AND c.status = 'active' WHERE e.id = ANY(%s)", (ids,)).fetchall():
        names = titles.setdefault(r["id"], {_fold(r["label"])})
        if isinstance(r["value"], str):
            names.add(_fold(r["value"]))
    picked = {r["entity_id"]: r["id"] for r in store.conn.execute(
        "SELECT id, entity_id FROM claim WHERE field = 'pick' AND source_id = 'user' AND status = 'active' "
        "AND value = 'true'::jsonb AND entity_id = ANY(%s)", (ids,)).fetchall()}
    out: dict[str, Any] = {"picked": 0, "already": 0, "unpicked": 0, "missing": [], "mismatch": []}
    for t in tags:
        uid, want = int(t["unit"]), _fold(t.get("title", ""))
        if uid not in titles:
            out["missing"].append(t)
            continue
        if want and not any(want == n or want.startswith(n) or n.startswith(want) for n in titles[uid] if n):
            out["mismatch"].append(t)
            continue
        if is_active(t):
            if uid in picked:
                out["already"] += 1
            else:
                out["picked"] += 1
                if not dry_run:
                    store.upsert_claim(uid, "pick", value=True, source_id="user", kind="decided",
                                       extractor=TAG_SOURCE)
        elif uid in picked:
            # Only a pick a tag made is undone; a pick from maestro-score --pick stays.
            out["unpicked"] += 1
            if not dry_run:
                store.conn.execute("UPDATE claim SET status = 'superseded' WHERE id = %s AND extractor = %s",
                                   (picked[uid], TAG_SOURCE))
    if not dry_run:
        store.conn.commit()
    return out


SITE_FIELDS = ("maestro", "unit", "title", "year", "container", "tagged_at", "film", "singers")


def site_tags(ledger: dict[str, dict[str, Any]], now: str | None = None) -> str:
    """The ledger's active tags as a `maestro-gem-tags/1` file for the public site (no ledger bookkeeping)."""
    tags = [{k: t[k] for k in SITE_FIELDS if t.get(k) is not None}
            for t in sorted(ledger.values(), key=lambda t: (t["maestro"], int(t["unit"]))) if is_active(t)]
    return json.dumps({"format": FORMAT, "kind": "site", "exported_at": now or _now(),
                       "about": "Gem tags shown to every visitor of the public explorer site (from the ledger).",
                       "tags": tags}, ensure_ascii=False, indent=1) + "\n"


# ---- CLI --------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="maestro-gem-tags", description="Keep the canon explorer's gem tags durably.")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge", help="fold explorer tags into a ledger file (never drops a tag)")
    m.add_argument("--ledger", required=True, help="the ledger JSON (created if missing)")
    m.add_argument("--complete", action="store_true",
                   help="the sources are the explorer's whole tag set: mark ledger tags they lack as untagged")
    m.add_argument("--history", metavar="DIR", help="also keep a dated copy of the merged ledger here")
    m.add_argument("sources", nargs="+", help="explorer exports, ledgers, or folders of gem_tags documents")
    r = sub.add_parser("record", help="write a ledger's tags into a canon store as the user's picks")
    r.add_argument("--pack", default=os.environ.get("MAESTRO_PACK"), required=os.environ.get("MAESTRO_PACK") is None)
    r.add_argument("--dsn", help="default $MAESTRO_DSN_<SLUG>, then $MAESTRO_DSN (.env is read)")
    r.add_argument("--ledger", required=True)
    r.add_argument("--dry-run", action="store_true")
    w = sub.add_parser("site", help="write a ledger's active tags as the public site's seed tags")
    w.add_argument("--ledger", required=True)
    w.add_argument("--out", required=True, help="e.g. web/canon/gem-tags.json")
    args = p.parse_args(argv)

    if args.cmd == "site":
        ledger = load_ledger(args.ledger)
        Path(args.out).write_text(site_tags(ledger), encoding="utf-8")
        print(f"{args.out}: {sum(1 for t in ledger.values() if is_active(t))} tag(s)")
        return 0

    if args.cmd == "merge":
        ledger = load_ledger(args.ledger)
        incoming = [t for s in args.sources for t in read_source(s)]
        complete = {t["maestro"] for t in incoming} if args.complete else None
        counts = merge(ledger, incoming, complete_for=complete)
        write_ledger(args.ledger, ledger, args.history)
        active = sum(1 for t in ledger.values() if is_active(t))
        print(f"{args.ledger}: {active} active tag(s), {len(ledger)} kept in all; "
              + ", ".join(f"{v} {k}" for k, v in counts.items()))
        return 0

    from maestro import config
    from maestro.pack import load_pack
    from maestro.store import Store

    pack = load_pack(args.pack)
    dsn = config.dsn_for(pack.slug, args.dsn)
    if not dsn:
        p.error(f"--dsn (or {config.dsn_env_name(pack.slug)} or MAESTRO_DSN) is required")
    store = Store.connect(dsn, pack)
    try:
        res = record(store, load_ledger(args.ledger), pack.slug, dry_run=args.dry_run)
    finally:
        store.close()
    print(f"{pack.slug}: {res['picked']} pick(s) recorded, {res['already']} already there, "
          f"{res['unpicked']} untagged pick(s) superseded{' (dry run)' if args.dry_run else ''}")
    for what in ("missing", "mismatch"):
        for t in res[what]:
            print(f"  skipped ({'no such unit' if what == 'missing' else 'title differs'}): "
                  f"unit {t['unit']} “{t.get('title')}” ({t.get('year')})", file=sys.stderr)
    return 1 if res["missing"] or res["mismatch"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
