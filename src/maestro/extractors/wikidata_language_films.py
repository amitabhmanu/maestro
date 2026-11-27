"""Wikidata Query Service: every film in one original language (P364), or of one country of origin (P495)
in a span of years, for a film register.

Seed with `films_url("Q1568")` (Hindi), or `country_films_url("Q30", 1940, 1949)` (American films first
released in the 1940s; short films are left out, a register lists feature films). Each film becomes a container keyed by its QID (xref on
source `wikidata`), with its English and native labels, first publication year (P577), IMDb ID (P345,
kept as a reference only: IMDb itself is never fetched), English Wikipedia article, and composer
(P86) and director (P57) credits to person entities keyed by their QIDs. Wikidata is CC0.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

SOURCE = "wikidata"
ENDPOINT = "https://query.wikidata.org/sparql"
QUERY = """SELECT ?film ?en ?native (SAMPLE(?t) AS ?title) (MIN(YEAR(?date)) AS ?year)
  (SAMPLE(?imdb) AS ?imdbid) (SAMPLE(?art) AS ?enwiki)
  (GROUP_CONCAT(DISTINCT CONCAT(STRAFTER(STR(?mus), "entity/"), "|", COALESCE(?musl, "")); separator="\\t") AS ?composers)
  (GROUP_CONCAT(DISTINCT CONCAT(STRAFTER(STR(?dir), "entity/"), "|", COALESCE(?dirl, "")); separator="\\t") AS ?directors)
WHERE {{
  ?film wdt:P364 wd:{lang} ; wdt:P31/wdt:P279* wd:Q11424 .
  OPTIONAL {{ ?film wdt:P577 ?date }}
  OPTIONAL {{ ?film rdfs:label ?en FILTER(LANG(?en) = "en") }}
  OPTIONAL {{ ?film rdfs:label ?native FILTER(LANG(?native) = "{code}") }}
  OPTIONAL {{ ?film wdt:P1476 ?t }}
  OPTIONAL {{ ?film wdt:P345 ?imdb }}
  OPTIONAL {{ ?art schema:about ?film ; schema:isPartOf <https://en.wikipedia.org/> }}
  OPTIONAL {{ ?film wdt:P86 ?mus . OPTIONAL {{ ?mus rdfs:label ?musl FILTER(LANG(?musl) = "en") }} }}
  OPTIONAL {{ ?film wdt:P57 ?dir . OPTIONAL {{ ?dir rdfs:label ?dirl FILTER(LANG(?dirl) = "en") }} }}
}} GROUP BY ?film ?en ?native"""


def films_url(lang_qid: str, lang_code: str = "hi") -> str:
    return f"{ENDPOINT}?{urlencode({'format': 'json', 'query': QUERY.format(lang=lang_qid, code=lang_code)})}"


# By country the class path (P31/P279*) times out, so the film classes are named: film, feature film, silent,
# animated, musical, documentary, black-and-white and 3D film. Items also marked short film are left out.
FILM_CLASSES = ("Q11424", "Q24869", "Q226730", "Q202866", "Q842256", "Q93204", "Q20667187", "Q229390")
_COUNTRY_FILTER = """wdt:P31 ?class . VALUES ?class {{ {classes} }}
  ?film wdt:P495 wd:{country} ; wdt:P577 ?d0 .
  FILTER(?d0 >= "{start}-01-01"^^xsd:dateTime && ?d0 < "{after}-01-01"^^xsd:dateTime)
  FILTER NOT EXISTS {{ ?film wdt:P31 wd:Q24862 }}"""


def country_films_url(country_qid: str, start: int, end: int, lang_code: str = "en") -> str:
    """Films whose country of origin is `country_qid` with a publication date in start..end (a film dated in
    several years is listed in each span it touches; the register keeps its earliest year)."""
    query = QUERY.format(lang="LANG", code=lang_code).replace(
        "wdt:P364 wd:LANG ; wdt:P31/wdt:P279* wd:Q11424 .", _COUNTRY_FILTER.format(
            country=country_qid, start=start, after=end + 1, classes=" ".join("wd:" + c for c in FILM_CLASSES)))
    return f"{ENDPOINT}?{urlencode({'format': 'json', 'query': query})}"


def article_title(url: str | None) -> str | None:
    """'https://en.wikipedia.org/wiki/Pinjar_(2003_film)' -> 'Pinjar (2003 film)'."""
    if not url or "/wiki/" not in url:
        return None
    return unquote(url.split("/wiki/", 1)[1].split("#")[0]).replace("_", " ")


def _people(value: str) -> list[tuple[str, str]]:
    out = []
    for item in (value or "").split("\t"):
        qid, _, label = item.partition("|")
        if qid:
            out.append((qid, label or qid))
    return out


def parse(data: dict) -> list[dict]:
    films: dict[str, dict] = {}
    for b in data.get("results", {}).get("bindings", []):
        v = {k: x["value"] for k, x in b.items()}
        qid = v["film"].rsplit("/", 1)[-1]
        f = films.setdefault(qid, {"qid": qid, "title": None, "title_native": None, "year": None, "imdb": None,
                                   "wikipedia": None, "composers": [], "directors": []})
        f["title"] = f["title"] or v.get("en") or (v.get("title") if v.get("title", "").isascii() else None)
        f["title_native"] = f["title_native"] or v.get("native")
        if v.get("year"):
            y = int(v["year"])
            f["year"] = y if f["year"] is None else min(f["year"], y)
        f["imdb"] = f["imdb"] or v.get("imdbid")
        f["wikipedia"] = f["wikipedia"] or article_title(v.get("enwiki"))
        for key in ("composers", "directors"):
            for p in _people(v.get(key, "")):
                if p not in f[key]:
                    f[key].append(p)
    for f in films.values():
        for k in ("title", "title_native"):  # "दुलारी (1949 फ़िल्म)": a disambiguation, not the title
            if f[k]:
                f[k] = re.sub(r"\s*\([^)]*\)$", "", f[k]) or f[k]
        f["title"] = f["title"] or f["title_native"] or f["qid"]
    return sorted(films.values(), key=lambda f: int(f["qid"][1:]))


def extract(ctx) -> None:
    store = ctx.store
    query = parse_qs(urlsplit(ctx.page.url).query).get("query", [""])[0]
    if not re.search(r"wdt:P(?:364|495) wd:Q\d+", query):
        raise ValueError(f"wikidata_language_films expects a films_url() or country_films_url() query: {ctx.page.url}")

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="wikidata_language_films", extractor_version="1", raw_page_id=ctx.raw_page_id)

    for f in parse(json.loads(ctx.page.text)):
        fid = store.find_or_create_by_xref("container", SOURCE, f["qid"], f["title"])
        claim(fid, "title", f["title"])
        claim(fid, "wikidata", f["qid"])
        if f["title_native"]:
            claim(fid, "title_native", f["title_native"])
        if f["year"]:
            claim(fid, "year", f["year"])
        if f["imdb"]:
            claim(fid, "imdb", f["imdb"])
        if f["wikipedia"]:
            claim(fid, "wikipedia", f["wikipedia"])
        for key, role in (("composers", "composer"), ("directors", "director")):
            for qid, label in f[key]:
                pid = store.find_or_create_by_xref("person", SOURCE, qid, label)
                claim(fid, "credit", {"role": role}, ref=pid)
