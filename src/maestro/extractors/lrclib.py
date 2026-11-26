"""LRCLIB (lrclib.net): lyrics for canon songs that still have none, as private text plus publishable counts.

Amitabh's decision (26 Nov 2026): lyrics are collected for a word-frequency analysis, kept private,
and only aggregate word counts may be published. LRCLIB is an open lyrics API that asks clients to name
themselves in the User-Agent (the crawler's own names the project) and to keep their rate modest: the
pack lists it as `access: api` with `api_rps: 1`.

Planning: `plan(store)` queues one search per Tier A unit that has no active `lyrics` claim and no open
lrclib job: https://lrclib.net/api/search?track_name=<the song's short title>&artist_name=<maestro>,
with the unit as the job's entity. Run it with

    uv run python -m maestro.extractors.lrclib --pack maestros/<slug> [--limit N]

then `maestro-crawl --pack maestros/<slug> --max-jobs N` to fetch. The plan stops at the pack's crawl
budget; plan again after a crawl for the rest.

Extraction: a result counts only if its artistName names the maestro (the pack's name or a variant) and
its trackName matches one of the unit's titles at resolve's CLEAR_TITLE_RATIO; among those, one whose
album matches the unit's film wins, then one with synced lyrics. Its plainLyrics become a `lyrics` claim
(private under the pack's `legal.lyrics`) and a publishable `lyrics_meta` with the source, script and
line and word counts. No match writes nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.parse import urlencode

from maestro.extractors.hindigeetmala_lyrics import store_lyrics
from maestro.store import StoreError

SOURCE = "lrclib"
EXTRACTOR = "lrclib"
API = "https://lrclib.net/api/search"


def search_url(title: str, artist: str) -> str:
    return API + "?" + urlencode({"track_name": title, "artist_name": artist})


def short_title(text: str, words: int = 6) -> str:
    """A song's searchable title: up to the first comma, ellipsis or repeat mark, at most `words` words,
    without a leading line number ("2 Naiya Meri ...")."""
    text = re.sub(r"^\s*(?:\d+|[a-zA-Z])[\s,]+(?=\w{2})", "", text or "")
    text = re.split(r"\s*(?:---|,|\.{2,}|…|\(|\n)", text)[0]
    return " ".join(text.split()[:words]).strip()


def _members(store, groups) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for r in store.conn.execute("SELECT id FROM entity WHERE type = %s", (store.pack.unit,)).fetchall():
        out.setdefault(groups.find(r["id"]), []).append(r["id"])
    return out


def _titles(store, ids: list[int]) -> list[tuple[str, str, str]]:
    """(field, source, text) of every active title and first line on these records, then their labels."""
    out = [(r["field"], r["source_id"], r["value"]) for r in store.conn.execute(
        """SELECT field, source_id, value #>> '{}' AS value FROM claim
           WHERE entity_id = ANY(%s) AND status = 'active' AND field IN ('title', 'first_line')
             AND jsonb_typeof(value) = 'string' ORDER BY id""", (ids,)).fetchall()]
    return out + [("label", "", r["label"]) for r in store.conn.execute(
        "SELECT label FROM entity WHERE id = ANY(%s) ORDER BY id", (ids,)).fetchall()]


def query_title(titles: list[tuple[str, str, str]], label: str) -> str:
    """The title to search with: a roman song title from a source that gives short titles, else the
    first line, else any title (HFGK's titles are the whole opening)."""
    def roman(t):
        return t and not re.search(r"[^\x00-\x7f]", t)

    ranked = sorted((f != "title", s == "hfgk_online", i) for i, (f, s, t) in enumerate(titles) if roman(t))
    for _f, _s, i in ranked:
        t = short_title(titles[i][2])
        if len(t.split()) >= 2:
            return t
    return short_title(label) or label


def plan(store, limit: int | None = None) -> dict[str, int]:
    """Queue LRCLIB searches for Tier A units without lyrics. Returns counts."""
    from maestro.completeness import load_units
    from maestro.resolve import Groups

    groups = Groups(store)
    units = load_units(store, groups)
    members = _members(store, groups)
    have = {groups.find(r["entity_id"]) for r in store.conn.execute(
        "SELECT DISTINCT entity_id FROM claim WHERE field = 'lyrics' AND status = 'active'").fetchall()}
    tried = {r["entity_id"] for r in store.conn.execute(
        "SELECT DISTINCT entity_id FROM job WHERE extractor = %s AND entity_id IS NOT NULL", (EXTRACTOR,)).fetchall()}
    artist = store.pack.data["identity"]["name"]
    counts = {"tier_a": 0, "with_lyrics": 0, "already_tried": 0, "queued": 0, "budget_stop": 0}
    for uid in sorted(units):
        u = units[uid]
        if u.tier != "A":
            continue
        counts["tier_a"] += 1
        if uid in have:
            counts["with_lyrics"] += 1
            continue
        if uid in tried:
            counts["already_tried"] += 1
            continue
        if limit is not None and counts["queued"] >= limit:
            break
        title = query_title(_titles(store, members.get(uid, [uid])), u.label)
        try:
            store.enqueue_crawl(search_url(title, artist), EXTRACTOR, "enrich", entity_id=uid)
        except StoreError:
            counts["budget_stop"] = 1
            break
        counts["queued"] += 1
    return counts


# ---- extraction ----------------------------------------------------------------------------------------

def names_maestro(store, artist: str, norm) -> bool:
    ident = store.pack.data["identity"]
    mine = {norm.person(n) for n in [ident["name"], *ident.get("variants", [])]}
    parts = re.split(r"\s*(?:,|&|/|;|\bfeat\.?|\bft\.?|\band\b|\bwith\b|\bx\b)\s*", artist or "", flags=re.I)
    return any(norm.person(p) in mine for p in parts if p.strip())


def best_match(store, results: list[dict], unit: int) -> dict | None:
    from maestro.extractors.lastfm import split_title
    from maestro.resolve import CLEAR_TITLE_RATIO, Groups, Normaliser, title_similarity

    cached = getattr(store, "_lrclib_index", None)
    if cached is None:  # one crawl run reads many results: build the index once per store
        groups = Groups(store)
        cached = store._lrclib_index = (Normaliser.for_store(store), groups, _members(store, groups))
    norm, groups, members = cached
    ids = members.get(groups.find(unit)) or [unit]
    keys = {norm.title(short_title(t, 12)) for _f, _s, t in _titles(store, ids)} - {""}
    films = {norm.container(r["label"]) for r in store.conn.execute(
        """SELECT e.label FROM claim c JOIN entity e ON e.id = c.ref_entity_id
           WHERE c.entity_id = ANY(%s) AND c.field = 'part_of' AND c.status = 'active'""", (ids,)).fetchall()}
    best, best_rank = None, None
    for r in results:
        text = r.get("plainLyrics") or ""
        if r.get("instrumental") or not text.strip() or not names_maestro(store, r.get("artistName"), norm):
            continue
        name, film_hint = split_title(r.get("trackName") or "")
        key = norm.title(short_title(name, 12))
        sim = max((title_similarity(key, k) for k in keys), default=0.0)
        if sim < CLEAR_TITLE_RATIO:
            continue
        album = norm.container(film_hint or r.get("albumName") or "")
        rank = (album in films, sim, bool(r.get("syncedLyrics")))
        if best_rank is None or rank > best_rank:
            best, best_rank = r, rank
    return best


def extract(ctx) -> None:
    unit = (ctx.job or {}).get("entity_id")
    if unit is None:
        raise ValueError("an lrclib job needs the unit as its entity_id (queue it with plan())")
    results = json.loads(ctx.page.text)
    if not isinstance(results, list):
        raise ValueError(f"unexpected LRCLIB answer: {type(results).__name__}")
    hit = best_match(ctx.store, results, unit)
    if hit:
        store_lyrics(ctx.store, unit, hit["plainLyrics"].strip(), source_id=SOURCE, meta_source="lrclib",
                     extractor=EXTRACTOR, raw_page_id=ctx.raw_page_id)


def main(argv: list[str] | None = None) -> int:
    from maestro import config
    from maestro.pack import load_pack
    from maestro.store import Store

    p = argparse.ArgumentParser(description="Queue LRCLIB lyric searches for Tier A units without lyrics.")
    p.add_argument("--pack", required=True)
    p.add_argument("--dsn")
    p.add_argument("--limit", type=int, default=None, help="queue at most this many searches")
    args = p.parse_args(argv)
    pack = load_pack(args.pack)
    dsn = config.dsn_for(pack.slug, args.dsn)
    if not dsn:
        p.error(f"--dsn (or {config.dsn_env_name(pack.slug)}) is required")
    store = Store.connect(dsn, pack)
    store.migrate()
    store.sync_sources()
    print(json.dumps(plan(store, args.limit), indent=2))
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
