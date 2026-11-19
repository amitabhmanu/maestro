"""Film register enrichment: Wikidata film details, Wikipedia soundtrack songs, the raga list, HFGK cast
and censor dates, song roles and songs reused from earlier films."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from maestro import films, filmsongs
from maestro.extractors import hfgk, wikidata_film_details as wfd, wikipedia_film_songs as wfs

ROOT = Path(__file__).resolve().parent.parent


def _ctx(store, url, text):
    queued = []
    page = SimpleNamespace(url=url, text=text)
    return SimpleNamespace(store=store, page=page, raw_page_id=None, options={},
                           enqueue=lambda *a, **k: queued.append(a) or {}), queued


def _b(**kw):
    return {k: {"value": v} for k, v in kw.items()}


E = "http://www.wikidata.org/entity/"


def test_wikidata_details_parse_and_extract(store_for):
    data = {"results": {"bindings": [
        _b(film=E + "Q1", prop=E + "P57", v=E + "Q10", vl="Raj Kapoor"),
        _b(film=E + "Q1", prop=E + "P161", v=E + "Q11", vl="Nargis"),
        _b(film=E + "Q1", prop=E + "P162", v=E + "Q10", vl="Raj Kapoor"),
        _b(film=E + "Q1", prop=E + "P272", v=E + "Q12", vl="R. K. Films"),
        _b(film=E + "Q1", prop=E + "P4947", v="123"),
        _b(film=E + "Q1", prop=E + "P0", v="बरसात (1949 फ़िल्म)"),
        _b(film=E + "Q1", prop=E + "P2142", amount="+12000000", unit=E + "Q80031"),
        _b(film=E + "Q1", prop=E + "P166", v=E + "Q20", vl="Filmfare Award for Best Film", when="1950-01-01T00:00:00Z"),
        _b(film=E + "Q1", prop=E + "P1411", v=E + "Q21", vl="Filmfare Award for Best Music Director",
           to=E + "Q30", tol="Shankar–Jaikishan"),
        _b(film=E + "Q1", prop=E + "P577", v="+1949-04-21T00:00:00Z", prec="11", placel="India"),
        _b(film=E + "Q1", prop=E + "P577", v="+1949-01-01T00:00:00Z", prec="9"),
        _b(film=E + "Q1", prop=E + "P4969", v=E + "Q2", vl="Barsaat Remake"),
        _b(film=E + "Q9", prop=E + "P57", v=E + "Q10", vl="Raj Kapoor"),  # not in the register
    ]}}
    parsed = wfd.parse(data)
    q1 = parsed["Q1"]
    assert ("director", "Q10", "Raj Kapoor") in q1["credits"] and ("actor", "Q11", "Nargis") in q1["credits"]
    fields = dict((k, v) for k, v in q1["fields"] if k not in ("link", "release", "award"))
    assert fields["banner"] == "R. K. Films" and fields["box_office"]["amount"] == 12000000
    assert {"date": "1949-04-21", "place": "India"} in [v for k, v in q1["fields"] if k == "release"]
    assert {"date": "1949"} in [v for k, v in q1["fields"] if k == "release"]
    awards = [v for k, v in q1["fields"] if k == "award"]
    assert any(a["status"] == "nominated" and a["to"] == "Shankar–Jaikishan" for a in awards)
    assert any(a["status"] == "won" and a["year"] == 1950 for a in awards)
    assert {"site": "hiwiki", "id": "बरसात (1949 फ़िल्म)"} in [v for k, v in q1["fields"] if k == "link"]
    assert "Q1" in wfd.details_url(["Q1", "Q2"]) or "wd%3AQ1" in wfd.details_url(["Q1", "Q2"])

    s = store_for("hindi-films")
    fid = s.find_or_create_by_xref("container", "wikidata", "Q1", "Barsaat")
    s.upsert_claim(fid, "wikidata", value="Q1", source_id="wikidata")
    ctx, _ = _ctx(s, wfd.details_url(["Q1", "Q9"]), json.dumps(data))
    wfd.extract(ctx)
    assert s.entity_by_xref("wikidata", "Q9", "container") is None  # never adds films
    got = {r["field"] for r in s.conn.execute("SELECT field FROM claim WHERE entity_id = %s", (fid,)).fetchall()}
    assert {"credit", "banner", "box_office", "award", "release", "link", "remade_as"} <= got
    unit = s.conn.execute("SELECT value FROM claim WHERE entity_id = %s AND field = 'box_office'", (fid,)).fetchone()
    assert unit["value"]["unit"] == "INR"
    rec = films.load_films(s)[fid]
    d = films.film_details(films.Film(fid, [rec]))
    assert d["released"] == "1949-04-21" and d["prod"] == ["Raj Kapoor"] and d["links"]["tmdb"] == "123"


TRACKS = """<html><body>
<div class="mw-heading mw-heading2"><h2 id="Soundtrack">Soundtrack</h2></div>
<div role="note" class="hatnote">Main article: <a href="https://en.wikipedia.org/wiki/Film_X_(soundtrack)">Film X (soundtrack)</a></div>
<div class="track-listing"><p>All lyrics are written by Javed Akhtar; all music is composed by Shankar–Ehsaan–Loy.</p>
<table class="tracklist"><tbody>
<tr><th>No.</th><th>Title</th><th>Singer(s)</th><th>Length</th></tr>
<tr><th>1.</th><td>"Koi Kahe"</td><td>Shankar Mahadevan, Shaan &amp; Kavita Krishnamurthy</td><td>5:11</td></tr>
<tr><th>2.</th><td>"Koi Kahe" (Reprise)</td><td>Udit Narayan</td><td>2:01</td></tr>
<tr><th>3.</th><td>"Tamma Tamma" (Recreated)</td><td>Badshah, Chorus</td><td>3:00</td></tr>
<tr class="tracklist-total-length"><th colspan="3">Total length:</th><td>10:12</td></tr>
</tbody></table></div>
<div class="mw-heading mw-heading2"><h2 id="Release">Release</h2></div>
<table class="wikitable"><tr><th>Song</th><th>Singer</th></tr><tr><td>Not music</td><td>X</td></tr></table>
</body></html>"""


def test_wikipedia_film_songs_parse():
    songs = wfs.parse_songs(TRACKS)
    assert [s["title"] for s in songs] == ["Koi Kahe", "Koi Kahe (Reprise)", "Tamma Tamma (Recreated)"]
    assert songs[0]["singers"] == ["Shankar Mahadevan", "Shaan", "Kavita Krishnamurthy"]
    assert songs[0]["lyricists"] == ["Javed Akhtar"] and songs[0]["composers"] == ["Shankar–Ehsaan–Loy"]
    assert songs[1]["version"] == "reprise" and songs[0]["duration"] == "5:11"
    assert wfs.soundtrack_articles(TRACKS) == ["Film X (soundtrack)"]
    assert wfs.film_url("Film X (soundtrack)") == "https://en.wikipedia.org/wiki/Film_X_(soundtrack)"


def test_wikipedia_film_songs_extract(store_for):
    s = store_for("hindi-films")
    fid = s.find_or_create_by_xref("container", "wikidata", "Q5", "Film X")
    s.upsert_claim(fid, "wikipedia", value="Film X", source_id="wikidata")
    s.upsert_claim(fid, "year", value=2017, source_id="wikidata")
    ctx, queued = _ctx(s, "https://en.wikipedia.org/wiki/Film_X", TRACKS)
    wfs.extract(ctx)
    assert queued and queued[0][0].endswith("Film_X_(soundtrack)")
    by = filmsongs.load_songs(s)
    got = by[fid]
    assert len(got) == 3 and got[0]["sing"][0] == "Shankar Mahadevan" and got[1]["ver"] == "reprise"
    tiers = {r["value"] for r in s.conn.execute("SELECT value FROM claim WHERE field = 'tier'").fetchall()}
    assert tiers == {"B"}
    # The soundtrack page: found through the film's soundtrack_article, with its album infobox.
    page = TRACKS.replace("<html><body>", '<html><body><table class="infobox"><tr><th>Label</th><td>T-Series</td></tr>'
                                          '<tr><th>Released</th><td>1 May 2017</td></tr></table>')
    ctx, _ = _ctx(s, "https://en.wikipedia.org/wiki/Film_X_(soundtrack)", page)
    wfs.extract(ctx)
    label = s.conn.execute("SELECT value FROM claim WHERE entity_id = %s AND field = 'record_company'", (fid,)).fetchone()
    assert label["value"] == "T-Series"


def test_song_roles_and_reuse():
    assert filmsongs.roles(["Lata Mangeshkar"]) == {"kind": "solo", "chorus": False}
    assert filmsongs.roles(["Lata Mangeshkar", "Mukesh", "Others"]) == {"kind": "duet", "chorus": True}
    assert filmsongs.roles(["Chorus"]) == {"kind": "chorus", "chorus": True}

    def song(i, film, title, md, sing, src="hfgk_online", **kw):
        return {"id": i, "film": film, "title": title, "md": md, "sing": sing, "src": src, "ver": None,
                "note": None, "part": None, **kw}
    by = {
        1: [song(1, 1, "Yad A Raha Hai Dil Ko Bhula Hua Zamana", ["Husnlal-Bhagatram"], ["Suraiya"])],
        2: [song(2, 2, "Yaad Aa Raha Hai Dil Ko Bhula Hua", ["Husnlal-Bhagatram"], ["Suraiya"]),
            song(3, 2, "Yad A Raha Hai Dil Ko, Other Words", ["Naushad"], ["Suraiya"])],
        3: [song(4, 3, "Tamma Tamma Again", ["Tanishk Bagchi"], ["Badshah"], src="wikipedia",
                 ver="recreated"),
            song(5, 3, "Challa (Remix)", ["Vishal Bhardwaj"], ["Rabbi"], src="wikipedia", ver="remix")],
    }
    n = filmsongs.mark_reuse(by, {1: 1949, 2: 1955, 3: 2017}, {1: "Amar Kahani", 2: "Kanchan", 3: "Badrinath"})
    assert by[2][0]["reuse"]["kind"] == "reused" and by[2][0]["reuse"]["title"] == "Amar Kahani"
    assert "reuse" not in by[2][1]  # another music director: a new song
    assert by[3][0]["reuse"]["kind"] == "recreated"
    assert "reuse" not in by[3][1] and not filmsongs.is_new_composition(by[3][1])  # a remix of the film's own song
    assert n == 2 and not filmsongs.is_new_composition(by[2][0]) and filmsongs.is_new_composition(by[1][0])


def test_hfgk_cast_and_censor():
    page = (ROOT / "tests" / "fixtures" / "films" / "hfgk_film_cast.html")
    if not page.exists():
        page.write_text("""<h1>Tilismi Khanjar</h1><div>तिलस्मी ख़ंजर</div>
