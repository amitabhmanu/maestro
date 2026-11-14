"""Pilot: a map of Rafi's songs built from the audio alone.

Each recording becomes a vector computed only from its sound by an open, pretrained music model.
No metadata (film, composer, year, popularity) goes into the vectors or the 2D layout. Metadata is
joined afterwards, only to label the map and to test whether neighbours make musical sense.

Two steps:

    # on a machine that can reach archive.org and huggingface.co (Amitabh's PC):
    python maestros/rafi/pilots/audio_map.py embed --matches data/pilots/audio/matches.csv \
        --out data/pilots/audio-map [--models mert,clap] [--max 400]

    # anywhere, from the saved vectors:
    python maestros/rafi/pilots/audio_map.py map --out data/pilots/audio-map \
        --snapshot maestros/rafi/pilots/audio-pilot-canon.json.gz --labels data/explorer/canon/rafi.json \
        [--craft data/pilots/audio/features.csv]

``matches.csv`` comes from ``audio_gems.py discover``: canon songs published by 1965 matched to
Internet Archive recordings. Legal posture as in that pilot (Amitabh's approval, 14 Nov 2026):
recordings published in 1965 or earlier only (public domain in India); each file goes to a temporary
file, is embedded and deleted at once; only the vectors are kept (``emb/<model>/<file key>.npy``).
No audio and no lyrics are stored.

Models (both open, CPU is enough):

- ``mert``: MERT-v1-95M (m-a-p, CC BY-NC 4.0: fine for personal use; would need swapping before a
  FOSS release ships vectors). Self-supervised on music; mean of all 13 layers, 768 dims.
- ``clap``: LAION larger_clap_music (Apache 2.0). Audio tower of an audio-text model; 512 dims.

Needs ``torch``, ``transformers`` (4.57 runs both models; 5.x breaks MERT), ``librosa`` (and ``nnAudio`` for MERT's remote code) for
``embed``; ``numpy``, ``scikit-learn`` and optionally ``umap-learn`` for ``map``.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import math
import os
import random
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audio_gems import LAST_PD_YEAR, RAFI, Http, _discard, group_of, load_canon  # noqa: E402

CHUNK_S = 10.0
MAX_S = 300.0
MODELS = {
    "mert": {"repo": "m-a-p/MERT-v1-95M", "sr": 24000},
    "clap": {"repo": "laion/larger_clap_music", "sr": 48000},
}


def file_key(identifier: str, name: str) -> str:
    return hashlib.sha1(f"{identifier}/{name}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------- embed

class Embedder:
    def __init__(self, name: str):
        import torch

        self.name, self.sr, self.torch = name, MODELS[name]["sr"], torch
        repo = MODELS[name]["repo"]
        if name == "mert":
            from transformers import AutoModel, Wav2Vec2FeatureExtractor

            self.proc = Wav2Vec2FeatureExtractor.from_pretrained(repo, trust_remote_code=True)
            self.model = AutoModel.from_pretrained(repo, trust_remote_code=True)
        else:
            from transformers import ClapModel, ClapProcessor

            self.proc = ClapProcessor.from_pretrained(repo)
            self.model = ClapModel.from_pretrained(repo)
        self.model.eval()

    def __call__(self, chunks):
        """chunks: list of 1-D float arrays at self.sr. Returns a float32 vector (or layers x dims)."""
        import numpy as np

        torch = self.torch
        outs = []
        with torch.inference_mode():
            for i in range(0, len(chunks), 4):
                batch = chunks[i:i + 4]
                if self.name == "mert":
                    inp = self.proc(batch, sampling_rate=self.sr, return_tensors="pt", padding=True)
                    hs = self.model(**inp, output_hidden_states=True).hidden_states
                    outs.append(torch.stack(hs).mean(dim=2).permute(1, 0, 2).numpy())  # B x 13 x 768
                else:
                    try:
                        inp = self.proc(audio=batch, sampling_rate=self.sr, return_tensors="pt")
                    except (TypeError, ValueError):  # older transformers name it audios=
                        inp = self.proc(audios=batch, sampling_rate=self.sr, return_tensors="pt")
                    outs.append(self.model.get_audio_features(**inp).numpy())  # B x 512
        return np.concatenate(outs).mean(axis=0).astype(np.float32)


def chunk(y, sr):
    n = int(CHUNK_S * sr)
    parts = [y[i:i + n] for i in range(0, len(y), n)]
    return [p for p in parts if len(p) >= 3 * sr] or [y]


def embed_file(path, embedders):
    import librosa

    y, sr = librosa.load(path, sr=48000, mono=True, duration=MAX_S)
    y, _ = librosa.effects.trim(y, top_db=35)
    peak = float(abs(y).max()) if len(y) else 0.0
    if peak > 0:
        y = y / peak
    out = {}
    for name, e in embedders.items():
        ys = y if e.sr == 48000 else librosa.resample(y, orig_sr=48000, target_sr=e.sr)
        out[name] = e(chunk(ys, e.sr))
    return out, len(y) / 48000


def embed(args):
    import numpy as np

    out = Path(args.out)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    for m in models:
        (out / "emb" / m).mkdir(parents=True, exist_ok=True)
    matches = [m for m in csv.DictReader(open(args.matches, encoding="utf-8")) if int(m["year"]) <= LAST_PD_YEAR]
    # every expert-listed song first, then the rest in a fixed random order, up to --max files
    rng = random.Random(14)
    rng.shuffle(matches)
    matches.sort(key=lambda m: m["group"] not in ("gem", "expert"))
    files, units_of = [], collections.defaultdict(list)
    for m in matches:
        k = file_key(m["identifier"], m["file"])
        if k not in units_of:
            files.append((k, m))
        units_of[k].append(m["unit"])
    files = files[:args.max]
    with open(out / "files.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["key", "identifier", "file", "units"])
        for k, m in files:
            w.writerow([k, m["identifier"], m["file"], " ".join(units_of[k])])

    todo = [(k, m) for k, m in files if not all((out / "emb" / mm / f"{k}.npy").exists() for mm in models)]
    print(f"{len(files)} recordings, {len(todo)} to embed with {', '.join(models)}", file=sys.stderr)
    if not todo:
        return
    embedders = {m: Embedder(m) for m in models}
    http = Http(args.rps)
    log = open(out / "embed-log.csv", "a", newline="", encoding="utf-8")
    lw = csv.writer(log)
    for n, (k, m) in enumerate(todo, 1):
        url = f"https://archive.org/download/{m['identifier']}/{urllib.parse.quote(m['file'])}"
        fd, tmp = tempfile.mkstemp(suffix=Path(m["file"]).suffix)
        t0 = time.time()
        try:
            with os.fdopen(fd, "wb") as t:
                t.write(http.get(url))
            vecs, dur = embed_file(tmp, embedders)
        except Exception as e:  # noqa: BLE001
            print(f"{m['unit']}: {e}", file=sys.stderr)
            lw.writerow([k, "error", str(e)[:200]])
            continue
        finally:
            _discard(tmp)  # never keep the audio
        for name, v in vecs.items():
            np.save(out / "emb" / name / f"{k}.npy", v.astype(np.float16))
        lw.writerow([k, "ok", round(dur, 1)])
        log.flush()
        print(f"[{n}/{len(todo)}] {time.time() - t0:.0f}s {m['group']} {m['canon_title'][:40]}", file=sys.stderr)


# ---------------------------------------------------------------- map

def load_views(out: Path, craft_csv: str | None):
    """Return {view: (keys, matrix)}; each row L2-normalised. Views are audio-only."""
    import numpy as np

    views = {}
    files = {r["key"]: r for r in csv.DictReader(open(out / "files.csv", encoding="utf-8"))}
    for name in MODELS:
        d = out / "emb" / name
        if not d.exists():
            continue
        keys = sorted(p.stem for p in d.glob("*.npy") if p.stem in files)
        if not keys:
            continue
        X = np.stack([np.load(d / f"{k}.npy").astype(np.float32) for k in keys])
        if X.ndim == 3:  # MERT: layers x dims. Fixed in advance: mean of all layers, plus a few single layers
            for layer in (3, 6, 9, 12):
                views[f"{name}_L{layer}"] = (keys, X[:, layer])
            X = X.mean(axis=1)
        views[name] = (keys, X)
    if craft_csv and Path(craft_csv).exists():
        from audio_gems import FEATURES

        rows = list(csv.DictReader(open(craft_csv, encoding="utf-8")))
        byfile = {file_key(r["identifier"], r["file"]): r for r in rows}
        keys = sorted(k for k in byfile if k in files)
        X = np.array([[float(byfile[k][f]) if byfile[k][f] != "" else np.nan for f in FEATURES if f != "duration_s"]
                      for k in keys])
        X = np.where(np.isnan(X), np.nanmean(X, axis=0), X)
        views["craft"] = (keys, X)
    for v, (keys, X) in list(views.items()):
        X = X - X.mean(axis=0)
        if v == "craft":
            X = X / (X.std(axis=0) + 1e-9)
        X = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)
        views[v] = (keys, X)
    return views, files


def labels_for(files, snapshot, labels_path):
    units, _, _ = load_canon(snapshot)
    pilot = {str(u["id"]): u for u in units}
    full = json.load(open(labels_path, encoding="utf-8")) if labels_path else {"units": [], "containers": [], "people": []}
    fu = {str(u["id"]): u for u in full["units"]}
    by_id = lambda xs: ({str(x["id"]): x for x in xs} if isinstance(xs, list) else {str(i): x for i, x in xs.items()})
    cont, ppl = by_id(full["containers"]), by_id(full["people"])
    pname = lambda i: (ppl.get(str(i)) or {}).get("name") or str(i)
    lab = {}
    for k, f in files.items():
        uid = f["units"].split()[0]
        u = fu.get(uid) or pilot.get(uid) or {}
        cr = u.get("credits", {})
        singers = cr.get("singer", [])
        c = cont.get(str(u.get("container"))) or {}
        lab[k] = {
            "unit": int(uid), "units": [int(x) for x in f["units"].split()],
            "title": u.get("title", ""), "year": u.get("year"),
            "decade": (u["year"] // 10 * 10) if u.get("year") else None,
            "film": c.get("title") or c.get("name"), "film_id": u.get("container"),
            "composer": pname(cr["composer"][0]) if cr.get("composer") else None,
            "lyricist": pname(cr["lyricist"][0]) if cr.get("lyricist") else None,
            "cosingers": [pname(s) for s in singers if s != RAFI],
            "solo": singers == [RAFI] if singers else None,
            "group": group_of(pilot[uid]) if uid in pilot else "ordinary",
            "item": f["identifier"],
            "listen": f"https://archive.org/details/{f['identifier']}",
        }
    return lab


def knn(X, k):
    import numpy as np

    S = X @ X.T
    np.fill_diagonal(S, -np.inf)
    idx = np.argsort(-S, axis=1)[:, :k]
    return idx, np.take_along_axis(S, idx, axis=1)


def agreement(idx, labels, exclude=None):
    """Mean share of a song's neighbours with the same label (songs and neighbours without a label skipped)."""
    tot, n = 0.0, 0
    for i, row in enumerate(idx):
        if labels[i] is None:
            continue
        nb = [j for j in row if labels[j] is not None and not (exclude is not None and exclude[j] == exclude[i])]
        if not nb:
            continue
        tot += sum(labels[j] == labels[i] for j in nb) / len(nb)
        n += 1
    return tot / n if n else None


