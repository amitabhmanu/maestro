"""`maestro-review-desk`: a review page for grouped review questions, and its answers back as --apply files.

    maestro-review --export rafi-q.json --grouped      (per maestro, on the machine with the store)
    maestro-review-desk page --round twins_round1 --title "..." --out desk.html rafi-q.json lata-q.json
    (publish desk.html as an Artifact with the `db` and `user` capabilities; the reviewer answers)
    maestro-review-desk answers --maestro rafi --out rafi-answers.json <ArtifactData list dir or JSON>
    maestro-review --apply rafi-answers.json

The page shows each question whole (every subject that its items share), so answers cannot chain.
Each question starts with a suggested grouping: songs whose titles carry no differing version marker
("Part 2", "Male", "Sad", "Version 2") start in one group, the reviewer's standing rule that similar
titles are the same song unless the records call them different. The suggestion is only a starting
point; nothing is recorded until the reviewer saves an answer.
"""

from __future__ import annotations

import argparse
import json
import re
from importlib import resources
from pathlib import Path
from typing import Any

from maestro.resolve import fold

_MARKERS = [
    ("part", re.compile(r"\b(?:part|pt)\.?\s*(\d+|i{1,3}|iv)\b")),
    ("voice", re.compile(r"\b(male|female)\b")),
    ("mood", re.compile(r"\b(happy|sad)\b")),
    ("version", re.compile(r"\b(?:version|ver)\.?\s*(\d+)\b")),
    ("tempo", re.compile(r"\b(fast|slow)\b")),
    ("kind", re.compile(r"\b(reprise|revival|remix|jhankar|duet|solo)\b")),
]


def version_markers(label: str) -> dict[str, str]:
    """The version a title names, by kind ({"part": "2", "voice": "female"}); empty when it names none."""
    t = fold(label or "")
    out = {}
    for kind, rx in _MARKERS:
        m = rx.search(t)
        if m:
            out[kind] = m.group(1)
    return out


def suggest_groups(members: list[dict[str, Any]]) -> list[list[int]]:
    """A starting grouping for one question: members whose version markers do not disagree join one
    group (an unmarked copy joins the first group it fits); members naming another version stay apart.
    People and films are not grouped: each starts alone."""
    if any(m.get("type") != "song" for m in members):
        return [[m["id"]] for m in members]
    groups: list[tuple[dict[str, str], list[int]]] = []
    for m in sorted(members, key=lambda m: -len(version_markers(m.get("label", "")))):
        mk = version_markers(m.get("label", ""))
        for gm, ids in groups:
            if all(gm.get(k, v) == v for k, v in mk.items()):
                gm.update(mk)
                ids.append(m["id"])
                break
        else:
            groups.append((dict(mk), [m["id"]]))
    return [ids for _, ids in groups]


def build_page(exports: list[dict[str, Any]], round_name: str, title: str, about: str = "") -> str:
    data: dict[str, list[dict[str, Any]]] = {}
    for ex in exports:
        qs = []
        for q in ex["questions"]:
            qs.append({"q": f"{ex['maestro']}:{q['id']}", "kind": q["kind"], "members": q["members"],
                       "items": [p["item"] for p in q["pairs"]],
                       "pairs": [{"a": p["subjects"][0], "b": p["subjects"][-1],
                                  "similarity": (p.get("evidence") or {}).get("similarity")} for p in q["pairs"]],
                       "suggest": suggest_groups(q["members"])})
        data.setdefault(ex["maestro"], []).extend(qs)
    page = resources.files("maestro").joinpath("review_desk.html").read_text("utf-8")
    payload = json.dumps({"round": round_name, "title": title, "about": about, "maestros": data},
                         ensure_ascii=False).replace("</", "<\\/")
    return (page.replace("__TITLE__", _html(title))
            .replace("/*__DATA__*/{}", payload))


def _html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def answers_for(rows: list[dict[str, Any]], maestro: str) -> dict[str, Any]:
    """The page's saved answers for one maestro, as `maestro-review --apply` reads them (latest per question)."""
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = r.get("data", r)
        if d.get("maestro") != maestro or not d.get("items"):
            continue
        q = d.get("q") or ",".join(map(str, d["items"]))
        if q not in latest or (d.get("at") or "") >= (latest[q].get("at") or ""):
            latest[q] = d
    out = []
    for d in latest.values():
        if d.get("choice") == "groups" and d.get("groups"):
            out.append({"items": d["items"], "groups": d["groups"], "note": f"review desk {d.get('round', '')}".strip()})
        elif d.get("choice") == "skip":
            continue  # skipped stays open: nothing to record
    return {"answers": out}


def _read_rows(path: Path) -> list[dict[str, Any]]:
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    rows: list[dict[str, Any]] = []
    for f in files:
        x = json.loads(f.read_text("utf-8"))
        if isinstance(x, dict):
            x = x.get("docs") or x.get("documents") or x.get("rows") or [x]
        rows += [r for r in x if isinstance(r, dict)]
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="maestro-review-desk", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pg = sub.add_parser("page", help="write a review page from grouped exports")
    pg.add_argument("exports", nargs="+", help="files from maestro-review --export FILE --grouped")
    pg.add_argument("--round", required=True, help="the page's db collection for answers (letters, digits, _)")
    pg.add_argument("--title", default="Canon Review Desk")
    pg.add_argument("--about", default="")
    pg.add_argument("--out", required=True)
    an = sub.add_parser("answers", help="turn saved answers into an --apply file for one maestro")
    an.add_argument("rows", help="an ArtifactData list output (file or folder of JSON)")
    an.add_argument("--maestro", required=True)
    an.add_argument("--out", required=True)
    args = p.parse_args(argv)
    if args.cmd == "page":
        if not re.fullmatch(r"[A-Za-z0-9_]+", args.round):
            p.error("--round takes letters, digits and _ only")
        exports = [json.loads(Path(f).read_text("utf-8")) for f in args.exports]
        Path(args.out).write_text(build_page(exports, args.round, args.title, args.about), "utf-8")
        n = sum(len(e["questions"]) for e in exports)
        print(f"{n} questions from {', '.join(e['maestro'] for e in exports)} written to {args.out}")
    else:
        res = answers_for(_read_rows(Path(args.rows)), args.maestro)
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
        print(f"{len(res['answers'])} answers for {args.maestro} written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
