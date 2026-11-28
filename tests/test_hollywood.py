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


def test_song_list_dash_singers_dubbed_voice_and_section_credit():
    s = wfs.parse_list_item('"Wait and See" – Angela Lansbury (voice: Virginia Rees)')
    assert s["singers"] == ["Virginia Rees"] and s["on_screen"] == ["Angela Lansbury"]
    assert not s["songwriters"]
    s = wfs.parse_list_item('"If You Knew Susie" (DeSylva, Meyer) – Frank Sinatra & Gene Kelly')
    assert s["songwriters"] == ["DeSylva", "Meyer"] and s["singers"] == ["Frank Sinatra", "Gene Kelly"]
    s = wfs.parse_list_item('"Main Title" – MGM Studio and Orchestra')
    assert s["singers"] == []
    c = wfs.section_credit("The songs in The Harvey Girls were all written by Harry Warren (music) and "
                           "Johnny Mercer (lyrics):")
    assert c["composers"] == ["Harry Warren"] and c["lyricists"] == ["Johnny Mercer"]
    c = wfs.section_credit("Songs by Jule Styne and Sammy Cahn except where noted:")
    assert c["songwriters"] == ["Jule Styne", "Sammy Cahn"]
    page = ('<h2 id="Songs">Songs</h2><p>Music by Harry Warren, lyrics by Mack Gordon.</p>'
            '<ul><li>"Chattanooga Choo Choo" – Tex Beneke</li><li>"Jealousy" (Jacob Gade) – Kathryn Grayson</li></ul>')
    songs = wfs.song_lists(page)
    assert songs[0]["composers"] == ["Harry Warren"] and songs[0]["lyricists"] == ["Mack Gordon"]
    assert songs[0]["singers"] == ["Tex Beneke"]
    assert songs[1]["songwriters"] == ["Jacob Gade"] and not songs[1]["composers"]


def test_song_list_character_singers_become_actors():
    page = ('<h2 id="Cast">Cast</h2><ul><li>Gene Kelly as Gabey</li><li>Ann Miller as Claire Huddesen</li></ul>'
            '<h2 id="Musical_numbers">Musical numbers</h2><ul><li>"Prehistoric Man" – Claire, Gabey and Chip</li></ul>')
    assert wfs.song_lists(page)[0]["singers"] == ["Ann Miller", "Gene Kelly", "Chip"]


def test_song_list_parenthesised_performers_and_sung_by_lead():
    s = wfs.parse_list_item('"Happy Easter" (Fred Astaire)')
    assert s["singers"] == ["Fred Astaire"] and not s["songwriters"]
    s = wfs.parse_list_item('"I\'ll Capture Your Heart Singing" (with Fred Astaire & Virginia Dale)')
    assert s["singers"] == ["Fred Astaire", "Virginia Dale"] and not s["songwriters"]
    assert wfs.section_credit("All songs are sung by Bing Crosby unless stated. The track list is:")["songwriters"] == []


def test_song_list_western_and_colon_bullets():
    s = wfs.parse_list_item('"Be Honest With Me" (Gene Autry, Fred Rose) by Gene Autry')
    assert s["songwriters"] == ["Gene Autry", "Fred Rose"] and s["singers"] == ["Gene Autry"]
    s = wfs.parse_list_item('"In Old Capistrano" (Fred Stryker, Jerry Charleston) by Gene Autry and extras, '
                            'Composed by Manuel Esperon')
    assert s["singers"] == ["Gene Autry"] and s["composers"] == ["Manuel Esperon"]
    assert s["songwriters"] == ["Fred Stryker", "Jerry Charleston"]
    s = wfs.parse_list_item('"Sing While You Sell": Groucho, Harpo and Virginia O\'Brien')
    assert s["singers"] == ["Groucho", "Harpo", "Virginia O'Brien"]
    s = wfs.parse_list_item('"Boogie Barcarolle": A Porter number that overlays two rhythms. Astaire leads.')
    assert s["singers"] == [] and s["songwriters"] == []
    s = wfs.parse_list_item('"Happiness is a Thing Called Joe" (by Arlen and Harburg) – Ethel Waters')
    assert s["songwriters"] == ["Arlen", "Harburg"] and s["singers"] == ["Ethel Waters"]


def _song(sid, film, title, md=(), lyr=(), basis=None):
    return {"id": sid, "film": film, "src": "wikipedia", "title": title, "md": list(md), "lyr": list(lyr),
            "sing": [], "basis": basis, "note": None, "part": None, "ver": None}


def test_reused_song_takes_earlier_credits():
    from maestro import filmsongs
    road = _song(1, 10, "Moonlight Becomes You", ["Jimmy Van Heusen"], ["Johnny Burke"], "song")
    waves = _song(2, 20, "Moonlight Becomes You", ["Harold Arlen"], ["Johnny Mercer"], "section")
    own = _song(3, 20, "Accentuate the Positive", ["Harold Arlen"], ["Johnny Mercer"], "section")
    other = _song(4, 30, "Moonlight Becomes You", ["Someone Else"], [], "song")  # its own writers: not the same
    by_film = {10: [road], 20: [waves, own], 30: [other]}
    filmsongs.mark_reuse(by_film, {10: 1942, 20: 1944, 30: 1950}, {10: "Road to Morocco", 20: "Here Come the Waves"})
    assert waves["reuse"]["kind"] == "reused" and waves["md"] == ["Jimmy Van Heusen"] and waves["basis"] == "earlier film"
    assert not own.get("reuse") and not other.get("reuse")


def test_article_films_url_and_short_flag():
    from maestro.extractors import wikidata_language_films as wlf
    url = wlf.article_films_url(["Intrigue (1947 film)", "Bad Luck Blackie"])
    assert "Intrigue_(1947_film)" in __import__("urllib.parse").parse.unquote(url) and "Q24862" in \
        __import__("urllib.parse").parse.unquote(url)
    data = {"results": {"bindings": [{"film": {"value": "http://www.wikidata.org/entity/Q1"}, "en": {"value": "Bad Luck Blackie"},
                                      "short": {"value": "true"}, "year": {"value": "1949"}}]}}
    assert wlf.parse(data)[0]["short"] is True


def test_modern_year_list_cast_and_crew():
    from importlib import util
    spec = util.spec_from_file_location("wy", "maestros/hollywood-films/extractors/wikipedia_year.py")
    wy = util.module_from_spec(spec); spec.loader.exec_module(wy)
    assert wy.crew_directors("Jake Johnson (director/screenplay); Jake Johnson, Anna Kendrick") == ["Jake Johnson"]
    assert wy.crew_directors("Joel Coen, Ethan Coen (directors/screenplay); Frances McDormand") == ["Joel Coen", "Ethan Coen"]
    page = ('<table class="wikitable"><tr><th colspan="2">Opening</th><th>Title</th><th>Production company</th>'
            '<th>Cast and crew</th></tr><tr><td>JANUARY</td><td>3</td><td><a href="/wiki/Self_Reliance_(film)">Self Reliance</a></td>'
            '<td>Neon / Hulu</td><td>Jake Johnson (director/screenplay); Anna Kendrick</td></tr></table>')
    f = wy.parse_year_page(page)[0]
    assert f["title"] == "Self Reliance" and f["directors"] == ["Jake Johnson"] and f["companies"] == ["Neon", "Hulu"]
