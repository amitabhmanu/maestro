"""Lyric words over time: which words, and which words for one idea, songs used year by year.

Reads each song's text (the full lyrics where the canon holds them, kept private; otherwise or with
`--text opening` the opening line every canon may publish), folds spellings to one key per word, and
counts per year how many songs use each word. The output is aggregate counts only, so it may ship:

- `years`: songs counted per year;
- `words`: per word heard in at least `min_songs` songs, its spellings and songs per year;
- `concepts`: per idea in the concept lexicon (love: pyaar, prem, ishq ...), each word's songs per year;
- `waves`: words that rose for a few years and faded (more than half their songs inside one window,
  rarer both before and after it).

No text and no per-song word list is written: a word that fewer than `min_songs` songs use is left out,
so the counts never point back at one song's lines. The lexicons are the pack's (`lexicon.lyric_stopwords`,
`lyric_concepts`, `lyric_variants`; Hindi film packs share `maestros/hindi-films/lexicon/`).

Spelling: romanised sources spell one word many ways (mohabbat/muhabbat, zindagi/zindagee/jindagi),
Devanagari sources are transliterated first. `key()` folds long vowels, doubled letters, z/j, q/k, w/v,
ph/f, a y between vowels and a final h; then words of four consonants or more whose consonants agree and whose keys
differ by at most two letters (and end alike: chaand and chanda stay apart) join the most frequent spelling (taqdir/taqadir). The variants lexicon
settles the rest by hand.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

FORMAT = "maestro-words/1"
MIN_SONGS = 8
WAVE_MIN = 12          # a wave needs a word heard in at least this many songs
WAVE_SHARE = 0.5       # and more than this share of them inside its window
WAVE_WINDOWS = (3, 4, 5, 6, 8, 10)

# Devanagari -> a plain romanisation close to how Hindi film sites spell (no diacritics).
_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "ळ": "l", "क़": "q", "ख़": "kh", "ग़": "gh", "ज़": "z", "फ़": "f", "ड़": "r",
    "ढ़": "rh", "य़": "y",
}
_NUKTA = {"क": "q", "ख": "kh", "ग": "gh", "ज": "z", "फ": "f", "ड": "r", "ढ": "rh"}
_VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai",
           "ओ": "o", "औ": "au", "ऑ": "o"}
_MATRAS = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o",
           "ौ": "au", "ॉ": "o"}
_VIRAMA, _NUKTA_SIGN = "्", "़"
_NASAL = {"ं": "n", "ँ": "n"}


def devanagari_to_latin(text: str) -> str:
    """A loose romanisation: inherent 'a' after a consonant unless a vowel sign or virama follows, and
    none at a word's end (schwa deletion), so दिल -> dil, मोहब्बत -> mohabbat, ज़िंदगी -> zindagee."""
    out: list[str] = []
    chars = list(text)
    i = 0
    while i < len(chars):
        c = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if c in _CONS:
            cons = _CONS[c]
            if nxt == _NUKTA_SIGN:
                cons = _NUKTA.get(c, cons)
                i += 1
                nxt = chars[i + 1] if i + 1 < len(chars) else ""
            out.append(cons)
            if nxt in _MATRAS:
                out.append(_MATRAS[nxt])
                i += 1
            elif nxt == _VIRAMA:
                i += 1
            elif nxt and ("ऀ" <= nxt <= "ॿ") and nxt not in ("।", "॥"):
                out.append("a")
            # else: end of word, no inherent vowel
        elif c in _VOWELS:
            out.append(_VOWELS[c])
        elif c in _NASAL:
            out.append(_NASAL[c])
        elif c == "ः":
            out.append("h")
        else:
            out.append(c)
        i += 1
    return "".join(out)


def script_of(text: str) -> str:
    deva = sum(1 for c in text if "ऀ" <= c <= "ॿ")
    arab = sum(1 for c in text if "؀" <= c <= "ۿ")
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    total = deva + arab + latin or 1
    for name, n in (("devanagari", deva), ("urdu", arab), ("latin", latin)):
        if n / total >= 0.8:
            return name
    return "mixed"


def key(word: str) -> str:
    """One spelling key for a romanised word (see the module notes)."""
    w = re.sub(r"[^a-z]", "", word.lower())
    for a, b in (("ee", "i"), ("oo", "u"), ("aa", "a"), ("w", "v"), ("q", "k"), ("z", "j"), ("ph", "f")):
        w = w.replace(a, b)
    w = re.sub(r"(.)\1+", r"\1", w)
    w = re.sub(r"(?<=[aeiou])y(?=[aeiou])", "", w)   # aayee / aai, jaaye / jaae
    if len(w) > 3 and w.endswith("h"):
        w = w[:-1]
    if len(w) >= 5:   # a written inherent vowel: aapakee / aapki, taqadeer / taqdeer
        w = re.sub(r"(?<=[aeiou][^aeiouy])a(?=[^aeiouy][aeiou])", "", w)
    return w


