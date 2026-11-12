"""Wikidata Query Service: films (and other audiovisual works) whose score the maestro composed (P86).

Seed with `films_url(qid)`. Each film becomes a unit (keyed `film:<QID>`) of kind `film_score`,
titled with the film's original title (else its French, else English label), with its first
publication year and a composer credit to the maestro. Wikidata is CC0; keep to `api_rps`.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit

SOURCE = "wikidata"
ENDPOINT = "https://query.wikidata.org/sparql"
QUERY = """SELECT ?film ?title ?label ?en (MIN(YEAR(?date)) AS ?year) WHERE {{
  ?film wdt:P86 wd:{qid} .
  OPTIONAL {{ ?film wdt:P1476 ?title }}
  OPTIONAL {{ ?film rdfs:label ?label FILTER(LANG(?label) = "fr") }}
  OPTIONAL {{ ?film rdfs:label ?en FILTER(LANG(?en) = "en") }}
  OPTIONAL {{ ?film wdt:P577 ?date }}
}} GROUP BY ?film ?title ?label ?en"""


def films_url(qid: str) -> str:
    return f"{ENDPOINT}?{urlencode({'format': 'json', 'query': QUERY.format(qid=qid)})}"


def parse(data: dict) -> list[dict]:
    films: dict[str, dict] = {}
    for b in data.get("results", {}).get("bindings", []):
        qid = b["film"]["value"].rsplit("/", 1)[-1]
        title = next((b[k]["value"] for k in ("title", "label", "en") if k in b), qid)
        year = int(b["year"]["value"]) if "year" in b else None
        f = films.setdefault(qid, {"qid": qid, "title": title, "year": year})
        if year and (f["year"] is None or year < f["year"]):
            f["year"] = year
    return sorted(films.values(), key=lambda f: f["qid"])


def extract(ctx) -> None:
    store, pack = ctx.store, ctx.store.pack
    qid = pack.data["identity"]["ids"]["wikidata"]
    query = parse_qs(urlsplit(ctx.page.url).query).get("query", [""])[0]
    if not re.search(rf"wd:{qid}\b", query):
        raise ValueError(f"wikidata_films expects the film query for {qid}: {ctx.page.url}")
    maestro = store.maestro_entity()

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="wikidata_films", extractor_version="1", raw_page_id=ctx.raw_page_id)

    for f in parse(json.loads(ctx.page.text)):
        uid = store.find_or_create_by_xref(pack.unit, SOURCE, f"film:{f['qid']}", f["title"])
        claim(uid, "title", f["title"])
        claim(uid, "work_kind", "film_score")
        claim(uid, "film", f["qid"])
        if f["year"]:
            claim(uid, "year", f["year"])
        claim(uid, "credit", {"role": "composer"}, ref=maestro)
