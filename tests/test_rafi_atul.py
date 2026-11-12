"""Rafi's Atul's Song A Day extractor and the outside check of film listings."""

from pathlib import Path

from conftest import ROOT
from maestro.completeness import completeness_report
from maestro.crawl.runner import import_page, load_extractor
from maestro.resolve import resolve
from test_resolve import film, song

FIX = Path(__file__).parent / "fixtures" / "atul" / "yearwise.html"
atul = load_extractor(ROOT / "maestros" / "rafi", "atul")


def test_parse():
    rows = atul.parse(FIX.read_text())
    assert [(r["title"], r["film"], r["year"], r["non_film"]) for r in rows] == [
        ("Madhuban mein radhika naache re", "Kohinoor", 1960, False),
        ("Dil mein baji pyaar ki shehnaaiyaan", "Kohinoor", 1960, False),
        ("Kitna dukh bhulaaya tumne pyaari", None, 1960, True),
        ("Koi saagar dil ko behlaata nahin", "Kohinoor", 1960, False),
    ]


def test_atul_checks_hfgk_listings_without_counting_as_a_sighting(store_for, tmp_path):
    s = store_for("rafi")
    f = film(s, "hfgk_online", "400", "Kohinoor", 1960)
    s.upsert_claim(f, "song_count", value=2, source_id="hfgk_online")
    song(s, "hfgk_online", "401", "Madhuban Mein Radhika Nache Re, Giridhar Ki Muraliya Baje Re", f)
    song(s, "hfgk_online", "402", "Dil Men Baji Pyar Ki Shahanaiyan", f, rafi=False)
    import_page(s, "atul", "https://atulsongaday.me/list-of-songs-yearwise/", FIX, tmp_path)
    resolve(s)
    r = completeness_report(s)
    # Atul names no singers, so it is not a second sighting of Rafi's song.
    assert r["tiers"]["A"]["lineages"] == ["hfgk"]
    check = r["containers"]["outside_check"]["atulsongaday"]
    assert (check["in_listed_containers"], check["matched"], check["unmatched"]) == (3, 2, 1)
    assert check["examples"][0]["label"] == "Koi saagar dil ko behlaata nahin"
