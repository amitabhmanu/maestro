"""Praguefrank's Country Discographies for Eddy Arnold, from pages copied by hand.

Part 1 lists recording sessions; part 2 lists releases. The page's first line says which part it is.


The blog's robots.txt forbids crawling, so the page is copied as text and loaded with
`maestro-import --extractor praguefrank --url <post url> <file>`.

Part 1: after "SESSIONS", a session line `<date> [<times>] <studio, address> - Eddy Arnold (<personnel>)`
is followed by take lines `<seq> [<matrix>] <TITLE IN CAPITALS> <releases>`. Each session becomes a
`container`; each take a `recording` with matrix, title, session date and issue numbers, and a tier
(A issued, B unissued).

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


_TAKE = re.compile(r"^(?P<seq>\d{3,4})\s+(?P<rest>.+)$")
_MATRIX = re.compile(r"^(?=[^ ]*\d)(?=[^ ]*-)[A-Z0-9]+(?:-[A-Z0-9?]+)+$")
_MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}


def _is_release_start(tokens: list[str], i: int) -> bool:
    t = tokens[i]
    if re.search(r"[a-z]", t) or t[0].isdigit() and not re.match(r"^\d+[A-Z']*$", t) and "-" in t:
        return True
    if re.match(r"^(unissued|unssued)", t, re.I):
        return True
    if re.match(r"^[A-Z]{1,4}-?\d", t):  # LSP-2337, K14478/SE, SE-4878
        return True
    nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
    if re.match(r"^[A-Z]{2,4}$", t) and re.match(r"^\d+/?$", nxt):  # LSP 2578, SE 4878, BCD 17205
        return True
    return t in {"RCA", "MGM", "BMG", "BNJ"} and (nxt[:1].isdigit() or _is_release_start([nxt], 0))  # RCA 82876-70710-2, BNJ BR-101


def _split_take(rest: str) -> tuple[str | None, str, str]:
    tokens = rest.split()
    matrix = tokens.pop(0) if tokens and _MATRIX.match(tokens[0]) else None
    for i in range(len(tokens)):
        if i > 0 and _is_release_start(tokens, i):
            return matrix, " ".join(tokens[:i]), " ".join(tokens[i:])
    return matrix, " ".join(tokens), ""


def _session_date(head: str) -> tuple[str, str | None, int | None]:
    m = re.match(r"^(?:ca\.?\s+|early\s+|late\s+|spring\s+|summer\s+|autumn\s+|fall\s+|winter\s+)*"
                 r"(?:(\d{1,2})\s*)?([A-Za-z]+(?:/[A-Za-z]+)?)?\s*(\d{4})", head, re.I)
    if not m:
        return "", None, None
    raw = m.group(0).strip()
    day, month, year = m.group(1), (m.group(2) or "").lower(), int(m.group(3))
    iso = None
    if day and month in _MONTHS:
        iso = f"{year:04d}-{_MONTHS[month]:02d}-{int(day):02d}"
    return raw, iso, year


def parse_sessions(text: str) -> list[dict]:
    sessions: list[dict] = []
    started = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper() == "SESSIONS":
            started = True
            continue
        if not started:
            continue
        if re.search(r"-\s*Eddy Arnold", line) and not re.match(r"^\d{3,4}\s+[A-Z0-9]+-", line):
            date_raw, iso, year = _session_date(line)
            where = re.split(r"\s*-\s*Eddy Arnold", line, maxsplit=1)[0]
            where = where[len(date_raw):].strip() if date_raw and where.startswith(date_raw) else where
            where = re.sub(r"^\[[^\]]*\]\s*", "", where)
            pm = re.search(r"Eddy Arnold\s*\((.*?)\)?\s*$", line)
            sessions.append({"header": line, "date": date_raw, "iso_date": iso, "year": year,
                             "place": where.strip(), "personnel": pm.group(1) if pm else "", "takes": []})
            continue
        t = _TAKE.match(line)
        if t and sessions:
            matrix, title, releases = _split_take(t.group("rest"))
            unissued = not releases or bool(re.match(r"^(unissued|unssued)", releases, re.I))
            sessions[-1]["takes"].append({"seq": int(t.group("seq")), "matrix": matrix, "title": title,
                                          "releases": releases, "issued": not unissued})
            continue
        if sessions:
            sessions[-1].setdefault("notes", []).append(line)
    return sessions


def extract(ctx) -> None:
    store, raw = ctx.store, ctx.raw_page_id
    maestro = store.maestro_entity()

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="praguefrank", extractor_version="1", raw_page_id=raw)

    text = ctx.page.body.decode("utf-8", errors="replace")
    if re.search(r"part 1\b", text[:200], re.I):
        _extract_sessions(ctx, claim, maestro, text)
        return
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


def _extract_sessions(ctx, claim, maestro, text: str) -> None:
    store = ctx.store
    for n, sess in enumerate(parse_sessions(text), 1):
        key = f"session:{sess['date'] or n}|{sess['place'][:60]}"
        sid = store.find_or_create_by_xref("container", SOURCE, key, f"{sess['date']} {sess['place']}".strip())
        claim(sid, "container_kind", "session")
        if sess["date"]:
            claim(sid, "session_date", sess["date"])
        if sess["iso_date"]:
            claim(sid, "date", sess["iso_date"])
        if sess["year"]:
            claim(sid, "year", sess["year"])
        if sess["place"]:
            claim(sid, "place", sess["place"])
        if sess["personnel"]:
            claim(sid, "personnel", sess["personnel"])
        for note in sess.get("notes", []):
            claim(sid, "note", note)
        claim(sid, "credit", {"role": "singer"}, ref=maestro)
        for pos, take in enumerate(sess["takes"], 1):
            rid = store.find_or_create_by_xref("recording", SOURCE, f"seq:{take['seq']}", take["title"])
            claim(rid, "title", take["title"])
            claim(rid, "praguefrank_seq", take["seq"])
            if take["matrix"]:
                claim(rid, "matrix", take["matrix"])
                store.upsert_claim(rid, "xref", value=f"matrix:{take['matrix']}", source_id=SOURCE)
            claim(rid, "part_of", {"position": pos}, ref=sid)
            claim(rid, "issues", take["releases"] or "unissued")
            claim(rid, "credit", {"role": "singer"}, ref=maestro)
            store.upsert_claim(rid, "tier", value="A" if take["issued"] else "B", source_id="claude",
                               kind="decided", extractor="praguefrank")
