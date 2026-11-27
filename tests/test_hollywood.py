import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from maestro.extractors import wikidata_language_films as wlf
from maestro.extractors import wikipedia_film_songs as wfs

PACK = Path(__file__).resolve().parents[1] / "maestros" / "hollywood-films"


def _year_mod():
    spec = importlib.util.spec_from_file_location("hw_year", PACK / "extractors" / "wikipedia_year.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_country_films_query_names_country_years_and_classes():
    q = parse_qs(urlsplit(wlf.country_films_url("Q30", 1944, 1944)).query)["query"][0]
    assert "wdt:P495 wd:Q30" in q and '"1944-01-01"' in q and '"1945-01-01"' in q
    assert "wd:Q11424" in q and "wd:Q24862" in q and "{" + "classes}" not in q


YEAR_PAGE = """<table class="wikitable"><tr><th>Title</th><th>Director</th><th>Cast</th><th>Genre</th><th>Notes</th></tr>
<tr><td><i><a href="/wiki/Meet_Me_in_St._Louis">Meet Me in St. Louis</a></i></td><td>Vincente Minnelli</td>
<td>Judy Garland</td><td>Musical</td><td>MGM; 4 Oscar nominations</td></tr>
<tr><td><i>Lost Film</i></td><td>A. Director</td><td></td><td>Western, Comedy</td><td>based on a novel</td></tr>
</table>"""


def test_american_year_list_rows():
    films = _year_mod().parse_year_page(YEAR_PAGE)
    assert films[0]["article"] == "Meet Me in St. Louis" and films[0]["studio"] == "MGM"
    assert films[0]["genres"] == ["Musical"] and films[0]["directors"] == ["Vincente Minnelli"]
    assert films[1]["article"] is None and films[1]["genres"] == ["Western", "Comedy"] and films[1]["studio"] is None


def test_song_list_bullets():
    s = wfs.parse_list_item('"The Boy Next Door", Hugh Martin and Ralph Blane, 1944, performed by Judy Garland.')
    assert s["title"] == "The Boy Next Door" and s["songwriters"] == ["Hugh Martin", "Ralph Blane"]
    assert s["singers"] == ["Judy Garland"] and s["written"] == 1944
    s = wfs.parse_list_item('“Over the Rainbow” – music by Harold Arlen, lyrics by E. Y. Harburg; sung by Judy Garland')
    assert s["composers"] == ["Harold Arlen"] and s["lyricists"] == ["E. Y. Harburg"] and s["singers"] == ["Judy Garland"]
    assert wfs.parse_list_item("Judy Garland as Esther Smith") is None
    page = ('<h2 id="Cast">Cast</h2><ul><li>"Not a song" said someone</li></ul>'
            '<h2 id="Musical_numbers">Musical numbers</h2><ul><li>"The Trolley Song" – performed by Judy Garland</li></ul>'
            '<h2 id="Reception">Reception</h2>')
    assert [x["title"] for x in wfs.song_lists(page)] == ["The Trolley Song"]
