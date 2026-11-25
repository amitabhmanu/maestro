"""Pilot: a map of a maestro's songs built from the audio alone (Rafi first, then Lata).

Each recording becomes a vector computed only from its sound by an open, pretrained music model.
No song metadata (title, film, composer, year, popularity) is used anywhere: not in the vectors,
not in the layout, and not in judging the map (Amitabh, 14 Nov 2026). The map is judged by
audio-internal evidence instead: whether two unrelated models hear the same neighbours, whether
audio clusters are stable, whether neighbours are merely the same transfer, and blind listening.

Two steps:

    # on a machine that can reach archive.org and huggingface.co (Amitabh's PC):
    python maestros/rafi/pilots/audio_map.py embed --matches data/pilots/audio/matches.csv \
        --out data/pilots/audio-map [--models mert,clap] [--max 400]

    # anywhere, from the saved vectors:
    python maestros/rafi/pilots/audio_map.py map --out data/pilots/audio-map [--craft data/pilots/audio/features.csv]
    python maestros/rafi/pilots/audio_map.py page --out data/pilots/audio-map

Another maestro (Lata, 24 Nov 2026) runs the same steps on her own reduced canon, with
``audio_gems.py discover ... --search lata`` and ``find-gems --maestro lata``; one page then holds
every maestro's map behind a switch:

    python maestros/rafi/pilots/audio_map.py page --map rafi=data/pilots/audio-map \
        --map lata=data/pilots/lata-audio-map --page data/pilots/sound-map.html

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
from audio_gems import LAST_PD_YEAR, Http, _discard  # noqa: E402

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
    # a fixed random order, up to --max files (no song metadata decides which)
    random.Random(14).shuffle(matches)
    files, units_of = [], collections.defaultdict(list)
    for m in matches:
        k = file_key(m["identifier"], m["file"])
        if k not in units_of:
            files.append((k, m))
        units_of[k].append(m["unit"])
    files = files[:args.max]
    # keep recordings embedded by earlier runs (files.csv is the union of every run)
    old = {}
    if (out / "files.csv").exists():
        old = {r["key"]: r for r in csv.DictReader(open(out / "files.csv", encoding="utf-8"))}
    rows = dict(old)
    for k, m in files:
        units = sorted(set((old.get(k, {}).get("units") or "").split()) | set(units_of[k]))
        rows[k] = {"key": k, "identifier": m["identifier"], "file": m["file"], "units": " ".join(units)}
    with open(out / "files.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["key", "identifier", "file", "units"])
        w.writeheader()
        w.writerows(rows.values())

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
        print(f"[{n}/{len(todo)}] {time.time() - t0:.0f}s {k}", file=sys.stderr)


# ---------------------------------------------------------------- map

def load_views(out: Path, craft_csv: str | None, models=tuple(MODELS)):
    """Return {view: (keys, matrix)}; each row L2-normalised. Views are audio-only."""
    import numpy as np

    views = {}
    files = {r["key"]: r for r in csv.DictReader(open(out / "files.csv", encoding="utf-8"))}
    for name in models:
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


def knn(X, k):
    import numpy as np

    S = X @ X.T
    np.fill_diagonal(S, -np.inf)
    idx = np.argsort(-S, axis=1)[:, :k]
    return idx, np.take_along_axis(S, idx, axis=1)


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


def clusters(X, seed=14):
    """Audio-only clusters: k-means on the vectors, k chosen by silhouette, plus bootstrap stability (ARI)."""
    import numpy as np
    from sklearn.cluster import KMeans
    from sklearn.metrics import adjusted_rand_score, silhouette_score

    best = None
    for k in range(3, 11):
        lab = KMeans(k, n_init=10, random_state=seed).fit_predict(X)
        s = silhouette_score(X, lab, metric="cosine")
        if best is None or s > best[1]:
            best = (k, s, lab)
    k, sil, lab = best
    rng = np.random.default_rng(seed)
    aris = []
    for _ in range(20):
        sub = rng.choice(len(X), int(0.8 * len(X)), replace=False)
        lab2 = KMeans(k, n_init=10, random_state=int(rng.integers(1e6))).fit_predict(X[sub])
        aris.append(adjusted_rand_score(lab[sub], lab2))
    return lab, {"k": k, "silhouette": float(sil), "stability_ari": float(np.mean(aris))}


def overlap(a, b):
    """Mean share of a recording's neighbours that two views have in common."""
    return sum(len(set(x) & set(y)) / len(x) for x, y in zip(a, b)) / len(a)