def shuffled(idx, labels, exclude=None, reps=200, seed=14):
    """Agreement expected by chance: labels permuted among songs that have one."""
    rng = random.Random(seed)
    have = [i for i, l in enumerate(labels) if l is not None]
    vals = []
    for _ in range(reps):
        perm = [labels[i] for i in have]
        rng.shuffle(perm)
        lab = list(labels)
        for i, l in zip(have, perm):
            lab[i] = l
        vals.append(agreement(idx, lab, exclude))
    vals.sort()
    return sum(vals) / len(vals), vals[int(0.95 * len(vals))]


def auc(pos, neg):
    if len(pos) < 3 or len(neg) < 3:
        return None
    return sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg))


def project(X, seed=14):
    import warnings

    import numpy as np
    from sklearn.decomposition import PCA

    Z = PCA(n_components=min(50, X.shape[0] - 1, X.shape[1]), random_state=seed).fit_transform(X)
    warnings.filterwarnings("ignore", module="umap")
    try:
        import umap

        Y = umap.UMAP(n_neighbors=15, min_dist=0.15, metric="cosine", random_state=seed).fit_transform(Z)
        how = "umap"
    except ImportError:
        from sklearn.manifold import TSNE

        Y = TSNE(n_components=2, perplexity=20, metric="cosine", random_state=seed, init="pca").fit_transform(Z)
        how = "tsne"
    Y = (Y - Y.min(axis=0)) / (np.ptp(Y, axis=0) + 1e-9)
    return Y, how


