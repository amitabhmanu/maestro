"""Identity step: resolve a maestro's name to candidate entities with stable external IDs.

Uses the Wikidata API (no key needed). Each candidate carries the IDs the pack's
`identity.ids` block wants, plus enough description to rule namesakes in or out.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "maestro-profiler/0.1 (https://github.com/amitabhmanu/maestro)"

# Wikidata properties mapped to pack identity.ids keys.
ID_PROPERTIES = {"P434": "musicbrainz", "P214": "viaf", "P213": "isni", "P268": "bnf"}
INSTANCE_OF, HUMAN = "P31", "Q5"
BORN, DIED, OCCUPATION = "P569", "P570", "P106"

Fetch = Callable[[str], dict[str, Any]]


def http_get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


@dataclass
class Candidate:
    qid: str
    label: str
    description: str
    born: str | None = None
    died: str | None = None
    occupations: list[str] = field(default_factory=list)
    ids: dict[str, str | None] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _claim_values(entity: dict[str, Any], prop: str) -> list[Any]:
    out = []
    for c in entity.get("claims", {}).get(prop, []):
        snak = c.get("mainsnak", {})
        if snak.get("snaktype") == "value":
            out.append(snak["datavalue"]["value"])
    return out


def _year(entity: dict[str, Any], prop: str) -> str | None:
    vals = _claim_values(entity, prop)
    if not vals:
        return None
    # Wikidata time looks like "+1924-12-24T00:00:00Z"
    return vals[0]["time"].lstrip("+")[:10]


def _url(params: dict[str, str]) -> str:
    return WIKIDATA_API + "?" + urllib.parse.urlencode({**params, "format": "json"})


def identify(name: str, *, limit: int = 7, language: str = "en", fetch: Fetch = http_get_json) -> list[Candidate]:
    """Return human candidates for `name`, most likely first (Wikidata's search order)."""
    search = fetch(_url({"action": "wbsearchentities", "search": name, "language": language,
                         "type": "item", "limit": str(limit)}))
    qids = [hit["id"] for hit in search.get("search", [])]
    if not qids:
        return []
    data = fetch(_url({"action": "wbgetentities", "ids": "|".join(qids),
                       "props": "labels|descriptions|aliases|claims", "languages": language}))
    entities = data.get("entities", {})

    occupation_qids = sorted({o["id"] for q in qids for o in _claim_values(entities.get(q, {}), OCCUPATION)})
    occupation_labels: dict[str, str] = {}
    if occupation_qids:
        labels = fetch(_url({"action": "wbgetentities", "ids": "|".join(occupation_qids[:50]),
                             "props": "labels", "languages": language}))
        occupation_labels = {
            k: v.get("labels", {}).get(language, {}).get("value", k) for k, v in labels.get("entities", {}).items()
        }

    candidates = []
    for qid in qids:
        e = entities.get(qid)
        if not e or HUMAN not in [v["id"] for v in _claim_values(e, INSTANCE_OF)]:
            continue  # films, songs, places with the same name
        ids: dict[str, str | None] = {"wikidata": qid}
        for prop, key in ID_PROPERTIES.items():
            vals = _claim_values(e, prop)
            ids[key] = vals[0] if vals else None
        candidates.append(Candidate(
            qid=qid,
            label=e.get("labels", {}).get(language, {}).get("value", qid),
            description=e.get("descriptions", {}).get(language, {}).get("value", ""),
            born=_year(e, BORN),
            died=_year(e, DIED),
            occupations=[occupation_labels.get(o["id"], o["id"]) for o in _claim_values(e, OCCUPATION)],
            ids=ids,
            aliases=[a["value"] for a in e.get("aliases", {}).get(language, [])],
        ))
    return candidates


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(description="Find Wikidata candidates and external IDs for a maestro.")
    p.add_argument("name")
    p.add_argument("--language", default="en")
    args = p.parse_args(argv)
    cands = identify(args.name, language=args.language)
    json.dump([c.to_dict() for c in cands], sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0 if cands else 1


if __name__ == "__main__":
    raise SystemExit(main())