<div class="detail-box" style="margin-bottom:1rem;">
    <h3 style="font-size:1rem;margin-bottom:.7rem;">Artists</h3>
    <div style="display:flex;">
      <a href="person.php?id=4753" style="x"><span>Tasneem</span>
        </a>
      <a href="person.php?id=308" style="x"><span>Allauddin</span>
        </a>
          </div>
  </div>
<div class="detail-box" style="margin-bottom:1rem;">
    <h3 style="font-size:1rem;margin-bottom:.7rem;">Film Censor Info</h3>
    <p style="font-size:.88rem;">
      Cens No=Bombay/41745(cut) Len (ft)=10500 Reels=10 Time(mts)=116m40s Date=18.04.1949    </p>
  </div>
""", "utf-8")
    d = hfgk.parse_film(page.read_text("utf-8"))
    assert [c["name"] for c in d["cast"]] == ["Tasneem", "Allauddin"] and d["crew"] == []
    assert d["censor"] == {"number": "Bombay/41745(cut)", "length_ft": 10500, "reels": 10, "duration": "116m40s",
                           "date": "1949-04-18"}


def _pack_extractor(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "maestros" / "hindi-films" / "extractors" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_raga_list(store_for):
    ragas = _pack_extractor("wikipedia_ragas")
    page = """<table class="wikitable"><tr><th>Rāg</th><th>Song</th><th>Film</th><th>Music composer</th>