TESTS = [  # label, why it should (or should not) be audible
    ("composer", "each composer has an orchestral and melodic style"),
    ("film", "songs of one film share a recording session and orchestra"),
    ("decade", "recording technology and fashion change with time"),
    ("solo", "a second voice (often female) is plainly audible"),
    ("lyricist", "words are barely audible to these models; mostly rides on composer"),
    ("item", "same Internet Archive upload: same transfer, a recording artefact, not music"),
]


def map_(args):
    import numpy as np

    out = Path(args.out)
    views, files = load_views(out, args.craft)
    if not views:
        sys.exit("no embeddings found")
    lab = labels_for(files, args.snapshot, args.labels)
    k = args.k
    report = ["# Audio-only map of Rafi's songs: pilot report", "",
              "Vectors come from the audio alone. Metadata is joined afterwards to test the neighbours.",
              f"Neighbours: the {k} nearest songs by cosine similarity in the full vector space (not the 2D map). "
              "Chance = the same score with labels shuffled among songs (200 times). "
              "'Other uploads' leaves out neighbours from the same Internet Archive item, which share a transfer.", ""]
    summary, maps = {}, {}
    for v, (keys, X) in views.items():
        idx, sims = knn(X, k)
        L = lambda f: [lab[key][f] for key in keys]
        items = L("item")
        rows = {}
        for f, why in TESTS:
            labels = L(f)
            if sum(x is not None for x in labels) < 20:
                continue
            obs = agreement(idx, labels)
            base, p95 = shuffled(idx, labels, reps=args.reps)
            obs_x = agreement(idx, labels, exclude=items) if f != "item" else None
            base_x = shuffled(idx, labels, exclude=items, reps=args.reps)[0] if f != "item" else None
            rows[f] = {"agree": obs, "chance": base, "chance95": p95, "agree_other_uploads": obs_x,
                       "chance_other_uploads": base_x, "why": why}
        years = L("year")
        dy = [abs(years[i] - years[j]) for i in range(len(keys)) for j in idx[i] if years[i] and years[j]]
        rng = random.Random(14)
        yy = [y for y in years if y]
        dy0 = [abs(rng.choice(yy) - rng.choice(yy)) for _ in range(20000)]
        rows["year_gap"] = {"neighbours": float(np.mean(dy)), "random": float(np.mean(dy0))}
        # outliers: mean cosine distance to the k nearest songs
        iso = 1 - sims.mean(axis=1)
        grp = L("group")
        g = lambda names: [iso[i] for i in range(len(keys)) if grp[i] in names]
        rows["outlier_auc"] = {
            "expert_vs_ordinary": auc(g({"gem", "expert"}), g({"ordinary"})),
            "gem_vs_ordinary": auc(g({"gem"}), g({"ordinary"})),
            "hit_vs_ordinary": auc(g({"hit"}), g({"ordinary"})),
            "n": dict(collections.Counter(grp)),
        }
        dup = [(keys[i], keys[j], float(s)) for i in range(len(keys)) for j, s in zip(idx[i], sims[i])
               if s > args.dup and i < j]
        rows["near_duplicates"] = [{"a": lab[a]["unit"], "b": lab[b]["unit"], "sim": round(s, 4)} for a, b, s in dup]
        summary[v] = rows
        if v in ("mert", "clap", "craft"):
            Y, how = project(X)
            maps[v] = {"how": how, "xy": {key: [round(float(a), 4), round(float(b), 4)] for key, (a, b) in zip(keys, Y)},
                       "nn": {key: [[keys[j], round(float(s), 3)] for j, s in zip(idx[i][:8], sims[i][:8])]
                              for i, key in enumerate(keys)},
                       "iso": {key: round(float(iso[i]), 4) for i, key in enumerate(keys)}}

        report += [f"## {v} ({len(keys)} recordings)", "",
                   "| label | neighbours agree | chance | other uploads only | chance |", "|---|---|---|---|---|"]
        for f, _ in TESTS:
            r = rows.get(f)
            if not r:
                continue
            fx = lambda x: "" if x is None else f"{x:.2f}"
            report.append(f"| {f} | {fx(r['agree'])} | {fx(r['chance'])} | {fx(r['agree_other_uploads'])} | "
                          f"{fx(r['chance_other_uploads'])} |")
        o = rows["outlier_auc"]
        fx = lambda x: "n/a" if x is None else f"{x:.2f}"
        report += ["", f"Year gap to neighbours: {rows['year_gap']['neighbours']:.1f} years "
                       f"(random pairs {rows['year_gap']['random']:.1f}).",
                   f"Outlier AUC (isolation, 0.5 = chance): expert-listed vs ordinary {fx(o['expert_vs_ordinary'])}, "
                   f"gems vs ordinary {fx(o['gem_vs_ordinary'])}, hits vs ordinary {fx(o['hit_vs_ordinary'])}. "
                   f"Groups: {o['n']}.",
                   f"Near-duplicates (cosine > {args.dup}): {len(dup)}.", ""]

    text = "\n".join(report) + "\n"
    (out / "map-report.md").write_text(text, encoding="utf-8")
    (out / "map-summary.json").write_text(json.dumps(summary, indent=1, default=float), encoding="utf-8")
    songs = {key: lab[key] for key in files if any(key in m["xy"] for m in maps.values())}
    (out / "map.json").write_text(json.dumps({"songs": songs, "maps": maps, "k": k,
                                              "summary": summary}, default=float), encoding="utf-8")
    print(text)


