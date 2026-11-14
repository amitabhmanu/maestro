"""Pilot: do audio features, measured against Rafi's own norm, find his rare gems?

Three steps, run on a machine that can reach archive.org:

    python maestros/rafi/pilots/audio_gems.py discover SNAPSHOT.json --out DIR
    python maestros/rafi/pilots/audio_gems.py extract  SNAPSHOT.json --out DIR [--max 300]
    python maestros/rafi/pilots/audio_gems.py evaluate SNAPSHOT.json --out DIR

Legal posture (CLAUDE.md, Amitabh's approval 14 Nov 2026): only recordings published in 1965 or
earlier (public domain in India), from the Internet Archive. Each file is written to a temporary
file, analysed and deleted at once; only derived features are kept (``features.csv``). No audio,
no lyrics. robots.txt is honoured and requests are spaced (``--rps``, default 1 a second).

Needs ``librosa`` (and ``rapidfuzz`` optionally): ``uv pip install librosa``.
"""

from __future__ import annotations

import argparse
import collections
import csv
import difflib
import json
import math
import os
import random
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path

RAFI = 6  # person id of Mohammed Rafi in the snapshot
LAST_PD_YEAR = 1965  # India: sound recordings enter the public domain 60 years after publication
UA = "maestro-crawler/0.1 (+https://github.com/amitabhmanu/maestro)"
AUDIO_FORMATS = ("VBR MP3", "MP3", "128Kbps MP3", "64Kbps MP3", "Ogg Vorbis", "Flac", "FLAC")


# ---------------------------------------------------------------- polite HTTP

class Http:
    def __init__(self, rps: float):
        self.gap = 1.0 / rps
        self.last = 0.0
        self.robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _raw(self, url: str) -> bytes:
        wait = self.gap - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code in (429, 503) and attempt < 3:
                    time.sleep(10 * 2 ** attempt)
                    continue
                raise
        raise RuntimeError("unreachable")

    def allowed(self, url: str) -> bool:
        p = urllib.parse.urlsplit(url)
        root = f"{p.scheme}://{p.netloc}"
        if root not in self.robots:
            try:
                rp = urllib.robotparser.RobotFileParser()
                rp.parse(self._raw(root + "/robots.txt").decode("utf-8", "replace").splitlines())
                self.robots[root] = rp
            except urllib.error.HTTPError:
                self.robots[root] = None
        rp = self.robots[root]
        return rp is None or rp.can_fetch(UA, url)

    def get(self, url: str) -> bytes:
        if not self.allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        return self._raw(url)


# ---------------------------------------------------------------- canon

def load_canon(path):
    raw = Path(path).read_bytes()
    if str(path).endswith(".gz"):
        import gzip

        raw = gzip.decompress(raw)
    d = json.loads(raw.decode("utf-8"))
    return d["units"], d["containers"], d["people"]


def group_of(u):
    pop = u.get("pop", {})
    fac = set(pop.get("facets", {}))
    sig = {e["signal"] for e in pop.get("evidence", [])}
    if pop.get("gem"):
        return "gem"
    if sig & {"songsofyore", "expert_posts"}:
        return "expert"
    if "contemporary" in fac:
        return "hit"
    if fac & {"enduring"}:
        return "known"
    return "ordinary"


