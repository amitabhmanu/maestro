"""Wikidata Query Service: the full record of films a register already knows by QID.

`details_url(qids)` asks for one batch of films (up to ~60 QIDs a request). Each film container (found by its
`wikidata` xref; a QID the store does not know is skipped, so this never adds films) gains:

  credit          {role}, ref to a person keyed by QID: director, producer, actor (cast), screenwriter,
                  composer, lyricist, cinematographer, editor, choreographer, playback_singer
  banner          production company (name), as HFGK records it; `distributor` likewise
  release         {date, place}: each publication date (P577) with its country, when given
  box_office      {amount, unit, place?}; `budget` the same (P2130)
  award           {award, award_qid, year, status: won|nominated, to?}: awards and nominations of the film,
                  and those given to a person or song "for" it (P1686)
  based_on        {qid, label, kind}: P144 (based on) and P4969 inverse (a remake's original), the film a
                  remake retells; `remade_as` the other way round
  soundtrack      {qid, label}: the soundtrack album item (P406)
  language        each original language (P364); `country` each country of origin (P495)
  film_genre      {qid, label}: each genre (P136); `movement` each film movement (P135), e.g. parallel cinema
  broadcaster     original broadcaster or streaming service (P449), the first channel of a streaming original
  link            {site, id}: IMDb, TMDB, Letterboxd, Indiancine.ma, Rotten Tomatoes, Bollywood Hungama and the
                  Hindi Wikipedia article. IDs only: those sites are never fetched.

Wikidata is CC0.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit

SOURCE = "wikidata"
ENDPOINT = "https://query.wikidata.org/sparql"
VERSION = "2"

ITEM_ROLES = {
    "P57": "director", "P162": "producer", "P161": "actor", "P58": "screenwriter", "P86": "composer",
    "P676": "lyricist", "P344": "cinematographer", "P1040": "editor", "P1809": "choreographer",
}
ITEM_FIELDS = {"P272": "banner", "P750": "distributor", "P364": "language", "P495": "country",
               "P406": "soundtrack", "P144": "based_on", "P4969": "remade_as",
               "P136": "film_genre", "P135": "movement", "P449": "broadcaster"}
LINKS = {"P345": "imdb", "P4947": "tmdb", "P6127": "letterboxd", "P5987": "indiancine", "P1258": "rottentomatoes",
         "P8674": "bollywoodhungama"}
QUANTITIES = {"P2142": "box_office", "P2130": "budget"}
AWARDS = {"P166": "won", "P1411": "nominated"}
PROPS = [*ITEM_ROLES, *ITEM_FIELDS, *LINKS, *QUANTITIES, *AWARDS, "P577"]

_BLOCK_ITEM = """{{ ?film wdt:{p} ?v . BIND(wd:{p} AS ?prop) OPTIONAL {{ ?v rdfs:label ?vl FILTER(LANG(?vl) = "en") }} }}"""
_BLOCK_ID = """{{ ?film wdt:{p} ?v . BIND(wd:{p} AS ?prop) }}"""


def _blocks() -> str:
    blocks = [_BLOCK_ITEM.format(p=p) for p in [*ITEM_ROLES, *ITEM_FIELDS]]
    blocks += [_BLOCK_ID.format(p=p) for p in LINKS]
    blocks += [f"""{{ ?film p:{p} ?st . ?st psv:{p} ?vn . ?vn wikibase:quantityAmount ?amount ; wikibase:quantityUnit ?unit .
      BIND(wd:{p} AS ?prop) OPTIONAL {{ ?st pq:P585 ?when }} }}""" for p in QUANTITIES]
    blocks += [f"""{{ ?film p:{p} ?st . ?st ps:{p} ?v . BIND(wd:{p} AS ?prop) OPTIONAL {{ ?st pq:P585 ?when }}
      OPTIONAL {{ ?v rdfs:label ?vl FILTER(LANG(?vl) = "en") }} }}""" for p in AWARDS]
    blocks += [f"""{{ ?to p:{p} ?st . ?st pq:P1686 ?film ; ps:{p} ?v . BIND(wd:{p} AS ?prop) OPTIONAL {{ ?st pq:P585 ?when }}
      OPTIONAL {{ ?v rdfs:label ?vl FILTER(LANG(?vl) = "en") }} OPTIONAL {{ ?to rdfs:label ?tol FILTER(LANG(?tol) = "en") }} }}"""
               for p in AWARDS]
    blocks.append("""{ ?film p:P577 ?st . ?st ps:P577 ?v . BIND(wd:P577 AS ?prop)
      OPTIONAL { ?st psv:P577 ?tv . ?tv wikibase:timePrecision ?prec }
      OPTIONAL { ?st pq:P291 ?place . OPTIONAL { ?place rdfs:label ?placel FILTER(LANG(?placel) = "en") } } }""")
    blocks.append("""{ ?other wdt:P144 ?film . BIND(wd:P4969 AS ?prop) BIND(?other AS ?v)
      OPTIONAL { ?other rdfs:label ?vl FILTER(LANG(?vl) = "en") } }""")
    blocks.append("""{ ?art schema:about ?film ; schema:isPartOf <https://hi.wikipedia.org/> ; schema:name ?v .
      BIND(wd:P0 AS ?prop) }""")
    return "\n  UNION ".join(blocks)


QUERY = """SELECT ?film ?prop ?v ?vl ?amount ?unit ?when ?prec ?place ?placel ?to ?tol WHERE {{
  VALUES ?film {{ {films} }}
  {blocks}
}}"""


def details_url(qids: list[str]) -> str:
    films = " ".join(f"wd:{q}" for q in qids)
    query = QUERY.format(films=films, blocks=_blocks())
    return f"{ENDPOINT}?{urlencode({'format': 'json', 'query': query})}"


def _qid(uri: str | None) -> str | None:
    if uri and "/entity/" in uri:
        return uri.rsplit("/", 1)[-1]
    return None


def _year(when: str | None) -> int | None:
    m = re.match(r"-?(\d{4})", when or "")
    return int(m.group(1)) if m else None


def parse(data: dict) -> dict[str, dict]:
    """Bindings to {film qid: {"credits": [(role, qid, label)], "fields": [(field, value)]}}."""
    out: dict[str, dict] = {}
    seen: set[tuple] = set()
    for b in data.get("results", {}).get("bindings", []):
        v = {k: x["value"] for k, x in b.items()}
        film, prop = _qid(v.get("film")), _qid(v.get("prop"))
        if not film or not prop:
            continue
        rec = out.setdefault(film, {"credits": [], "fields": []})
        val, label = v.get("v", ""), v.get("vl")
        vq = _qid(val)
        to = _qid(v.get("to"))
        if to:  # an award given to a person (or song) for this film
            item = (film, "award", vq, to, v.get("when"))
            if item not in seen and vq:
                seen.add(item)
                rec["fields"].append(("award", {"award": label or vq, "award_qid": vq, "year": _year(v.get("when")),
                                                "status": AWARDS.get(prop, "won"), "to": v.get("tol") or to,
                                                "to_qid": to}))
            continue
        key = (film, prop, val, v.get("when"), v.get("place"), v.get("amount"))
        if key in seen:
            continue
        seen.add(key)
        if prop in ITEM_ROLES and vq:
            rec["credits"].append((ITEM_ROLES[prop], vq, label or vq))
        elif prop in ("P364", "P495", "P272", "P750", "P449"):
            rec["fields"].append((ITEM_FIELDS[prop], label or vq or val))
        elif prop in ("P406", "P144", "P4969", "P136", "P135") and vq:
            field = ITEM_FIELDS[prop]
            rec["fields"].append((field, {"qid": vq, "label": label or vq}))
        elif prop in LINKS:
            rec["fields"].append(("link", {"site": LINKS[prop], "id": val}))
        elif prop == "P0":
            rec["fields"].append(("link", {"site": "hiwiki", "id": val}))
        elif prop in QUANTITIES and v.get("amount"):
            amount = float(v["amount"])
            q = {"amount": int(amount) if amount.is_integer() else amount, "unit": _qid(v.get("unit")) or "",
                 "year": _year(v.get("when"))}
            if v.get("placel"):
                q["place"] = v["placel"]
            rec["fields"].append((QUANTITIES[prop], q))
        elif prop in AWARDS and vq:
            rec["fields"].append(("award", {"award": label or vq, "award_qid": vq, "year": _year(v.get("when")),
                                            "status": AWARDS[prop]}))
        elif prop == "P577" and val:
            # Keep only what the date's precision says: 9 = year, 10 = month, 11 = day.
            prec = int(v.get("prec") or 11)
            r = {"date": val.lstrip("+")[:4 if prec <= 9 else 7 if prec == 10 else 10]}
            if v.get("placel"):
                r["place"] = v["placel"]
            rec["fields"].append(("release", r))
    return out


UNITS = {"Q80031": "INR", "Q4917": "USD", "Q4916": "EUR", "Q25224": "GBP"}


def extract(ctx) -> None:
    store = ctx.store
    query = parse_qs(urlsplit(ctx.page.url).query).get("query", [""])[0]
    if "VALUES ?film" not in query:
        raise ValueError(f"wikidata_film_details expects a details_url() query: {ctx.page.url}")

    def claim(eid, field, value=None, ref=None):
        store.upsert_claim(eid, field, value=value, ref_entity_id=ref, source_id=SOURCE,
                           extractor="wikidata_film_details", extractor_version=VERSION, raw_page_id=ctx.raw_page_id)

    for qid, rec in parse(json.loads(ctx.page.text)).items():
        fid = store.entity_by_xref(SOURCE, qid, "container")
        if fid is None:
            continue  # only films the register has
        for role, pq, label in rec["credits"]:
            pid = store.find_or_create_by_xref("person", SOURCE, pq, label)
            claim(fid, "credit", {"role": role}, ref=pid)
        for field, value in rec["fields"]:
            if isinstance(value, dict) and value.get("unit") in UNITS:
                value = {**value, "unit": UNITS[value["unit"]]}
            claim(fid, field, value)
