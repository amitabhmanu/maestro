"""Wikidata Query Service: works the maestro performed, composed or wrote the words for, with how
many Wikipedia (and sister) articles each has (`wikibase:sitelinks`).

Seed with `articles_url(qid)`. Each item with at least one article becomes a `wikipedia_articles`
mention (strength = number of articles): a song or work that has its own encyclopedia article is
one people know. Items that are films, albums or series are marked `kind: container`; they count
only for a unit that is that item (a film score keyed by the film's QID), not for its songs.
Wikidata is CC0; keep to `api_rps`.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from maestro.popularity import record_mention

SOURCE = "wikidata"
ENDPOINT = "https://query.wikidata.org/sparql"
CONTAINER_TYPES = {"Q11424", "Q482994", "Q4176708", "Q24862", "Q506240", "Q5398426", "Q202866", "Q226730",
                   "Q208569", "Q1259759"}  # films, albums, soundtracks, series
QUERY = """SELECT ?item ?en ?fr ?title ?links (MIN(YEAR(?date)) AS ?year) (GROUP_CONCAT(DISTINCT ?type) AS ?types) WHERE {{
  VALUES ?p {{ wdt:P175 wdt:P86 wdt:P676 }}
  ?item ?p wd:{qid} ; wikibase:sitelinks ?links .
  FILTER(?links > 0)
  OPTIONAL {{ ?item wdt:P31 ?type }}
  OPTIONAL {{ ?item wdt:P1476 ?title }}
  OPTIONAL {{ ?item rdfs:label ?en FILTER(LANG(?en) = "en") }}
  OPTIONAL {{ ?item rdfs:label ?fr FILTER(LANG(?fr) = "fr") }}
  OPTIONAL {{ ?item wdt:P577 ?date }}
}} GROUP BY ?item ?en ?fr ?title ?links"""


def articles_url(qid: str) -> str:
    return f"{ENDPOINT}?{urlencode({'format': 'json', 'query': QUERY.format(qid=qid)})}"


def parse(data: dict) -> list[dict]:
    items: dict[str, dict] = {}
    for b in data.get("results", {}).get("bindings", []):
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        titles = [b[k]["value"] for k in ("title", "en", "fr") if k in b]
        types = {t.rsplit("/", 1)[-1] for t in b.get("types", {}).get("value", "").split()}
        it = items.setdefault(qid, {"qid": qid, "titles": [], "links": int(b["links"]["value"]),
                                    "year": int(b["year"]["value"]) if "year" in b else None,
                                    "container": bool(types & CONTAINER_TYPES)})
        for t in titles:
            if t not in it["titles"]:
                it["titles"].append(t)
    return sorted((i for i in items.values() if i["titles"]), key=lambda i: i["qid"])


def extract(ctx) -> None:
    qid = ctx.store.pack.data["identity"]["ids"]["wikidata"]
    query = parse_qs(urlsplit(ctx.page.url).query).get("query", [""])[0]
    if not re.search(rf"wd:{qid}\b", query):
        raise ValueError(f"wikidata_articles expects the query for {qid}: {ctx.page.url}")
    for it in parse(json.loads(ctx.page.text)):
        n = it["links"]
        record_mention(ctx, SOURCE, "wikipedia_articles", it["titles"][0], strength=n, year=it["year"],
                       note=f"{n} encyclopedia article{'s' if n != 1 else ''} (Wikidata {it['qid']})",
                       extractor="wikidata_articles", qid=it["qid"], alt_titles=it["titles"][1:],
                       kind="container" if it["container"] else None,
                       url=f"https://www.wikidata.org/wiki/{it['qid']}")