def norm(s: str) -> str:
    """Rough skeleton of a romanised Hindi/Urdu title, so that spelling variants meet."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]+", " ", s)
    for a, b in (("ee", "i"), ("oo", "u"), ("aa", "a"), ("ii", "i"), ("w", "v"), ("q", "k"),
                 ("z", "j"), ("ph", "f"), ("kh", "k"), ("gh", "g"), ("th", "t"), ("dh", "d"),
                 ("bh", "b"), ("chh", "ch"), ("sh", "s"), ("y", "i"), ("n ", " "), ("ain", "ai"),
                 ("ein", "ei"), ("en ", "e "), ("on ", "o ")):
        s = s.replace(a, b)
    s = re.sub(r"([a-z])\1+", r"\1", s)
    s = re.sub(r"([aeiou])h\b", r"\1", s)
    return " ".join(s.split()[:6])


def sim(a: str, b: str) -> float:
    """Similarity over the words both titles have (one may be the whole first line)."""
    n = min(len(a.split()), len(b.split()))
    if n < 3:
        return 0.0
    a, b = " ".join(a.split()[:n]), " ".join(b.split()[:n])
    return difflib.SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------- discover

SEARCH = "https://archive.org/advancedsearch.php"
QUERY = ('(creator:(rafi) OR title:(rafi) OR description:("rafi")) AND mediatype:(audio)')


def discover(args):
    units, containers, people = load_canon(args.snapshot)
    http = Http(args.rps)
    cand = [u for u in units if u["tier"] == "A" and u.get("year") and u["year"] <= LAST_PD_YEAR]
    index = collections.defaultdict(list)
    for u in cand:
        k = norm(u["title"])
        index[k[:3]].append((k, u))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    items, page = [], 1
    while True:
        q = urllib.parse.urlencode({"q": QUERY, "fl[]": ["identifier", "title", "date", "year", "creator"],
                                    "rows": 500, "page": page, "output": "json"}, doseq=True)
        docs = json.loads(http.get(f"{SEARCH}?{q}"))["response"]["docs"]
        items += docs
        if len(docs) < 500:
            break
        page += 1
    print(f"{len(items)} archive.org items mention Rafi", file=sys.stderr)

    rows = []
    for n, it in enumerate(items, 1):
        year = _year(it.get("year") or it.get("date"))
        if year and year > LAST_PD_YEAR:
            continue
        try:
            meta = json.loads(http.get(f"https://archive.org/metadata/{it['identifier']}"))
        except Exception as e:  # noqa: BLE001
            print(f"skip {it['identifier']}: {e}", file=sys.stderr)
            continue
        files = [f for f in meta.get("files", []) if f.get("format") in AUDIO_FORMATS]
        best_by_track = {}
        for f in files:
            title = f.get("title") or (it.get("title") if len(files) <= 3 else None) or Path(f["name"]).stem
            k = norm(title)
            if len(k) < 6:
                continue
            for uk, u in index.get(k[:3], []):
                s = sim(k, uk)
                if s < args.min_sim:
                    continue
                if year and abs(year - u["year"]) > 2:
                    continue
                key = (u["id"], it["identifier"])
                prev = best_by_track.get(key)
                rank = (s, _fmt_rank(f.get("format")))
                if prev is None or rank > prev[0]:
                    best_by_track[key] = (rank, f, title)
        for (uid, ident), ((s, _), f, title) in best_by_track.items():
            rows.append({"unit": uid, "identifier": ident, "file": f["name"], "format": f.get("format"),
                         "archive_title": title, "archive_year": year or "", "sim": round(s, 3)})
        if n % 50 == 0:
            print(f"{n}/{len(items)} items, {len(rows)} matches", file=sys.stderr)

    # one recording per unit: the closest title match
    best = {}
    for r in rows:
        if r["unit"] not in best or r["sim"] > best[r["unit"]]["sim"]:
            best[r["unit"]] = r
    byid = {u["id"]: u for u in units}
    with open(out / "matches.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["unit", "group", "year", "canon_title", "identifier", "file", "format",
                                "archive_title", "archive_year", "sim"])
        w.writeheader()
        for r in sorted(best.values(), key=lambda r: r["unit"]):
            u = byid[r["unit"]]
            w.writerow({**r, "group": group_of(u), "year": u["year"], "canon_title": u["title"]})
    counts = collections.Counter(group_of(byid[u]) for u in best)
    pool = collections.Counter(group_of(u) for u in cand)
    print(json.dumps({"matched_units": len(best), "by_group": counts, "canon_pd_pool": pool}, indent=1))


def _year(v):
    m = re.search(r"(1[89]\d\d|20\d\d)", str(v or ""))
    return int(m.group(1)) if m else None


def _fmt_rank(fmt):
    return {"Flac": 3, "FLAC": 3, "VBR MP3": 2, "Ogg Vorbis": 1}.get(fmt, 0)


# ---------------------------------------------------------------- extract

FEATURES = [
    "voiced_fraction", "pitch_range_st", "top_note_midi", "median_note_midi", "leap_rate",
    "mean_leap_st", "interval_entropy", "pitch_class_entropy", "ornament_cents", "note_rate",
    "tempo_bpm", "onset_rate", "beat_irregularity", "pulse_clarity", "spectral_contrast",
    "spectral_flatness", "chroma_polyphony", "dynamic_range_db", "section_changes", "duration_s",
]


def analyse(path: str) -> dict:
    import librosa
    import numpy as np

    y, sr = librosa.load(path, sr=22050, mono=True, duration=300)
    y, _ = librosa.effects.trim(y, top_db=35)
    dur = len(y) / sr
    hop = 512
    harm, perc = librosa.effects.hpss(y)
    # melody: pYIN on the harmonic part, limited to a male vocal range (G2..A5)
    f0, voiced, prob = librosa.pyin(harm, fmin=98, fmax=880, sr=sr, frame_length=2048, hop_length=hop)
    v = voiced & (prob > 0.5) & ~np.isnan(f0)
    midi = librosa.hz_to_midi(f0[v]) if v.any() else np.array([])
    feats = {"duration_s": dur, "voiced_fraction": float(v.mean())}
    if len(midi) > 50:
        p5, p50, p95, p98 = np.percentile(midi, [5, 50, 95, 98])
        feats.update(pitch_range_st=p95 - p5, top_note_midi=p98, median_note_midi=p50)
        # notes: runs of frames that stay within half a semitone
        q = np.round(midi)
        notes, cents = [], []
        start = 0
        for i in range(1, len(q) + 1):
            if i == len(q) or q[i] != q[start]:
                if i - start >= 3:  # >= ~70 ms
                    notes.append(q[start])
                    cents.append(np.std((midi[start:i] - q[start]) * 100))
                start = i
        notes = np.array(notes)
        iv = np.diff(notes) if len(notes) > 1 else np.array([0])
        iv = iv[iv != 0] if (iv != 0).any() else iv
        h = np.bincount(np.clip(np.abs(iv).astype(int), 0, 12), minlength=13).astype(float)
        h /= h.sum()
        pc = np.bincount((notes % 12).astype(int), minlength=12).astype(float)
        pc /= pc.sum()
        ent = lambda p: float(-(p[p > 0] * np.log2(p[p > 0])).sum())
        feats.update(
            leap_rate=float((np.abs(iv) >= 5).mean()), mean_leap_st=float(np.abs(iv).mean()),
            interval_entropy=ent(h), pitch_class_entropy=ent(pc),
            ornament_cents=float(np.median(cents)) if cents else math.nan,
            note_rate=len(notes) / (v.sum() * hop / sr),
        )
    onset_env = librosa.onset.onset_strength(y=perc, sr=sr, hop_length=hop)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=hop)
    bt = librosa.frames_to_time(beats, sr=sr, hop_length=hop)
    ibi = np.diff(bt)
    ac = librosa.autocorrelate(onset_env - onset_env.mean())
    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=hop)
    S = np.abs(librosa.stft(y, hop_length=hop))
    chroma = librosa.feature.chroma_stft(S=S**2, sr=sr)
    rms = librosa.feature.rms(S=S)[0]
    mfcc = librosa.feature.mfcc(S=librosa.power_to_db(librosa.feature.melspectrogram(S=S**2, sr=sr)), n_mfcc=13)
    # coarse structure: boundaries in a 2-second-pooled MFCC sequence
    pooled = librosa.util.sync(mfcc, np.arange(0, mfcc.shape[1], int(2 * sr / hop)))
    k = max(2, min(12, pooled.shape[1] // 4))
    bounds = librosa.segment.agglomerative(pooled, k) if pooled.shape[1] > k else []
    feats.update(
        tempo_bpm=float(np.atleast_1d(tempo)[0]),
        onset_rate=len(onsets) / max(dur, 1),
        beat_irregularity=float(ibi.std() / ibi.mean()) if len(ibi) > 4 else math.nan,
        pulse_clarity=float(ac[1:].max() / ac[0]) if ac[0] > 0 else math.nan,
        spectral_contrast=float(librosa.feature.spectral_contrast(S=S, sr=sr).mean()),
        spectral_flatness=float(librosa.feature.spectral_flatness(S=S).mean()),
        chroma_polyphony=float((chroma > 0.5).sum(axis=0).mean()),
        dynamic_range_db=float(20 * np.log10(np.percentile(rms, 95) / max(np.percentile(rms, 5), 1e-6))),
        section_changes=float(len(bounds)),
    )
    return feats


def extract(args):
    units, _, _ = load_canon(args.snapshot)
    byid = {u["id"]: u for u in units}
    out = Path(args.out)
    matches = list(csv.DictReader(open(out / "matches.csv", encoding="utf-8")))
    matches = [m for m in matches if int(m["year"]) <= LAST_PD_YEAR]
    # every gem and expert-listed song, then hits and ordinary songs up to --max
    rng = random.Random(14)
    pick = [m for m in matches if m["group"] in ("gem", "expert")]
    for g, cap in (("hit", 80), ("known", 40)):
        pool = [m for m in matches if m["group"] == g]
        pick += rng.sample(pool, min(cap, len(pool)))
    rest = [m for m in matches if m["group"] == "ordinary"]
    pick += rng.sample(rest, max(0, min(len(rest), args.max - len(pick))))
    done_path = out / "features.csv"
    done = set()
    if done_path.exists():
        done = {r["unit"] for r in csv.DictReader(open(done_path, encoding="utf-8"))}
    from concurrent.futures import ProcessPoolExecutor, as_completed

    http = Http(args.rps)
    new = not done_path.exists()
    todo = [m for m in pick if m["unit"] not in done]
    print(f"{len(pick)} picked, {len(todo)} to analyse", file=sys.stderr)
    with open(done_path, "a", newline="", encoding="utf-8") as fh, \
            ProcessPoolExecutor(max_workers=args.workers) as pool:
        w = csv.DictWriter(fh, ["unit", "group", "year", "identifier", "file", *FEATURES])
        if new:
            w.writeheader()
        running = {}

        def drain(block):
            for fut in list(as_completed(running) if block else [f for f in running if f.done()]):
                m, tmp = running.pop(fut)
                try:
                    feats = fut.result()
                except Exception as e:  # noqa: BLE001
                    print(f"{m['unit']}: {e}", file=sys.stderr)
                    continue
                finally:
                    _discard(tmp)  # a crashed worker never reaches its own delete
                w.writerow({"unit": m["unit"], "group": m["group"], "year": byid[int(m["unit"])]["year"],
                            "identifier": m["identifier"], "file": m["file"],
                            **{k: _r(feats.get(k)) for k in FEATURES}})
                fh.flush()
                print(f"done {m['group']} {m['canon_title'][:40]}", file=sys.stderr)

        try:
            for m in todo:
                while len(running) >= args.workers * 2:
                    time.sleep(1)
                    drain(False)
                url = f"https://archive.org/download/{m['identifier']}/{urllib.parse.quote(m['file'])}"
                fd, tmp = tempfile.mkstemp(suffix=Path(m["file"]).suffix)
                try:
                    with os.fdopen(fd, "wb") as t:
                        t.write(http.get(url))
                except Exception as e:  # noqa: BLE001
                    os.remove(tmp)
                    print(f"{m['unit']}: {e}", file=sys.stderr)
                    continue
                try:
                    running[pool.submit(_analyse_and_delete, tmp)] = (m, tmp)
                except Exception:  # pool already broken
                    _discard(tmp)
                    raise
                drain(False)
            drain(True)
        finally:
            for _, tmp in running.values():  # pool broke or run interrupted
                _discard(tmp)


def _discard(tmp: str) -> None:
    try:
        os.remove(tmp)
    except FileNotFoundError:
        pass
    except OSError as e:  # e.g. still open on Windows: say so, so it can be removed by hand
        print(f"could not delete temp audio {tmp}: {e}", file=sys.stderr)


def _analyse_and_delete(tmp: str) -> dict:
    try:
        return analyse(tmp)
    finally:
        _discard(tmp)  # never keep the audio


def _r(x):
    return "" if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 4)


# ---------------------------------------------------------------- evaluate

# features where "more" plausibly means more musical craft
CRAFT = ["pitch_range_st", "top_note_midi", "interval_entropy", "pitch_class_entropy", "ornament_cents",
         "leap_rate", "spectral_contrast", "chroma_polyphony", "dynamic_range_db", "section_changes"]


def auc(pos, neg):
    if len(pos) < 3 or not neg:
        return None
    return sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg))


def matched_auc(pos, neg):
    """AUC within decade strata, pooled (removes era and recording-quality drift)."""
    by = collections.defaultdict(lambda: ([], []))
    for d, v in pos:
        by[d][0].append(v)
    for d, v in neg:
        by[d][1].append(v)
    num = den = 0.0
    for p, q in by.values():
        if p and q:
            a = sum((x > y) + 0.5 * (x == y) for x in p for y in q) / (len(p) * len(q))
            num += a * len(p) * len(q)
            den += len(p) * len(q)
    return num / den if den else None


def evaluate(args):
    import statistics as st

    units, _, _ = load_canon(args.snapshot)
    byid = {u["id"]: u for u in units}
    rows = list(csv.DictReader(open(Path(args.out) / "features.csv", encoding="utf-8")))
    for r in rows:
        for k in FEATURES:
            r[k] = float(r[k]) if r[k] != "" else None
    # z-scores against Rafi's own norm (all analysed songs)
    for k in FEATURES:
        xs = [r[k] for r in rows if r[k] is not None]
        mu, sd = st.mean(xs), (st.pstdev(xs) or 1)
        for r in rows:
            r["z_" + k] = (r[k] - mu) / sd if r[k] is not None else None
    # ... and within each composer's own songs in the sample
    comp = lambda r: ((byid[int(r["unit"])]["credits"].get("composer") or [None])[0])
    groups = collections.defaultdict(list)
    for r in rows:
        groups[comp(r)].append(r)
    for r in rows:
        zs = [abs(r["z_" + k]) for k in FEATURES if r["z_" + k] is not None]
        r["atypicality"] = sum(zs) / len(zs) if zs else None
        cz = [r["z_" + k] for k in CRAFT if r["z_" + k] is not None]
        r["craft"] = sum(cz) / len(cz) if cz else None
    for r in rows:
        peers = [p["craft"] for p in groups[comp(r)] if p is not r and p.get("craft") is not None]
        r["craft_vs_composer"] = (r["craft"] - st.mean(peers)) if len(peers) >= 4 and r["craft"] is not None else None

    scores = FEATURES + ["atypicality", "craft", "craft_vs_composer"]
    low = ("ordinary",)
    comparisons = {
        "gems vs ordinary unpopular songs": (["gem"], low),
        "expert-listed (any) vs ordinary": (["gem", "expert"], low),
        "hits vs ordinary (sanity check)": (["hit"], low),
    }
    decade = lambda r: int(r["year"]) // 10 * 10
    key = lambda k: (k if k in ("atypicality", "craft", "craft_vs_composer") else "z_" + k)
    lines = ["# Audio gem pilot: Rafi, recordings published by 1965", "",
             f"{len(rows)} recordings analysed: " + ", ".join(
                 f"{g} {c}" for g, c in collections.Counter(r['group'] for r in rows).most_common()) + ".",
             "AUC 0.5 = chance. Matched = compared within the same decade."]
    for title, (pg, ng) in comparisons.items():
        P = [r for r in rows if r["group"] in pg]
        N = [r for r in rows if r["group"] in ng]
        lines += ["", f"## {title} ({len(P)} vs {len(N)})", "", "| score | AUC | matched AUC |", "|---|---|---|"]
        for s in scores:
            p = [(decade(r), r[key(s)]) for r in P if r[key(s)] is not None]
            q = [(decade(r), r[key(s)]) for r in N if r[key(s)] is not None]
            a = auc([v for _, v in p], [v for _, v in q])
            if a is None:
                continue
            m = matched_auc(p, q)
            lines.append(f"| {s} | {a:.2f} | {'' if m is None else f'{m:.2f}'} |")
    cand = sorted((r for r in rows if r["group"] == "ordinary" and r["craft"] is not None),
                  key=lambda r: -r["craft"])[:25]
    lines += ["", "## Ordinary unpopular songs with the highest craft score (for listening)", "",
              "| unit | year | title | craft | vs composer |", "|---|---|---|---|---|"]
    for r in cand:
        u = byid[int(r["unit"])]
        vc = r["craft_vs_composer"]
        lines.append(f"| {u['id']} | {u['year']} | {u['title'][:60]} | {r['craft']:.2f} | "
                     f"{'' if vc is None else f'{vc:.2f}'} |")
    text = "\n".join(lines) + "\n"
    (Path(args.out) / "audio-report.md").write_text(text, encoding="utf-8")
    print(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["discover", "extract", "evaluate"])
    ap.add_argument("snapshot")
    ap.add_argument("--out", default="data/pilots/audio")
    ap.add_argument("--rps", type=float, default=1.0)
    ap.add_argument("--min-sim", type=float, default=0.82)
    ap.add_argument("--max", type=int, default=300)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = ap.parse_args()
    {"discover": discover, "extract": extract, "evaluate": evaluate}[args.step](args)


if __name__ == "__main__":
    main()
