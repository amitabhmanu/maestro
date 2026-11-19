"""The songs of a film register: each film's song list with its credits, and the songs that are not new
musical offerings.

`load_songs(store)` reads every song of every film (publishable claims only: a long HFGK title is read
from its publishable `first_line`), grouped by the film's group root. `mark_reuse(songs, years)` marks
songs that repeat an earlier film's song:

  - `reused`: the same song in a later film, as HFGK lists it: the same opening words (consonants of the
    first four words), the same music director and a singer in common. The earliest film keeps it.
  - `recreated`: a later film's new recording of an older composition: the title marks it
    ("Recreated", "Remix", "Revisited") or a note says so, or its opening words match an older film's
    song whose music director it credits.

Neither counts as a new musical offering (`new_songs` in a film's row): a song reused in a later film
is the earlier film's song. Versions inside one film ("Reprise", "Female", "Sad") are the same
composition too, so a film's count of compositions leaves them out.

`roles(singers)` describes a song's singing: solo, duet, trio or group, and whether a chorus joins.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from maestro.resolve import Groups, fold

CHORUS = re.compile(r"\b(chorus|korus|others|group|children|kids)\b", re.I)
RECREATION = re.compile(r"\b(recreat\w*|remix\w*|revisit\w*|reloaded|redux|reprised from|original(?:ly)? (?:from|by|composed)"
                        r"|from the (?:\d{4} )?film|old song|rehash\w*)\b", re.I)
SAME_COMPOSITION = {"reprise", "female", "male", "sad", "duet", "unplugged", "version", "film version", "instrumental",
                    "theme", "extended", "acoustic", "lofi", "lo-fi"}


def title_key(title: str | None, words: int = 4) -> str:
    """The consonants of a title's first words: "Tute Na Dil Tute Na" meets "Toote Na Dil Toote Na"."""
    from maestro.films import skeleton

    t = re.sub(r"\([^)]*\)", " ", fold(title or ""))
    w = re.sub(r"[^a-z0-9 ]+", " ", t).split()
    keys = [k for k in (skeleton(x) for x in w) if k]  # vowel-only words ("A", "Aa") spelt every way: left out
    return " ".join(keys[:words])


def load_songs(store, groups: Groups | None = None) -> dict[int, list[dict[str, Any]]]:
    groups = groups or Groups(store)
    songs: dict[int, dict[str, Any]] = {}
    for r in store.conn.execute(
            """SELECT m.entity_id AS song, m.ref_entity_id AS film, m.value ->> 'position' AS pos, m.source_id
               FROM claim m JOIN entity e ON e.id = m.entity_id AND e.type = 'song'
               WHERE m.field = 'part_of' AND m.status = 'active'""").fetchall():
        songs[r["song"]] = {"id": r["song"], "film_record": r["film"], "film": groups.find(r["film"]),
                            "n": int(r["pos"]) if (r["pos"] or "").isdigit() else None, "src": r["source_id"],
                            "title": None, "first_line": None, "sing": [], "lyr": [], "md": [], "screen": [],
                            "raga": [], "dur": None, "ver": None, "note": None, "part": None}
    rows = store.conn.execute(
        """SELECT c.entity_id, c.field, c.value, p.label AS person FROM claim c
           JOIN entity e ON e.id = c.entity_id AND e.type = 'song'
           LEFT JOIN entity p ON p.id = c.ref_entity_id
           WHERE c.status = 'active' AND c.visibility = 'publishable'
             AND c.field IN ('title', 'first_line', 'credit', 'credit_unlinked', 'on_screen', 'raga', 'duration',
                             'version', 'note', 'album_part')
           ORDER BY c.id""").fetchall()
    roles = {"singer": "sing", "lyricist": "lyr", "composer": "md"}
    for r in rows:
        s = songs.get(r["entity_id"])
        if s is None:
            continue
        f, v = r["field"], r["value"]
        if f in ("title", "first_line") and s[f] is None and isinstance(v, str):
            s[f] = v
        elif f in ("credit", "credit_unlinked") and isinstance(v, dict):
            key, name = roles.get(v.get("role")), r["person"] or v.get("name")
            if key and name and name not in s[key]:
                s[key].append(name)
        elif f == "on_screen" and r["person"] and r["person"] not in s["screen"]:
            s["screen"].append(r["person"])
        elif f == "raga" and isinstance(v, str) and v not in s["raga"]:
            s["raga"].append(v)
        elif f == "duration":
            s["dur"] = v
        elif f == "version":
            s["ver"] = v
        elif f == "note":
            s["note"] = v
        elif f == "album_part":
            s["part"] = v
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for s in songs.values():
        s["title"] = s["title"] or s["first_line"]
        if s["title"]:
            out[s["film"]].append(s)
    for film_songs in out.values():
        film_songs.sort(key=lambda s: (s["src"] != "hfgk_online", s["n"] or 0, s["id"]))
    return out


def roles(singers: list[str]) -> dict[str, Any]:
    voices = [n for n in singers if not CHORUS.search(n)]
    chorus = len(voices) < len(singers)
    kind = {0: "chorus" if chorus else "", 1: "solo", 2: "duet", 3: "trio"}.get(len(voices), "group")
    return {"kind": kind, "chorus": chorus}


def _names_overlap(a: list[str], b: list[str]) -> bool:
    from maestro.films import names_agree

    return bool(names_agree(a, b))


def mark_reuse(by_film: dict[int, list[dict[str, Any]]], years: dict[int, int | None],
               titles: dict[int, str]) -> int:
    """Mark songs that repeat an earlier film's song (see the module docstring). Returns how many."""
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for film, songs in by_film.items():
        for s in songs:
            k = title_key(s["title"])
            if len(k.replace(" ", "")) >= 5:
                index[k].append(s)
    marked = 0
    for film, songs in by_film.items():
        y = years.get(film)
        for s in songs:
            if s.get("reuse"):
                continue
            text = " ".join(filter(None, [s["title"], s.get("note"), s.get("part"), s.get("ver")]))
            earlier = []
            k = title_key(s["title"])
            for o in index.get(k, []):
                oy = years.get(o["film"])
                if o["film"] == film or oy is None or y is None or oy >= y:
                    continue
                earlier.append(o)
            earlier.sort(key=lambda o: years[o["film"]])
            for o in earlier:
                same_md = _names_overlap(s["md"], o["md"])
                if s["src"] == o["src"] == "hfgk_online" and same_md and _names_overlap(s["sing"], o["sing"]):
                    s["reuse"] = {"kind": "reused", "film": o["film"], "title": titles.get(o["film"]),
                                  "year": years[o["film"]], "rule": "same opening words, music director and singer"}
                    break
                if s["src"] != "hfgk_online" and same_md:
                    s["reuse"] = {"kind": "recreated", "film": o["film"], "title": titles.get(o["film"]),
                                  "year": years[o["film"]], "rule": "an older film's song, its music director credited"}
                    break
            if not s.get("reuse") and s["src"] != "hfgk_online" and RECREATION.search(text):
                o = earlier[0] if earlier else None
                s["reuse"] = {"kind": "recreated", "rule": "the listing says it is a recreation or remix",
                              **({"film": o["film"], "title": titles.get(o["film"]), "year": years[o["film"]]}
                                 if o else {})}
            if s.get("reuse"):
                marked += 1
    return marked


def is_new_composition(s: dict[str, Any]) -> bool:
    return not s.get("reuse") and (s.get("ver") or "").lower() not in SAME_COMPOSITION