<th>Singer(s)</th><th>Lang</th></tr>
<tr><td>Bhairavi</td><td>"Tute Na Dil Tute Na"</td><td><a href="/wiki/Andaz_(1949_film)">Andaz</a></td><td>Naushad</td><td>Mukesh</td><td>Hindi</td></tr>
<tr><td>Kalyani</td><td>Something</td><td><a href="/wiki/Telugu_Film">X</a></td><td>Y</td><td>Z</td><td>Telugu</td></tr>
<tr><td>Yaman</td><td>"Missing Song"</td><td><a href="/wiki/Andaz_(1949_film)">Andaz</a></td><td>Naushad</td><td>Lata</td><td>Hindi</td></tr>
</table>"""
    rows = ragas.parse(page)
    assert [r["raga"] for r in rows] == ["Bhairavi", "Yaman"] and rows[0]["article"] == "Andaz (1949 film)"
    s = store_for("hindi-films")
    wd = s.find_or_create_by_xref("container", "wikidata", "Q7", "Andaz")
    s.upsert_claim(wd, "wikipedia", value="Andaz (1949 film)", source_id="wikidata")
    hf = s.find_or_create_by_xref("container", "hfgk_online", "700", "Andaz")
    s.merge_entities(wd, hf, "test")
    song = s.find_or_create_by_xref("song", "hfgk_online", "7001", "Toote Na Dil Toote Na")
    s.upsert_claim(song, "title", value="Toote Na Dil Toote Na", source_id="hfgk_online")
    s.upsert_claim(song, "part_of", value={"position": 1}, ref_entity_id=hf, source_id="hfgk_online")
    ctx, _ = _ctx(s, "https://en.wikipedia.org/wiki/List_of_film_songs_based_on_ragas", page)
    ragas.extract(ctx)
    ragas.extract(ctx)  # read whole again: the unmatched row is not kept twice
    assert s.conn.execute("SELECT value FROM claim WHERE entity_id = %s AND field = 'raga'", (song,)).fetchone()["value"] == "Bhairavi"
    gaps = s.conn.execute("SELECT count(*) AS n FROM claim WHERE field = 'listing_gap' AND status = 'active'").fetchone()
    assert gaps["n"] == 1
