"""Praguefrank's Country Discographies, Eddy Arnold part 2 (releases), from a page saved by hand.

The blog's robots.txt forbids crawling, so the page is copied as text and loaded with
`maestro-import --extractor praguefrank --url <post url> <file>`.

Part 2 lists releases in three sections: albums (LPs and CDs), EPs ("EPS") and singles ("SINGLES",
with label sub-headings such as "RCA Victor (1943-72)"). Each line is
`<label> <catalogue> <title>: <tracks> - <date> (<notes>)`; singles are `<catalogue> <A side> / <B side> - <date>`.
Dates are MM-YY, DD-MM-YY or YY. Long lines are sometimes wrapped onto the next line.
Each release becomes a `release` entity with catalogue, label, title, date and `track` claims.
Recordings are not created here: matching track titles to recordings is entity resolution's job.
"""

from __future__ import annotations

import re

SOURCE = "praguefrank"
_DATE = re.compile(
    r"\s+[-–]\s*(?P<date>(?:\d{1,2}-){0,2}\d{2}|unknown[^(]*|ca\.\s*\d{2}|unissued)\s*(?:\((?P<notes>.*)\)\s*\)?)?\s*$",
    re.I,
)
_SECTION = {"eps": "ep", "singles": "single"}
_LABEL_HEADING = re.compile(r"^[A-Z][\w&./ -]*?(?:\s*\(\d{4}-\d{2,4}\))?$")
_CAT_TOKEN = re.compile(r"^(?=.*\d)(?:[\w.,/=()\[\]]*[-/,][\w.,/=()\[\]-]*|\d{4,})$")


def _year(date: str) -> int | None:
    m = re.search(r"(\d{2})$", date or "")
    if not m:
        return None
    yy = int(m.group(1))
    year = 1900 + yy if yy >= 40 else 2000 + yy
    return year if year <= 2026 else None  # e.g. a typo "37" for "67": leave the year unknown


def _split_head(head: str) -> tuple[str, str, str]:
    """`RCA Victor LPM-3027 Country Classics` -> (label, catalogue, title)."""
    tokens = head.split()
    start = next((i for i, t in enumerate(tokens) if _CAT_TOKEN.match(t)), None)
    if start is None:
        m = re.match(r"^(?P<label>.*?)\s*\b(?P<cat>[A-Z]{2,5}\s?\d{2,5})\s+(?P<title>.+)$", head.strip())
        if m:
            return m.group("label"), m.group("cat"), m.group("title")
        return "", "", head.strip()
    end = start
    while end + 1 < len(tokens) and _CAT_TOKEN.match(tokens[end + 1]):
        end += 1
    return " ".join(tokens[:start]), " ".join(tokens[start:end + 1]), " ".join(tokens[end + 1:])


def _tracks(text: str) -> list[str]:
    parts = re.split(r"\s*;\s*|\s+/\s+|\s*/\s*(?=[A-Z(])", text)
    return [p.strip(" .*") for p in parts if p.strip(" .*")]


def _starts_record(line: str) -> bool:
    return ":" in line or " / " in line


def _logical_lines(text: str) -> list[str]:
    """Join wrapped lines: an undated line is continued by the next line unless that one starts a record."""
    out: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower() in _SECTION:
            if pending:
                out.append(pending)
            pending = ""
            if line:
                out.append(line)
            continue
        if pending and not _DATE.search(pending) and not _starts_record(line):
            pending = f"{pending} {line}"
        else:
            if pending:
                out.append(pending)
            pending = line
    if pending:
        out.append(pending)
    return out


def parse(text: str) -> list[dict]:
    releases = []
    kind, label = "album", ""
    for line in _logical_lines(text):
        low = line.lower()
        if low in _SECTION:
            kind, label = _SECTION[low], ""
            continue
        m = _DATE.search(line)
        if not m:
            if kind != "album" and _LABEL_HEADING.match(line) and len(line) < 40:
                label = re.sub(r"\s*\(.*\)$", "", line)
            continue
        body, date, notes = line[: m.start()], m.group("date").strip(), (m.group("notes") or "").strip()
        if kind == "single":
            sm = re.match(r"((?:[A-Z]{1,3}\s(?=\d))?\S+)\s+(.*)$", body.strip())
            if not sm:
                continue
            cat, sides = sm.groups()
            if '"' in sides:  # album sets listed among singles, e.g. P-195 "All Time Hits From The Hills"
                continue
            releases.append({"kind": kind, "label": label, "catalogue": cat, "title": sides.strip(),
                             "date": date, "year": _year(date), "notes": notes, "tracks": _tracks(sides)})
            continue
        head, sep, tracks = body.partition(":")
        if not sep:
            continue
        lab, cat, title = _split_head(head)
        if kind == "ep" and not lab:
            lab = label
        releases.append({"kind": kind, "label": lab, "catalogue": cat, "title": title or head.strip(),
                         "date": date, "year": _year(date), "notes": notes, "tracks": _tracks(tracks)})
    return releases


def extract(ctx) -> None:
    store, raw = ctx.store, ctx.raw_page_id
    maestro = store.maestro_entity()

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="praguefrank", extractor_version="1", raw_page_id=raw)

    text = ctx.page.body.decode("utf-8", errors="replace")
    for r in parse(text):
        key = f"{r['label']}|{r['catalogue']}|{r['title']}"
        rel = store.find_or_create_by_xref("release", SOURCE, key, r["title"] or r["catalogue"])
        claim(rel, "release_kind", r["kind"])
        for field in ("label", "catalogue", "title", "notes"):
            if r[field]:
                claim(rel, field, r[field])
        claim(rel, "release_date", r["date"])
        if r["year"]:
            claim(rel, "year", r["year"])
        claim(rel, "credit", {"role": "singer"}, ref=maestro)
        for n, t in enumerate(r["tracks"], 1):
            claim(rel, "track", {"position": n, "title": t})
