"""Find the best legal recording of each tagged gem (Rafi, Lata, ...), and say how good it is.

Run on a machine that can reach archive.org and commons.wikimedia.org (the PC):

    python maestros/rafi/pilots/gem_recordings.py search --gems GEMS.json --out DIR
        [--before gems.csv ...] [--measure] [--rps 1]

``GEMS.json`` lists the gems: ``[{"maestro", "unit", "title", "year", "container"}, ...]`` (rows
of the gem-tags ledger). ``--before`` takes earlier ``find-gems`` outputs (``gems.csv``) so the
report can say what each gem had before.

Sources, each searched in several ways (title words alone, title with film, film with singer):

* Internet Archive items (mediatype audio): every audio file of each item, with format, bitrate
  (size over length), the item's collection and what its description says about the transfer
  (78 rpm, LP, cassette, CD, remaster).
* Wikimedia Commons audio files (freely licensed by Commons' own rule).

Legal posture (CLAUDE.md, Amitabh's approval 14 Nov 2026): only songs published in 1965 or
earlier (public domain in India) are searched; later gems are listed with what would fill the gap.
No piracy sites, no scraping against terms: two public APIs, robots.txt honoured, one request a
second. ``--measure`` fetches each gem's top candidates to a temporary file, measures quality
(bandwidth, noise floor, clipping, duration) and deletes the file at once. Nothing else is kept.

Writes ``candidates.csv`` (every candidate), ``best.csv`` (one row per gem) and ``summary.json``.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audio_gems import LAST_PD_YEAR, Http, _discard, _year, norm, sim  # noqa: E402

IA_SEARCH = "https://archive.org/advancedsearch.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"
NAMES = {"rafi": ["mohammed rafi", "mohd rafi", "md rafi", "rafi"],
         "lata": ["lata mangeshkar", "lata"]}
LOSSLESS = {"Flac", "FLAC", "24bit Flac", "WAVE", "Apple Lossless Audio", "AIFF"}
LOSSY = {"VBR MP3", "MP3", "128Kbps MP3", "64Kbps MP3", "Ogg Vorbis", "Ogg Opus"}
TRANSFER = [  # (pattern in item text, label); first match wins
    (r"\b78\s*-?\s*(rpm|r\.p\.m)|shellac|gramophone record", "78 rpm transfer"),
    (r"\b(lp|vinyl|33\s*rpm|ep record|45\s*rpm)\b", "vinyl transfer"),
    (r"\b(cassette|tape)\b", "tape transfer"),
    (r"\b(remaster|digitally restored|restored)\b", "remaster"),
    (r"\b(cd|compact disc)\b", "CD source"),
    (r"\b(radio|broadcast|vividh bharati|ceylon)\b", "radio recording"),
]


# ---------------------------------------------------------------- search

def search(args):
    gems = json.load(open(args.gems, encoding="utf-8"))
    if isinstance(gems, dict):  # a ledger
        gems = [t for t in gems["tags"] if not t.get("untagged_seen_at")]
    before = _before(args.before or [])
    http = Http(args.rps)
    meta_cache: dict[str, dict | None] = {}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cand_rows, best_rows = [], []
    for n, g in enumerate(gems, 1):
        year = g.get("year")
        b = before.get((g.get("maestro"), str(g["unit"])), {})
        row = {"maestro": g.get("maestro", ""), "unit": g["unit"], "year": year or "", "title": g["title"],
               "film": g.get("container") or "", "before": b.get("status", ""),
               "before_identifier": b.get("identifier", ""), "before_file": b.get("file", "")}
        if not year or int(year) > LAST_PD_YEAR:
            row.update(status="after 1965: no legal free source",
                       gap="your own copy (CD, purchased download, Carvaan); analyse locally, keep features only")
            best_rows.append(row)
            continue
        cands = _ia_candidates(http, meta_cache, g) + _commons_candidates(http, g)
        cands.sort(key=_rank, reverse=True)
        if args.measure:
            for c in cands[: args.measure_top]:
                c.update(_measure(http, c["download"]))
        cands.sort(key=_rank, reverse=True)
        for c in cands:
            cand_rows.append({"maestro": row["maestro"], "unit": g["unit"], **c})
        if cands:
            c = cands[0]
            row.update(status="found", **{k: c.get(k, "") for k in BEST_FIELDS}, n_candidates=len(cands))
        else:
            row.update(status="no legal source found", n_candidates=0,
                       gap="your own copy (78/LP/CD); or a collector's upload to the Internet Archive")
        best_rows.append(row)
        print(f"{n}/{len(gems)} {row['maestro']} {g['unit']}: {row['status']}"
              f" ({len(cands)} candidates)", file=sys.stderr)
    _write(out / "candidates.csv", cand_rows)
    _write(out / "best.csv", best_rows)
    summary = _summary(best_rows)
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


BEST_FIELDS = ["source", "identifier", "file", "download", "page", "match_title", "sim", "singer_named",
               "format", "lossless", "kbps", "length_s", "transfer", "license", "quality",
               "bandwidth_khz", "noise_floor_db", "clipped_pct", "quality_note"]


def _before(paths):
    out = {}
    for p in paths:
        maestro = None
        for part in Path(p).parts:  # lata-audio... -> lata
            for m in NAMES:
                if part.lower().startswith(m):
                    maestro = m
        for r in csv.DictReader(open(p, encoding="utf-8")):
            out[(r.get("maestro") or maestro, r["unit"])] = r
    return out


def _queries(g):
    raw = [w for w in re.sub(r"[^A-Za-z ]+", " ", g["title"]).split() if len(w) > 1]
    # the canon spells as HFGK does (Kabhee, Hamane); uploads use common spellings (Kabhi, Hamne),
    # so long words are searched fuzzily (Lucene word~1)
    fuzzy = [w.lower() + "~1" if len(w) >= 4 else w.lower() for w in raw[:4]]
    film = (g.get("container") or "").strip()
    short = NAMES.get(g.get("maestro"), [g.get("maestro", "")])[-1]
    qs = []
    if len(raw) >= 2:
        qs.append(" ".join(raw[:4]))
        qs.append(" AND ".join(fuzzy))
    if film and raw:
        qs.append(f'{" ".join(raw[:3])} "{film}"')
    if film:
        qs.append(f'"{film}" {short}')
    return list(dict.fromkeys(qs))


def _ia_candidates(http, cache, g):
    k = norm(g["title"])
    names = NAMES.get(g.get("maestro"), [])
    found = {}
    for q in _queries(g):
        url = IA_SEARCH + "?" + urllib.parse.urlencode(
            {"q": f"({q}) AND mediatype:(audio)", "fl[]": ["identifier"], "rows": 20, "output": "json"}, doseq=True)
        try:
            docs = json.loads(http.get(url))["response"]["docs"]
        except Exception as e:  # noqa: BLE001
            print(f"  search failed ({q}): {e}", file=sys.stderr)
            continue
        for d in docs:
            ident = d["identifier"]
            if ident not in cache:
                try:
                    cache[ident] = json.loads(http.get(f"https://archive.org/metadata/{ident}"))
                except Exception:  # noqa: BLE001
                    cache[ident] = None
            meta = cache[ident]
            if not meta:
                continue
            m = meta.get("metadata", {})
            text = " ".join(_s(m.get(f)) for f in ("title", "description", "subject", "notes", "creator")).lower()
            files = [f for f in meta.get("files", []) if f.get("format") in LOSSLESS | LOSSY]
            item_title = _s(m.get("title"))
            for f in files:
                title = f.get("title") or (item_title if len(files) <= 3 else "") or Path(f["name"]).stem
                s = sim(k, norm(str(title)))
                if s < 0.72:
                    continue
                ftext = " ".join(_s(f.get(x)) for x in ("title", "artist", "creator", "name")).lower()
                length = _seconds(f.get("length"))
                size = int(f.get("size") or 0)
                kbps = round(size * 8 / length / 1000) if length and size else ""
                key = (ident, f["name"])
                c = {"source": "Internet Archive", "identifier": ident, "file": f["name"],
                     "download": f"https://archive.org/download/{ident}/{urllib.parse.quote(f['name'])}",
                     "page": f"https://archive.org/details/{ident}", "match_title": str(title)[:120],
                     "sim": round(s, 3), "singer_named": any(nm in ftext or nm in text for nm in names),
                     "format": f.get("format"), "lossless": f.get("format") in LOSSLESS,
                     "original": f.get("source") == "original", "kbps": kbps,
                     "length_s": round(length) if length else "", "transfer": _transfer(text),
                     "item_year": _year(m.get("year") or m.get("date")) or "",
                     "license": _s(m.get("licenseurl")) or "none stated (public domain by date)",
                     "collection": ",".join(m.get("collection", []) if isinstance(m.get("collection"), list)
                                            else [_s(m.get("collection"))])}
                c["quality"] = _quality(c)
                if key not in found or _rank(c) > _rank(found[key]):
                    found[key] = c
    # an item's derivatives (VBR MP3 made from a FLAC) are the same recording: keep its best file
    by_rec = {}
    for c in found.values():
        rk = (c["identifier"], Path(c["file"]).stem)
        if rk not in by_rec or _rank(c) > _rank(by_rec[rk]):
            by_rec[rk] = c
    return list(by_rec.values())


def _commons_candidates(http, g):
    k = norm(g["title"])
    raw = [w for w in re.sub(r"[^A-Za-z ]+", " ", g["title"]).split() if len(w) > 1][:4]
    if len(raw) < 2:
        return []
    url = COMMONS + "?" + urllib.parse.urlencode(
        {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": 10,
         "gsrsearch": f'{" ".join(raw)} filetype:audio', "prop": "imageinfo",
         "iiprop": "url|size|mime|extmetadata"})
    try:
        pages = json.loads(http.get(url)).get("query", {}).get("pages", {})
    except Exception as e:  # noqa: BLE001
        print(f"  commons failed: {e}", file=sys.stderr)
        return []
    out = []
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        title = p["title"].split(":", 1)[-1].rsplit(".", 1)[0]
        s = sim(k, norm(title))
        if s < 0.72:
            continue
        ext = ii.get("extmetadata", {})
        mime = ii.get("mime", "")
        c = {"source": "Wikimedia Commons", "identifier": p["title"], "file": p["title"], "download": ii.get("url", ""),
             "page": ii.get("descriptionurl", ""), "match_title": title, "sim": round(s, 3),
             "singer_named": any(nm in json.dumps(ext).lower() for nm in NAMES.get(g.get("maestro"), [])),
             "format": mime, "lossless": mime in ("audio/flac", "audio/x-flac", "audio/wav"), "original": True,
             "kbps": "", "length_s": "", "transfer": "", "item_year": "",
             "license": _s(ext.get("LicenseShortName", {}).get("value")), "collection": ""}
        c["quality"] = _quality(c)
        out.append(c)
    return out


def _quality(c):
    """A rough score from metadata (0-100); --measure refines it from the audio itself."""
    q = 40 if c["lossless"] else min(35, int(c["kbps"] or 96) // 8)
    q += {"remaster": 15, "CD source": 12, "vinyl transfer": 10, "78 rpm transfer": 6, "tape transfer": 2,
          "radio recording": 0}.get(c["transfer"], 4)
    q += 10 if c["singer_named"] else 0
    q += 5 if c.get("original") else 0
    return q


def _rank(c):
    # title match first (wrong song is worthless), then measured quality, then metadata quality
    measured = c.get("measured_q")
    return (c["sim"] >= 0.85, c["singer_named"], measured if measured is not None else -1, c["quality"], c["sim"])


def _transfer(text):
    for pat, label in TRANSFER:
        if re.search(pat, text):
            return label
    return ""


def _s(v):
    return " ".join(map(str, v)) if isinstance(v, list) else str(v or "")


def _seconds(v):
    if not v:
        return None
    try:
        if ":" in str(v):
            parts = [float(x) for x in str(v).split(":")]
            return sum(x * 60 ** i for i, x in enumerate(reversed(parts)))
        return float(v)
    except ValueError:
        return None


# ---------------------------------------------------------------- measure

def _measure(http, url):
    """Fetch to a temp file, measure, delete. Bandwidth shows MP3 low-pass or a dull transfer;
    the noise floor shows surface hiss; clipping shows a harsh remaster."""
    import numpy as np

    try:
        import librosa
    except ImportError:
        return {"quality_note": "librosa missing: not measured"}
    fd, tmp = tempfile.mkstemp(suffix=Path(urllib.parse.urlsplit(url).path).suffix or ".audio")
    try:
        with open(fd, "wb") as fh:
            fh.write(http.get(url))
        y, sr = librosa.load(tmp, sr=None, mono=True, duration=240)
    except Exception as e:  # noqa: BLE001
        _discard(tmp)
        return {"quality_note": f"not measured: {e}"[:120]}
    _discard(tmp)  # never keep the audio
    spec = np.abs(librosa.stft(y, n_fft=4096)) ** 2
    mean = spec.mean(axis=1) + 1e-20
    db = 10 * np.log10(mean / mean.max())
    freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
    bw = float(freqs[np.nonzero(db > -60)[0].max()]) / 1000
    rms = librosa.feature.rms(y=y)[0] + 1e-9
    floor = float(20 * np.log10(np.percentile(rms, 5) / np.percentile(rms, 95)))
    clipped = float((np.abs(y) > 0.99).mean() * 100)
    mq = min(40, bw * 3) + min(40, -floor) + (20 if clipped < 0.05 else 10 if clipped < 0.5 else 0)
    notes = []
    notes.append("narrow (≤6 kHz), typical of a worn 78" if bw <= 6 else
                 "MP3 low-pass or dull transfer" if bw <= 11 else "full bandwidth")
    notes.append("heavy surface noise" if floor > -20 else "some hiss" if floor > -30 else "quiet background")
    if clipped >= 0.5:
        notes.append("clipped")
    return {"bandwidth_khz": round(bw, 1), "noise_floor_db": round(floor, 1), "clipped_pct": round(clipped, 3),
            "measured_q": round(mq), "quality_note": "; ".join(notes)}


# ---------------------------------------------------------------- report

def _summary(rows):
    pd = [r for r in rows if r["year"] and int(r["year"]) <= LAST_PD_YEAR]
    had = lambda r: r["before"] in ("already matched", "found")  # noqa: E731
    by = collections.defaultdict(collections.Counter)
    for r in rows:
        m = by[r["maestro"]]
        m["gems"] += 1
        if r in pd:
            m["public_domain"] += 1
            m["had_recording_before"] += had(r)
            m["has_candidate_now"] += r["status"] == "found"
            m["new"] += r["status"] == "found" and not had(r)
            m["lossless"] += r.get("lossless") is True
            m["better_than_before"] += bool(had(r) and r.get("identifier") and
                                            (r["identifier"], r["file"]) != (r["before_identifier"], r["before_file"]))
        else:
            m["after_1965"] += 1
    return {k: dict(v) for k, v in by.items()}


def _write(path, rows):
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, keys)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("--gems", required=True, help="JSON list of gems, or the gem-tags ledger")
    s.add_argument("--out", required=True)
    s.add_argument("--before", nargs="*", help="earlier find-gems gems.csv files")
    s.add_argument("--measure", action="store_true", help="fetch, measure and delete the top candidates")
    s.add_argument("--measure-top", type=int, default=3)
    s.add_argument("--rps", type=float, default=1.0)
    args = ap.parse_args()
    search(args)


if __name__ == "__main__":
    main()