def consonants(k: str) -> str:
    return re.sub(r"[aeiouy]", "", k)


def _edit_distance(a: str, b: str, cap: int = 3) -> int:
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# Version notes and catalogue remarks that sources put inside titles, not words of the song.
_NOTES = re.compile(r"\((?:[^)]*\b(?:part|song|version|female|male|sad|happy|duet|solo|revival|aalap|alaap|dialogue?s?|"
                    r"chorus|remix|instrumental|reprise|bit)\b[^)]*)\)", re.I)
_OPENING_CUT = re.compile(r"\.\s*\.|…|\s/\s")


def words_of(text: str, opening_only: bool = False) -> list[str]:
    """The romanised words of a text (Devanagari transliterated first), lower case."""
    if opening_only:
        text = _OPENING_CUT.split(text, maxsplit=1)[0]
    text = _NOTES.sub(" ", text)
    if any("ऀ" <= c <= "ॿ" for c in text):
        text = devanagari_to_latin(text)
    return [w.lower() for w in re.findall(r"[A-Za-z]+", text)]


# --- lexicons -------------------------------------------------------------------------------------

def _rows(path: Path) -> Iterable[list[str]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].rstrip()
        if line.strip():
            yield [c.strip() for c in line.split("\t")]


class Lexicon:
    """Stopwords, hand-set spelling variants and concepts, all keyed by `key()`."""

    def __init__(self, stopwords: Iterable[str] = (), variants: dict[str, str] | None = None,
                 concepts: dict[str, dict[str, Any]] | None = None):
        self.stop = {key(w) for w in stopwords}
        self.variants = {key(a): key(b) for a, b in (variants or {}).items()}
        self.concepts = concepts or {}

    @classmethod
    def for_pack(cls, pack_data: dict[str, Any], pack_dir: Path) -> "Lexicon":
        lex = pack_data.get("lexicon") or {}

        def paths(name: str) -> list[Path]:
            v = lex.get(name) or []
            return [pack_dir / p for p in ([v] if isinstance(v, str) else v)]

        stop: list[str] = []
        for p in paths("lyric_stopwords"):
            stop += [r[0] for r in _rows(p)]
        variants: dict[str, str] = {}
        for p in reversed(paths("lyric_variants")):   # earlier files win
            variants.update({r[0]: r[1] for r in _rows(p) if len(r) >= 2})
        concepts: dict[str, dict[str, Any]] = {}
        for p in reversed(paths("lyric_concepts")):
            for r in _rows(p):
                if len(r) >= 3:
                    concepts[r[0]] = {"label": r[1], "words": [w.strip() for w in r[2].split(",") if w.strip()],
                                      "note": r[3] if len(r) > 3 else ""}
        return cls(stop, variants, concepts)

    def norm(self, word: str) -> str:
        k = key(word)
        return self.variants.get(k, k)


# --- songs ----------------------------------------------------------------------------------------

def songs_from_snapshot(snap: dict[str, Any], tiers: tuple[str, ...] = ("A",)) -> list[dict[str, Any]]:
    """Opening lines from an explorer snapshot: {id, year, container, text, lyricists, kind: 'opening'}."""
    out = []
    for u in snap.get("units", []):
        if u.get("tier") not in tiers or not u.get("year"):
            continue
        out.append({"id": u["id"], "year": u["year"], "container": u.get("container"), "text": u["title"],
                    "lyricists": (u.get("credits") or {}).get("lyricist", []), "kind": "opening"})
    return out


def lyrics_by_unit(store) -> dict[int, str]:
    """The canon's lyrics (private claims), one text per unit (merged records count once; the longest wins)."""
    from maestro.resolve import Groups

    groups = Groups(store)
    best: dict[int, str] = {}
    for r in store.conn.execute(
            """SELECT entity_id, value #>> '{}' AS text FROM claim
               WHERE field = 'lyrics' AND status = 'active' AND jsonb_typeof(value) = 'string'""").fetchall():
        root = groups.find(r["entity_id"])
        if r["text"] and len(r["text"]) > len(best.get(root, "")):
            best[root] = r["text"]
    return best


def with_lyrics(songs: list[dict[str, Any]], lyrics: dict[int, str]) -> list[dict[str, Any]]:
    return [{**s, "text": lyrics[s["id"]], "kind": "lyrics"} if s["id"] in lyrics else s for s in songs]


