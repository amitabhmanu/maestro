"""Core MusicBrainz extractor, against a synthetic page shaped like ws/2 release browse output."""

from pathlib import Path

from maestro.crawl.fetcher import Page
from maestro.crawl.runner import ExtractContext, load_extractor
from maestro.extractors import musicbrainz
from maestro.resolve import resolve

FIX = Path(__file__).parent / "fixtures" / "musicbrainz" / "releases.json"
RAFI = "28365249-f3fd-44db-8131-32cb170970c4"


def test_core_extractor_is_found_from_any_pack():
    from conftest import ROOT

    assert load_extractor(ROOT / "maestros" / "rafi", "musicbrainz").SOURCE == "musicbrainz"


def test_parse_keeps_only_the_maestros_tracks():
    import json

    page = musicbrainz.parse(json.loads(FIX.read_text()), RAFI)
    assert page["containers"] == {"rg-k": {"title": "Kohinoor", "year": 1961}}
    assert [(t["recording"], t["container"]) for t in page["tracks"]] == [
        ("rec-1", "rg-k"), ("rec-2", "rg-k"), ("rec-1", None), ("rec-9", None)]
    assert page["tracks"][1]["credit"] == "Lata Mangeshkar & Mohd. Rafi"


def test_extract_writes_units_and_matches_hfgk(store_for):
    from test_resolve import film, song

    s = store_for("rafi")
    hfgk_film = film(s, "hfgk_online", "400", "Kohinoor", 1960)
    hfgk_song = song(s, "hfgk_online", "401", "Madhuban Mein Radhika Nache Re, Giridhar Ki Muraliya Baje Re", hfgk_film)
    raw = s.record_raw_page("u", 200, "x", "p")
    url = musicbrainz.release_url(RAFI)
    page = Page(url=url, status=200, body=FIX.read_bytes(), sha256="x", path=FIX)
    musicbrainz.extract(ExtractContext(s, {"purpose": "spine"}, page, raw))
    assert s.query_graph("SELECT count(*) AS n FROM entity WHERE type = 'song'")[0]["n"] == 4
    # Last page: nothing more to queue.
    assert s.query_graph("SELECT count(*) AS n FROM job")[0]["n"] == 0
    resolve(s)
    rec1 = s.entity_by_xref("musicbrainz", "rec-1", "song")
    merged = s.query_graph("SELECT ref_entity_id FROM claim WHERE field = 'same_as' AND entity_id = %(e)s", {"e": rec1})
    assert [r["ref_entity_id"] for r in merged] == [hfgk_song]
    lineages = s.coverage_report()["units_per_lineage"]
    assert lineages["musicbrainz"] >= 2
    # rec-2 (only on MusicBrainz) sits in Kohinoor, which HFGK covers: it takes the film's tier.
    from maestro.completeness import completeness_report

    tiers = completeness_report(s)["tiers"]
    assert (tiers["A"]["observed"], tiers["A"]["inherited_tier"]) == (2, 1)
    assert tiers["A"]["method"] == "chapman" and tiers["unassigned"]["observed"] == 1  # rec-9, compilation only


def test_next_page_is_queued(store_for):
    import json

    s = store_for("rafi")
    data = json.loads(FIX.read_text())
    data["release-count"] = 500
    body = json.dumps(data).encode()
    raw = s.record_raw_page("u", 200, "x", "p")
    page = Page(url=musicbrainz.release_url(RAFI), status=200, body=body, sha256="x", path=FIX)
    musicbrainz.extract(ExtractContext(s, {"purpose": "spine"}, page, raw))
    assert [r["url"] for r in s.query_graph("SELECT url FROM job")] == [musicbrainz.release_url(RAFI, 2)]


def test_catalogue_numbers_and_live_flag():
    data = {"release-count": 1, "release-offset": 0, "releases": [{
        "id": "rel", "date": "1966-03", "label-info": [{"catalog-number": "LSP-3361"}, {"catalog-number": "[none]"}],
        "release-group": {"id": "rg", "secondary-types": ["Live"]},
        "media": [{"tracks": [{"title": "T", "recording": {"id": "rec", "title": "T"},
                               "artist-credit": [{"name": "A", "artist": {"id": "me"}}]}]}]}]}
    t = musicbrainz.parse(data, "me")["tracks"][0]
    assert (t["catalogues"], t["live"], t["release_year"]) == (["LSP-3361"], True, 1966)