def page(args):
    """Write a self-contained explorer page from map.json (no audio, publishable titles only)."""
    d = json.load(open(Path(args.out) / "map.json", encoding="utf-8"))
    d["summary"] = {v: r for v, r in d["summary"].items() if v in d["maps"]}
    html = Path(__file__).with_name("audio_map.html").read_text(encoding="utf-8")
    html = html.replace("/*MAP_DATA*/null", json.dumps(d, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    dest = Path(args.page or Path(args.out) / "rafi-sound-map.html")
    dest.write_text(html, encoding="utf-8")
    print(dest)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="step", required=True)
    e = sub.add_parser("embed")
    e.add_argument("--matches", default="data/pilots/audio/matches.csv")
    e.add_argument("--out", default="data/pilots/audio-map")
    e.add_argument("--models", default="mert,clap")
    e.add_argument("--max", type=int, default=400)
    e.add_argument("--rps", type=float, default=1.0)
    m = sub.add_parser("map")
    m.add_argument("--out", default="data/pilots/audio-map")
    m.add_argument("--snapshot", default=str(Path(__file__).with_name("audio-pilot-canon.json.gz")))
    m.add_argument("--labels", help="full canon snapshot (maestro-explore JSON) for film and people names")
    m.add_argument("--craft", default="data/pilots/audio/features.csv", help="the earlier pilot's 20 features")
    m.add_argument("--k", type=int, default=10)
    m.add_argument("--reps", type=int, default=200)
    m.add_argument("--dup", type=float, default=0.985)
    p = sub.add_parser("page")
    p.add_argument("--out", default="data/pilots/audio-map")
    p.add_argument("--page", help="where to write the page (default: OUT/rafi-sound-map.html)")
    args = ap.parse_args()
    {"embed": embed, "map": map_, "page": page}[args.step](args)


if __name__ == "__main__":
    main()