def dedupe(songs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Versions of one song in one film (sad and happy, part 1 and 2) count once: same container,
    same opening words. Reused songs are already merged into their originals by resolve."""
    seen, out = set(), []
    for s in sorted(songs, key=lambda s: (s["kind"] != "lyrics", s["year"])):
        k = (s.get("container"), "".join(key(w) for w in words_of(s["text"], opening_only=True)[:5]))
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


# --- counting -------------------------------------------------------------------------------------

def merge_spellings(forms: dict[str, Counter]) -> dict[str, str]:
    """Join keys that are spellings of one word (four consonants or more, same consonants, keys at
    most two letters apart) to the most heard one. Returns key -> canonical key."""
    by_cons: dict[str, list[str]] = defaultdict(list)
    for k in forms:
        c = consonants(k)
        if len(c) >= 4:
            by_cons[c].append(k)
    out: dict[str, str] = {}
    for ks in by_cons.values():
        ks.sort(key=lambda k: -sum(forms[k].values()))
        for k in ks[1:]:
            head = next((h for h in ks if h != k and out.get(h, h) == h and sum(forms[h].values()) >= sum(forms[k].values())
                         and h[-1] == k[-1] and _edit_distance(h, k) <= 2), None)
            if head:
                out[k] = head
    return out


def count(songs: list[dict[str, Any]], lex: Lexicon) -> tuple[Counter, dict[str, Counter], dict[str, Counter], dict[str, str]]:
    """(songs per year, per word key its songs per year, per word key its spellings, joined key -> head)."""
    years: Counter = Counter()
    per: dict[str, Counter] = defaultdict(Counter)
    forms: dict[str, Counter] = defaultdict(Counter)
    for s in songs:
        years[s["year"]] += 1
        seen = set()
        for w in words_of(s["text"], opening_only=s["kind"] == "opening"):
            k = lex.norm(w)
            if len(k) < 2:
                continue
            forms[k][w] += 1
            seen.add(k)
        for k in seen:
            per[k][s["year"]] += 1
    joined = merge_spellings(forms)
    # Two spellings of one word in one song must count the song once, so recount per song.
    if joined:
        per = defaultdict(Counter)
        for s in songs:
            seen = {joined.get(k, k) for k in (lex.norm(w) for w in words_of(s["text"], opening_only=s["kind"] == "opening"))
                    if len(k) >= 2}
            for k in seen:
                per[k][s["year"]] += 1
        for k, head in joined.items():
            forms[head].update(forms.pop(k))
    return years, per, forms, joined


def waves(years: Counter, per: dict[str, Counter], stop: set[str], min_total: int = WAVE_MIN) -> list[dict[str, Any]]:
    """Words concentrated in one window of years and rarer on both sides of it."""
    ys = sorted(years)
    if not ys:
        return []
    total_songs = sum(years.values())
    out = []
    for w, c in per.items():
        t = sum(c.values())
        if t < min_total or w in stop or len(w) < 3:
            continue
        best = None
        for span in WAVE_WINDOWS:
            for a in range(ys[0], ys[-1] - span + 2):
                b = a + span - 1
                inside = sum(c[y] for y in range(a, b + 1))
                if inside / t <= WAVE_SHARE:
                    continue
                n_in = sum(years[y] for y in range(a, b + 1)) or 1
                rate_in = inside / n_in
                before_n = sum(years[y] for y in ys if y < a)
                after_n = sum(years[y] for y in ys if y > b)
                before = sum(c[y] for y in ys if y < a)
                after = t - inside - before
                # it must fade: clearly rarer after the window (and before it, where there are songs)
                if after_n < 50 or (after + 0.5) / after_n > rate_in / 2:
                    continue
                if before_n >= 50 and (before + 0.5) / before_n > rate_in / 2:
                    continue
                ratio = rate_in / ((t - inside + 0.5) / max(total_songs - n_in, 1))
                score = math.log(ratio) * math.log(inside)
                if best is None or score > best["score"]:
                    best = {"word": w, "from": a, "to": b, "inside": inside, "total": t,
                            "ratio": round(ratio, 1), "score": round(score, 2)}
        if best:
            out.append(best)
    out.sort(key=lambda x: -x["score"])
    return out


def build(songs: list[dict[str, Any]], lex: Lexicon, *, maestro: dict[str, Any], min_songs: int = MIN_SONGS,
          top_waves: int = 150) -> dict[str, Any]:
    songs = dedupe(songs)
    years, per, forms, joined = count(songs, lex)
    def label(k: str) -> str:
        return forms[k].most_common(1)[0][0] if forms.get(k) else k
    words = {}
    for k, c in per.items():
        if sum(c.values()) >= min_songs:
            words[k] = {"label": label(k), "spellings": [f for f, _ in forms[k].most_common(6)],
                        "stop": k in lex.stop, "years": {str(y): n for y, n in sorted(c.items())}}
    concepts = {}
    for cid, cdef in lex.concepts.items():
        members = {}
        for w in cdef["words"]:
            k = lex.norm(w)
            k = joined.get(k, k)
            c = per.get(k)
            if any(m["key"] == k for m in members.values()):
                continue
            if c and sum(c.values()) >= min_songs:
                members[w] = {"key": k, "years": {str(y): n for y, n in sorted(c.items())}}
        if members:
            concepts[cid] = {"label": cdef["label"], "note": cdef.get("note", ""), "words": members}
    kinds = Counter(s["kind"] for s in songs)
    return {
        "format": FORMAT,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "maestro": maestro,
        "text": {"lyrics": kinds.get("lyrics", 0), "opening": kinds.get("opening", 0)},
        "min_songs": min_songs,
        "years": {str(y): n for y, n in sorted(years.items())},
        "lyrics_years": {str(y): n for y, n in sorted(Counter(s["year"] for s in songs if s["kind"] == "lyrics").items())},
        "words": words,
        "concepts": concepts,
        "waves": waves(years, per, lex.stop)[:top_waves],
    }


def check_publishable(words: dict[str, Any], min_songs: int) -> list[str]:
    """Problems that would make a words file unsafe to ship (it must hold counts only)."""
    bad = []
    for k in ("words", "concepts", "waves", "years"):
        if k not in words:
            bad.append(f"missing {k}")
    for k, w in words.get("words", {}).items():
        if sum(w["years"].values()) < min_songs:
            bad.append(f"word {k!r} is used by fewer than {min_songs} songs")
        if any(" " in s for s in w["spellings"]):
            bad.append(f"word {k!r} has a spelling with a space")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="maestro-words", description=__doc__.split("\n\n")[0])
    ap.add_argument("--pack", required=True, help="the maestro's pack folder (for its lexicons)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--snapshot", type=Path, help="an explorer snapshot: counts its opening lines")
    src.add_argument("--dsn", nargs="?", const="", help="the canon store: full lyrics where held (private), else opening lines")
    ap.add_argument("--text", choices=["best", "opening"], default="best",
                    help="'opening' counts opening lines only, even where lyrics are held")
    ap.add_argument("--min-songs", type=int, default=MIN_SONGS)
    out = ap.add_mutually_exclusive_group(required=True)
    out.add_argument("--out", type=Path, help="the words JSON to write (counts only)")
    out.add_argument("--site", type=Path, help="an explorer site folder: writes canon/words/<slug>.json and lists it "
                                               "in canon/index.json, so the explorer shows its Words view")
    args = ap.parse_args(argv)

    from maestro.pack import load_pack

    pack = load_pack(Path(args.pack) / "maestro.yaml" if Path(args.pack).is_dir() else Path(args.pack))
    lex = Lexicon.for_pack(pack.data, pack.path.parent)
    if args.snapshot:
        snap = json.loads(args.snapshot.read_text(encoding="utf-8"))
    else:
        from maestro.config import dsn_for
        from maestro.explore import snapshot
        from maestro.store import Store

        store = Store.connect(args.dsn or dsn_for(pack), pack)
        snap = snapshot(store)
    songs = songs_from_snapshot(snap)
    if args.dsn is not None and args.text == "best":
        songs = with_lyrics(songs, lyrics_by_unit(store))
    ident = pack.data["identity"]
    data = build(songs, lex, maestro={"slug": pack.path.parent.name, "name": ident.get("name"),
                                      "wikidata": (ident.get("ids") or {}).get("wikidata")}, min_songs=args.min_songs)
    bad = check_publishable(data, args.min_songs)
    if bad:
        print("\n".join(bad), file=sys.stderr)
        return 1
    slug = data["maestro"]["slug"]
    if args.site:
        args.out = args.site / "canon" / "words" / f"{slug}.json"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{args.out}: {sum(int(v) for v in data['years'].values())} songs "
          f"({data['text']['lyrics']} with lyrics), {len(data['words'])} words, {len(data['concepts'])} concepts, "
          f"{len(data['waves'])} waves")
    if args.site:
        idx_path = args.site / "canon" / "index.json"
        index = json.loads(idx_path.read_text("utf-8")) if idx_path.is_file() else {"maestros": []}
        entry = next((m for m in index.get("maestros", []) if m["slug"] == slug), None)
        if entry is None:
            print(f"{idx_path} lists no maestro {slug!r}: write its snapshot with maestro-explore first", file=sys.stderr)
            return 1
        entry["words"] = f"canon/words/{slug}.json"
        idx_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
