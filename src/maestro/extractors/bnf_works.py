"""data.bnf.fr SPARQL: works the BnF catalogue gives the maestro as creator.

Seed with `works_url(ark)` (the maestro's BnF id from the pack, e.g. 13901146z). Each work becomes a
unit (keyed `work:<ark>`) titled with its main title; a subtitle such as "Musique de film" marks a
film score (`work_kind`). BnF data is open (Licence Ouverte).
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit

SOURCE = "bnf"
ENDPOINT = "https://data.bnf.fr/sparql"
QUERY = """PREFIX dcterms: <http://purl.org/dc/terms/>
SELECT ?work ?title ?date WHERE {{
  ?work dcterms:creator <http://data.bnf.fr/ark:/12148/cb{ark}#about> .
  OPTIONAL {{ ?work dcterms:title ?title }}
  OPTIONAL {{ ?work dcterms:date ?date }}
}}"""
FILM = re.compile(r"\bmusique (?:de|du) film\b", re.I)


def works_url(ark: str) -> str:
    return f"{ENDPOINT}?{urlencode({'format': 'application/sparql-results+json', 'query': QUERY.format(ark=ark)})}"


def parse(data: dict) -> list[dict]:
    works: dict[str, dict] = {}
    for b in data.get("results", {}).get("bindings", []):
        uri = b["work"]["value"]
        ark = re.sub(r"^.*/(?:ark:/12148/|temp-work/)", "", uri).split("#")[0].strip("/")
        full = b.get("title", {}).get("value", "").strip()
        main, _, sub = full.partition(" : ")
        m = re.match(r"\d{4}", b.get("date", {}).get("value", ""))
        works.setdefault(ark, {"ark": ark, "title": main.strip() or full, "full_title": full,
                               "film": bool(FILM.search(sub)), "year": int(m.group()) if m else None})
    return sorted(works.values(), key=lambda w: w["ark"])


def extract(ctx) -> None:
    store, pack = ctx.store, ctx.store.pack
    ark = pack.data["identity"]["ids"]["bnf"]
    if f"cb{ark}#" not in parse_qs(urlsplit(ctx.page.url).query).get("query", [""])[0]:
        raise ValueError(f"bnf_works expects the works query for {ark}: {ctx.page.url}")
    maestro = store.maestro_entity()
    role = pack.data["roles"]["primary"]

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="bnf_works", extractor_version="1", raw_page_id=ctx.raw_page_id)

    for w in parse(json.loads(ctx.page.text)):
        if not w["title"]:
            continue
        uid = store.find_or_create_by_xref(pack.unit, SOURCE, f"work:{w['ark']}", w["title"])
        claim(uid, "title", w["title"])
        if w["full_title"] != w["title"]:
            claim(uid, "full_title", w["full_title"])
        if w["film"]:
            claim(uid, "work_kind", "film_score")
        if w["year"]:
            claim(uid, "year", w["year"])
        claim(uid, "credit", {"role": role}, ref=maestro)