def map_(args):
    """Everything here comes from the audio vectors. No song metadata is read.

    The one outside fact used is which Internet Archive upload a file came from: it is about the
    transfer, not the song, and is used only to warn when neighbours are the same transfer.
    """
    import numpy as np
    from sklearn.manifold import trustworthiness
    from sklearn.metrics import adjusted_rand_score

    out = Path(args.out)
    views, files = load_views(out, args.craft, [m.strip() for m in args.models.split(",") if m.strip()])
    if not views:
        sys.exit("no embeddings found")
    common = sorted(set.intersection(*(set(keys) for keys, _ in views.values())))
    pos = {v: {key: i for i, key in enumerate(keys)} for v, (keys, _) in views.items()}
    views = {v: (common, X[[pos[v][key] for key in common]]) for v, (keys, X) in views.items()}
    n, k = len(common), args.k
    num = {key: i + 1 for i, key in enumerate(sorted(common, key=lambda c: hashlib.sha1(c.encode()).hexdigest()))}
    items = [files[key]["identifier"] for key in common]
    chance = k / (n - 1)

    nn, summary, maps, labs = {}, {}, {}, {}
    for v, (keys, X) in views.items():
        idx, sims = knn(X, k)
        nn[v] = (idx, sims)
        same_upload = float(np.mean([[items[j] == items[i] for j in row] for i, row in enumerate(idx)]))
        perm = np.random.default_rng(14).permutation(n)
        upload_chance = float(np.mean([[items[perm[j]] == items[perm[i]] for j in row] for i, row in enumerate(idx)]))
        sims_all = X @ X.T
        dup = [(i, j, float(sims_all[i, j])) for i in range(n) for j in range(i + 1, n) if sims_all[i, j] > args.dup]
        r = {"same_upload": same_upload, "same_upload_chance": upload_chance, "near_duplicates": len(dup)}
        if v in ("mert", "clap", "craft"):
            Y, how = project(X)
            lab, cl = clusters(X)
            labs[v] = lab
            r.update(cl, trustworthiness=float(trustworthiness(X, Y, n_neighbors=k, metric="cosine")))
            iso = 1 - sims.mean(axis=1)
            from sklearn.decomposition import PCA

            pca = PCA(n_components=min(args.pcs, n - 1, X.shape[1]), random_state=14)
            P = pca.fit_transform(X)
            P = P / (P.std(axis=0) + 1e-9)  # each axis in standard units, so the strips compare
            ends = [{"low": [keys[j] for j in np.argsort(P[:, c])[:3]],
                     "high": [keys[j] for j in np.argsort(-P[:, c])[:3]]} for c in range(P.shape[1])]
            maps[v] = {"how": how,
                       "pc": {key: [round(float(x), 2) for x in P[i]] for i, key in enumerate(keys)},
                       "pcvar": [round(float(x), 4) for x in pca.explained_variance_ratio_],
                       "pcends": ends,
                       "xy": {key: [round(float(a), 4), round(float(b), 4)] for key, (a, b) in zip(keys, Y)},
                       "nn": {key: [[keys[j], round(float(s), 3)] for j, s in zip(idx[i][:8], sims[i][:8])]
                              for i, key in enumerate(keys)},
                       "iso": {key: round(float(iso[i]), 4) for i, key in enumerate(keys)},
                       "cluster": {key: int(lab[i]) for i, key in enumerate(keys)}}
        summary[v] = r

    names = [v for v in views if v in ("mert", "clap", "craft")]
    agree = {f"{a}~{b}": overlap(nn[a][0], nn[b][0]) for i, a in enumerate(names) for b in names[i + 1:]}
    cl_agree = {f"{a}~{b}": float(adjusted_rand_score(labs[a], labs[b]))
                for i, a in enumerate(list(labs)) for b in list(labs)[i + 1:]}

    # blind listening test: for each anchor, its nearest neighbour under both models versus a far recording
    rng = random.Random(14)
    trials = []
    used = [v for v in ("mert", "clap") if v in nn]
    if used:
        S = sum(views[v][1] @ views[v][1].T for v in used)
        for i in rng.sample(range(n), min(args.trials, n)):
            order = [j for j in np.argsort(-S[i]) if j != i]
            near, far = order[0], rng.choice(order[n // 2:])
            a, b = (near, far) if rng.random() < 0.5 else (far, near)
            trials.append({"anchor": common[i], "a": common[a], "b": common[b], "near": "a" if a == near else "b"})

    fx = lambda x: f"{x:.2f}"
    report = [f"# Audio-only map of {args.maestro_name}'s songs: pilot report", "",
              "Built and judged from the audio alone: no title, film, composer, year or popularity is used.",
              f"{n} recordings. Neighbours = the {k} closest recordings by cosine similarity in the full vector space.", "",
              "## Do the models hear the same thing?", "",
              f"Share of neighbours two views have in common (chance {fx(chance)}):", ""]
    report += [f"- {p.replace('~', ' and ')}: {fx(x)}" for p, x in agree.items()]
    report += ["", "Agreement of the audio clusters (adjusted Rand, 0 = chance, 1 = identical):", ""]
    report += [f"- {p.replace('~', ' and ')}: {fx(x)}" for p, x in cl_agree.items()]
    report += ["", "## Each view", "",
               "| view | clusters | silhouette | stability | 2D trust | same-upload neighbours | chance | near-duplicates |",
               "|---|---|---|---|---|---|---|---|"]
    for v, r in summary.items():
        report.append(f"| {v} | {r.get('k', '')} | {fx(r['silhouette']) if 'silhouette' in r else ''} | "
                      f"{fx(r['stability_ari']) if 'stability_ari' in r else ''} | "
                      f"{fx(r['trustworthiness']) if 'trustworthiness' in r else ''} | {fx(r['same_upload'])} | "
                      f"{fx(r['same_upload_chance'])} | {r['near_duplicates']} |")
    report += ["", "Stability: adjusted Rand between the clusters and clusters re-fitted on 80% samples. "
               "2D trust: how well the map keeps each recording's true neighbours (1 = perfectly). "
               "Same-upload neighbours: share of neighbours from the same Internet Archive upload; far above "
               "chance means the model hears the transfer (noise, speed, EQ) rather than the music.",
               "", f"Listening test: {len(trials)} blind trials in the page (which of A and B sounds more like the anchor?)."]
    text = "\n".join(report) + "\n"
    (out / "map-report.md").write_text(text, encoding="utf-8")
    summary = {"views": summary, "neighbour_overlap": agree, "cluster_agreement": cl_agree, "chance": chance}
    (out / "map-summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    recs = {key: {"n": num[key], "listen": f"https://archive.org/download/{files[key]['identifier']}/"
                                           f"{urllib.parse.quote(files[key]['file'])}",
                  "upload": files[key]["identifier"]} for key in common}
    if args.gems and Path(args.gems).exists():  # Amitabh's own gem tags, shown on the map at his request
        gem_keys = {r["key"] for r in csv.DictReader(open(args.gems, encoding="utf-8")) if r.get("key")}
        for key in common:
            recs[key]["gem"] = key in gem_keys
        is_gem = [key in gem_keys for key in common]
        g = sum(is_gem)
        if g >= 3:
            report += ["", f"## Your tagged gems ({g} of {n} recordings)", "",
                       "| view | gem neighbours of gems | chance | gems more isolated (AUC, 0.5 = chance) |", "|---|---|---|---|"]
            for v in maps:
                idx, sims = nn[v]
                share = sum(sum(is_gem[j] for j in idx[i]) / k for i in range(n) if is_gem[i]) / g
                iso = 1 - sims.mean(axis=1)
                pos, neg = [iso[i] for i in range(n) if is_gem[i]], [iso[i] for i in range(n) if not is_gem[i]]
                auc = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg))
                summary["gems_" + v] = {"neighbour_share": share, "chance": (g - 1) / (n - 1), "isolation_auc": auc}
                report.append(f"| {v} | {share:.2f} | {(g - 1) / (n - 1):.2f} | {auc:.2f} |")
            text = "\n".join(report) + "\n"
            (out / "map-report.md").write_text(text, encoding="utf-8")
            (out / "map-summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
            print("\n".join(report[-len(maps) - 4:]))
    (out / "map.json").write_text(json.dumps({"recordings": recs, "maps": maps, "k": k, "summary": summary,
                                              "trials": trials}), encoding="utf-8")
    print(text)


def find_gems(args):
    """Find recordings for the gems Amitabh tagged in the canon explorer.

    Only gems published in 1965 or earlier (public domain in India). Each is looked up first in the
    earlier pilot's matches, then by a title search on the Internet Archive. Writes gems.csv (one row
    per tagged gem, with the recording found, if any) and gem-matches.csv (new recordings, in the
    matches.csv format, for ``embed --matches``).
    """
    from audio_gems import norm, sim

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.ledger:
        ledger = json.load(open(args.ledger, encoding="utf-8"))
        tags = [t for t in ledger["tags"] if t.get("maestro") == args.maestro and not t.get("untagged_seen_at")]
    else:  # tagged unit ids, with titles and years from a reduced canon (a machine without the ledger)
        from audio_gems import load_canon

        byid = {u["id"]: u for u in load_canon(args.canon)[0]}
        ids = [int(x) for x in args.units.replace(",", " ").split()]
        tags = [{"unit": i, "title": byid[i]["title"], "year": byid[i]["year"]} if i in byid
                else {"unit": i, "title": "(not in the reduced canon)", "year": None} for i in ids]
    known = list(csv.DictReader(open(args.matches, encoding="utf-8"))) if Path(args.matches).exists() else []
    by_unit = {m["unit"]: m for m in known}
    http = Http(args.rps)
    rows, new = [], []
    def as_match(t, ident, f, s):
        return {"unit": t["unit"], "group": "gem", "year": t["year"], "canon_title": t["title"], "identifier": ident,
                "file": f, "format": "", "archive_title": "", "archive_year": "", "sim": s}

    for n, t in enumerate(sorted(tags, key=lambda t: (t.get("year") or 0, t["unit"])), 1):
        year = t.get("year")
        row = {"unit": t["unit"], "year": year or "", "title": t["title"], "film": t.get("container") or "",
               "status": "", "key": "", "identifier": "", "file": "", "sim": ""}
        rows.append(row)
        if not year or year > LAST_PD_YEAR:
            row["status"] = "after 1965 (not public domain)" if year else "no year, or not tier A by 1965"
            continue
        k = norm(t["title"])
        hit = by_unit.get(str(t["unit"]))
        if hit and sim(k, norm(hit["canon_title"])) < 0.6:  # unit ids can shift between canon rebuilds
            hit = None
        if not hit:
            cand = [(sim(k, norm(m["canon_title"])), m) for m in known if abs(int(m["year"]) - year) <= 1]
            cand = [c for c in cand if c[0] >= args.min_sim]
            hit = max(cand, key=lambda c: c[0])[1] if cand else None
        if hit:
            row.update(status="already matched", identifier=hit["identifier"], file=hit["file"], sim=hit.get("sim", ""))
            row["key"] = file_key(hit["identifier"], hit["file"])
            # listed too, so that embed covers it even when the earlier random sample left it out
            new.append(as_match(t, hit["identifier"], hit["file"], hit.get("sim", "")))
            continue
        found = _search_archive(http, t, k, year, args.min_sim, args.search or args.maestro)
        if found:
            ident, f, s = found
            row.update(status="found", identifier=ident, file=f, sim=round(s, 3), key=file_key(ident, f))
            new.append(as_match(t, ident, f, round(s, 3)))
        else:
            row["status"] = "no recording found"
        print(f"{n}/{len(tags)} {row['status']}", file=sys.stderr)
    with open(out / "gems.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(out / "gem-matches.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["unit", "group", "year", "canon_title", "identifier", "file", "format",
                                "archive_title", "archive_year", "sim"])
        w.writeheader()
        w.writerows(new)
    print(json.dumps(collections.Counter(r["status"] for r in rows), indent=1))


def _search_archive(http, tag, k, year, min_sim, term="rafi"):
    """Search the Internet Archive for one song by the first words of its title."""
    from audio_gems import AUDIO_FORMATS, _fmt_rank, _year, norm, sim

    words = [w for w in k.split() if len(w) > 1][:4]
    if len(words) < 2:
        return None
    q = f'({" ".join(words)}) AND ({term}) AND mediatype:(audio)'
    url = "https://archive.org/advancedsearch.php?" + urllib.parse.urlencode(
        {"q": q, "fl[]": ["identifier", "year", "date", "title"], "rows": 25, "output": "json"}, doseq=True)
    try:
        docs = json.loads(http.get(url))["response"]["docs"]
    except Exception as e:  # noqa: BLE001
        print(f"search failed for {tag['unit']}: {e}", file=sys.stderr)
        return None
    best = None
    for it in docs:
        iy = _year(it.get("year") or it.get("date"))
        if iy and iy > LAST_PD_YEAR:
            continue
        try:
            meta = json.loads(http.get(f"https://archive.org/metadata/{it['identifier']}"))
        except Exception:  # noqa: BLE001
            continue
        files = [f for f in meta.get("files", []) if f.get("format") in AUDIO_FORMATS]
        for f in files:
            title = f.get("title") or (it.get("title") if len(files) <= 3 else None) or Path(f["name"]).stem
            s = sim(k, norm(str(title)))
            if s >= min_sim and (best is None or (s, _fmt_rank(f.get("format"))) > best[0]):
                best = ((s, _fmt_rank(f.get("format"))), it["identifier"], f["name"])
    return (best[1], best[2], best[0][0]) if best else None


NAMES = {"rafi": ("Mohammed Rafi", "Rafi"), "lata": ("Lata Mangeshkar", "Lata")}  # full name, short name


def load_map(path: str, slug: str | None = None) -> dict:
    """A map.json, a folder holding one, a gzipped copy, or an earlier page that embeds one."""
    p = Path(path)
    if p.is_dir():
        p = p / "map.json"
    raw = p.read_bytes()
    if p.suffix == ".gz":
        import gzip

        raw = gzip.decompress(raw)
    text = raw.decode("utf-8")
    if p.suffix in (".html", ".htm"):
        mark = "const ALL = " if "const ALL = " in text else "const DATA = "
        d = json.JSONDecoder().raw_decode(text, text.index(mark) + len(mark))[0]  # json reads "<\\/" as "</"
        if "maestros" in d:  # a page with a switch: that maestro's map
            return d["maestros"][slug if slug in d["maestros"] else next(iter(d["maestros"]))]
        return d
    return json.loads(text)


def page(args):
    """Write a self-contained explorer page from map.json (no audio, no song metadata).

    With several ``--map SLUG=PATH`` the page holds each maestro's map behind a switch.
    """
    specs = [m.split("=", 1) for m in args.map] if args.map else [[args.maestro, args.out]]
    book = {}
    for slug, path in specs:
        d = load_map(path, slug)
        d["name"], d["short"] = NAMES.get(slug, (slug.replace("-", " ").title(),) * 2)
        book[slug] = d
    html = Path(__file__).with_name("audio_map.html").read_text(encoding="utf-8")
    data = json.dumps({"maestros": book}, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace("/*MAP_DATA*/null", data)
    dest = Path(args.page or Path(args.out) / ("sound-map.html" if len(book) > 1 else f"{specs[0][0]}-sound-map.html"))
    dest.write_text(html, encoding="utf-8")
    for slug, d in book.items():
        print(f"{slug}: {len(d['recordings'])} recordings, {sum(1 for r in d['recordings'].values() if r.get('gem'))} gems",
              file=sys.stderr)
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
    m.add_argument("--craft", default="data/pilots/audio/features.csv", help="the earlier pilot's 20 features")
    m.add_argument("--k", type=int, default=10)
    m.add_argument("--models", default="mert,clap", help="views to map; recordings must be in all of them")
    m.add_argument("--gems", help="gems.csv from find-gems: mark Amitabh's tagged gems on the map")
    m.add_argument("--trials", type=int, default=30)
    m.add_argument("--pcs", type=int, default=24, help="principal axes kept for the latent views")
    m.add_argument("--dup", type=float, default=0.985)
    m.add_argument("--maestro-name", default="Rafi", help="name in the report heading")
    g = sub.add_parser("find-gems")
    g.add_argument("--ledger", help="gem-tags-ledger.json from the canon explorer")
    g.add_argument("--canon", help="without --ledger: reduced canon (audio-pilot-canon.json.gz) to read tagged units from")
    g.add_argument("--units", help="without --ledger: the tagged unit ids, comma separated")
    g.add_argument("--matches", default="data/pilots/audio/matches.csv")
    g.add_argument("--out", default="data/pilots/audio-map")
    g.add_argument("--min-sim", type=float, default=0.82)
    g.add_argument("--maestro", default="rafi", help="whose tags to read from the ledger")
    g.add_argument("--search", help="name archive.org items must mention (default: the maestro slug)")
    g.add_argument("--rps", type=float, default=1.0)
    p = sub.add_parser("page")
    p.add_argument("--out", default="data/pilots/audio-map")
    p.add_argument("--page", help="where to write the page (default: OUT/<maestro>-sound-map.html)")
    p.add_argument("--maestro", default="rafi", help="slug of the single map in --out")
    p.add_argument("--map", action="append", metavar="SLUG=PATH",
                   help="a maestro's map (map.json, its folder, .json.gz or an earlier page); repeat for a switch")
    args = ap.parse_args()
    {"embed": embed, "map": map_, "page": page, "find-gems": find_gems}[args.step](args)


if __name__ == "__main__":
    main()
