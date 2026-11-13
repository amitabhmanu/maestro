"""Command-line helpers."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from maestro.pack import PackError, load_pack


def validate_main(argv: list[str] | None = None) -> int:
    """Validate maestro packs: `maestro-validate maestros/rafi maestros/eddy-arnold`.

    With no arguments, validates every folder under maestros/.
    """
    args = sys.argv[1:] if argv is None else argv
    paths = [Path(a) for a in args] or sorted(p for p in Path("maestros").iterdir() if p.is_dir())
    failed = 0
    for path in paths:
        try:
            pack = load_pack(path)
        except PackError as exc:
            failed += 1
            print(f"FAIL {exc}")
        else:
            print(f"ok   {pack.slug} ({pack.data['status']}): unit={pack.unit}, container={pack.container}")
    return 1 if failed else 0


def _store(p: argparse.ArgumentParser, argv: list[str] | None):
    from maestro.store import Store

    p.add_argument("--pack", default=os.environ.get("MAESTRO_PACK"), help="maestro folder (default $MAESTRO_PACK)")
    p.add_argument("--dsn", default=os.environ.get("MAESTRO_DSN"), help="Postgres DSN (default $MAESTRO_DSN)")
    args = p.parse_args(argv)
    if not args.pack or not args.dsn:
        p.error("--pack and --dsn (or MAESTRO_PACK and MAESTRO_DSN) are required")
    store = Store.connect(args.dsn, load_pack(args.pack))
    store.migrate()
    store.sync_sources()
    return args, store


def resolve_main(argv: list[str] | None = None) -> int:
    """`maestro-resolve`: merge clear duplicates and queue unclear ones for review."""
    from maestro.resolve import resolve

    _, store = _store(argparse.ArgumentParser(description=resolve_main.__doc__), argv)
    print(json.dumps(resolve(store).as_dict(), indent=2))
    store.close()
    return 0


def review_main(argv: list[str] | None = None) -> int:
    """`maestro-review`: list open review items, or decide one.

    maestro-review                       list the open items, highest priority first
    maestro-review 12 merge [--keep ID]  the subjects of item 12 are one entity
    maestro-review 12 distinct           they are different entities (never queued again)
    maestro-review 12 dismiss            close the item without a decision
    maestro-review --export items.json   every open item with its evidence, as JSON
    maestro-review --export q.json --grouped
                                         the open items grouped into questions that cannot chain
    maestro-review --apply answers.json  record a reviewer's answers to grouped questions:
                                         {"answers": [{"items": [...], "groups": [[ids], ...] or null}]}
    """
    from maestro.resolve import decide_review, open_reviews

    p = argparse.ArgumentParser(description=review_main.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("item", nargs="?", type=int)
    p.add_argument("decision", nargs="?", choices=["merge", "distinct", "dismiss"])
    p.add_argument("--keep", type=int, help="for merge: the entity to keep (default: the lowest id)")
    p.add_argument("--note")
    p.add_argument("--kind", help="list only this kind")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--export", metavar="FILE", help="write every open item with its evidence as JSON (for a review page)")
    p.add_argument("--grouped", action="store_true",
                   help="with --export: group items into questions, so answers to pairs cannot chain")
    p.add_argument("--apply", metavar="FILE", help="record answers to grouped questions from a JSON file")
    args, store = _store(p, argv)
    if args.export and args.grouped:
        from maestro.resolve import review_questions

        qs = review_questions(store)
        Path(args.export).write_text(json.dumps({"maestro": store.pack.slug, "questions": qs}, ensure_ascii=False), "utf-8")
        print(f"{len(qs)} questions ({sum(len(q['pairs']) for q in qs)} items) written to {args.export}")
    elif args.export:
        from maestro.resolve import export_reviews

        items = export_reviews(store)
        Path(args.export).write_text(json.dumps({"maestro": store.pack.slug, "items": items}, ensure_ascii=False), "utf-8")
        print(f"{len(items)} open items written to {args.export}")
    elif args.apply:
        from collections import Counter

        from maestro.resolve import apply_groups

        total: Counter[str] = Counter()
        failed = 0
        for a in json.loads(Path(args.apply).read_text("utf-8"))["answers"]:
            try:
                total.update(apply_groups(store, a["items"], a.get("groups"), a.get("note") or args.note))
            except ValueError as e:
                failed += 1
                print(f"items {a['items']}: not applied: {e}")
        print(json.dumps(dict(total)) + (f"; {failed} answers not applied" if failed else ""))
    elif args.item is None:
        for r in open_reviews(store, args.limit, args.kind):
            print(f"#{r['id']} [{r['kind']}, priority {r['priority']}] {r['subject_ids']}: "
                  f"{json.dumps(r['evidence'], ensure_ascii=False)}")
    elif args.decision is None:
        p.error("give a decision: merge, distinct or dismiss")
    else:
        claim = decide_review(store, args.item, args.decision, args.note, args.keep)
        print(f"item {args.item}: {args.decision}" + (f" (claim {claim})" if claim else ""))
    store.close()
    return 0


def run_main(argv: list[str] | None = None) -> int:
    """`maestro-run`: the orchestrator loop. Each round resolves, measures, plans and crawls what it
    can, until every tier meets its target, nothing is left to crawl, or --rounds is reached. Prints
    (and with --save writes) each round's estimates, the jobs it ran and what it needs from a person."""
    from maestro.crawl.fetcher import Fetcher, api_hosts
    from maestro.orchestrate import render_markdown, run_rounds

    p = argparse.ArgumentParser(description=run_main.__doc__)
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--max-jobs", type=int, help="jobs per round (default and ceiling: the pack's budget)")
    p.add_argument("--dry-run", action="store_true", help="plan one round without crawling")
    p.add_argument("--cache-dir", default=None, help="raw page cache (default data/<slug>/raw)")
    p.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    p.add_argument("--save", action="store_true", help="also write maestros/<slug>/runs/<time>-run.{md,json}")
    args, store = _store(p, argv)
    pack = store.pack
    fetcher = Fetcher(Path(args.cache_dir or Path("data") / pack.slug / "raw"),
                      rps=pack.data["budget"]["per_domain_rps"], api_hosts=api_hosts(pack.data))
    rounds = run_rounds(store, fetcher, rounds=args.rounds, max_jobs=args.max_jobs, dry_run=args.dry_run)
    md = render_markdown(pack.slug, rounds)
    if args.save:
        runs = pack.path.parent / "runs"
        runs.mkdir(exist_ok=True)
        stem = runs / f"{rounds[0]['at'][:16].replace(':', '')}-run"
        stem.with_suffix(".md").write_text(md, encoding="utf-8")
        stem.with_suffix(".json").write_text(json.dumps(rounds, indent=2, ensure_ascii=False, default=str), "utf-8")
    print(json.dumps(rounds, indent=2, ensure_ascii=False, default=str) if args.json else md)
    store.close()
    return 0


def report_main(argv: list[str] | None = None) -> int:
    """`maestro-report`: completeness report (tiers, published counts, containers, open reviews)."""
    from maestro.completeness import completeness_report, render_markdown

    p = argparse.ArgumentParser(description=report_main.__doc__)
    p.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    p.add_argument("--save", action="store_true", help="also write maestros/<slug>/runs/<date>-report.{md,json}")
    args, store = _store(p, argv)
    report = completeness_report(store)
    md = render_markdown(report)
    if args.save:
        runs = store.pack.path.parent / "runs"
        runs.mkdir(exist_ok=True)
        stem = runs / f"{date.today().isoformat()}-report"
        stem.with_suffix(".md").write_text(md, encoding="utf-8")
        stem.with_suffix(".json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str) if args.json else md)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(validate_main())
