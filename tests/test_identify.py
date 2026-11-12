"""Identity resolver tests. Fixtures are synthetic Wikidata-shaped responses, not live data."""

from urllib.parse import parse_qs, urlparse

from maestro.profiler.identify import identify


def snak(value):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"value": value}}}


ENTITIES = {
    "Q1001": {
        "labels": {"en": {"value": "Jean Example"}},
        "descriptions": {"en": {"value": "French composer (1896-1982)"}},
        "aliases": {"en": [{"value": "Jean Exemple"}]},
        "claims": {
            "P31": [snak({"id": "Q5"})],
            "P106": [snak({"id": "Q36834"}), snak({"id": "Q486748"})],
            "P569": [snak({"time": "+1896-03-19T00:00:00Z"})],
            "P570": [snak({"time": "+1982-06-08T00:00:00Z"})],
            "P434": [snak("mbid-1234")],
            "P268": [snak("bnf-5678")],
        },
    },
    "Q1002": {  # a film with the same name: must be dropped
        "labels": {"en": {"value": "Jean Example"}},
        "descriptions": {"en": {"value": "1950 film"}},
        "claims": {"P31": [snak({"id": "Q11424"})]},
    },
    "Q1003": {  # a namesake
        "labels": {"en": {"value": "Jean Example"}},
        "descriptions": {"en": {"value": "Belgian cyclist"}},
        "claims": {"P31": [snak({"id": "Q5"})], "P106": [snak({"id": "Q2309784"})]},
    },
}
OCCUPATIONS = {"Q36834": "composer", "Q486748": "pianist", "Q2309784": "sport cyclist"}


def fake_fetch(url):
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    if q["action"] == "wbsearchentities":
        return {"search": [{"id": i} for i in ENTITIES]}
    ids = q["ids"].split("|")
    if q["props"] == "labels":
        return {"entities": {i: {"labels": {"en": {"value": OCCUPATIONS[i]}}} for i in ids}}
    return {"entities": {i: ENTITIES[i] for i in ids}}


def test_identify_keeps_humans_with_ids_and_occupations():
    cands = identify("Jean Example", fetch=fake_fetch)
    assert [c.qid for c in cands] == ["Q1001", "Q1003"]
    first = cands[0]
    assert first.born == "1896-03-19" and first.died == "1982-06-08"
    assert first.occupations == ["composer", "pianist"]
    assert first.ids == {"wikidata": "Q1001", "musicbrainz": "mbid-1234", "viaf": None, "isni": None, "bnf": "bnf-5678"}
    assert first.aliases == ["Jean Exemple"]
    assert cands[1].occupations == ["sport cyclist"]


def test_identify_with_no_hits():
    assert identify("nobody", fetch=lambda url: {"search": []}) == []
