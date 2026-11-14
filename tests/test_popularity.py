"""Popularity and gem scoring: table reading, popularity extractors, mention matching, scores, gems."""

import importlib.util
import json
from types import SimpleNamespace

from maestro.explore import snapshot
from maestro.popularity import _rank_scores, names_maestro, record_mention, score_canon, unit_summary
from maestro.wikitable import read_tables
from test_resolve import film, person, song

from conftest import ROOT


def load(slug, name):
    path = ROOT / "maestros" / slug / "extractors" / f"{name}.py"
    if not path.exists():
        path = ROOT / "src" / "maestro" / "extractors" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"t_{slug}_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SINGLES = """
<h2 id="Singles">Singles</h2><h3 id="1940s">1940s</h3>
<table class="wikitable"><tbody>
<tr><th rowspan="2">Year</th><th rowspan="2">Single (A-side, B-side)</th><th colspan="2">Peak chart positions</th><th rowspan="2">Album</th></tr>
<tr><th>US Country<sup class="reference">[6]</sup></th><th>US</th></tr>
<tr><td rowspan="2">1948</td><td>"Bouquet of Roses" b/w "Texarkana Baby"</td><td>1</td><td>13</td><td rowspan="2">Non-album tracks</td></tr>
<tr><td>"Rockin' Alone" b/w "Prisoner's Song"</td><td>—</td><td>—</td></tr>
<tr><td colspan="5">"—" denotes releases that did not chart</td></tr>
</tbody></table>
<h2 id="Charted_B-sides">Charted B-sides</h2>
<table class="wikitable"><tr><th rowspan="2">Year</th><th rowspan="2">B-side</th><th colspan="2">Peak chart positions</th><th rowspan="2">A-side single</th></tr>
<tr><th>US Country</th><th>US</th></tr>
<tr><td>1948</td><td>"Texarkana Baby"</td><td>1</td><td>18</td><td>"Bouquet of Roses"</td></tr></table>
"""

FILMFARE = """
<h2>Winners and nominees</h2><h3>1960s</h3>
<table class="wikitable"><tr><th>Year</th><th>Photos of winners</th><th>Singer</th><th>Song</th><th>Film</th><th>Ref.</th></tr>
<tr><th rowspan="3">1961 <small>(8th)</small></th><td rowspan="3"></td><td><b>Mohammed Rafi</b> ‡</td><td><b>"Chaudhvin Ka Chand"</b></td><td><i><b>Chaudhvin Ka Chand</b></i></td><td></td></tr>
<tr><td colspan="3">Other nominees were female singers</td><td></td></tr>
<tr><td>Mukesh</td><td>"Some Song"</td><td>Some Film</td><td></td></tr>
</table>
"""


def ctx(store):
    return SimpleNamespace(store=store, raw_page_id=None, page=None)


def test_read_tables_expands_spans():
    t = [x for x in read_tables(SINGLES) if x.heading == "1940s"][0]
    assert t.headings == ["Singles", "1940s"]
    assert t.header_texts() == ["Year", "Single (A-side, B-side)", "Peak chart positions US Country",
                                "Peak chart positions US", "Album"]
    body = t.body()
    assert [c.text for c in body[1]] == ["1948", '"Rockin\' Alone" b/w "Prisoner\'s Song"', "—", "—", "Non-album tracks"]


def test_arnold_singles_parse():
    mod = load("eddy-arnold", "wikipedia_singles")
    rows = mod.parse(SINGLES)
    bouquet = next(r for r in rows if r["title"] == "Bouquet of Roses")
    assert bouquet["peaks"] == {"billboard_country": (1, "US Country"), "billboard_hot100": (13, "US")}
    assert bouquet["flip"] == "Texarkana Baby" and not bouquet["b_side"]
    assert next(r for r in rows if r["title"] == "Rockin' Alone")["peaks"] == {}
    tex = next(r for r in rows if r["title"] == "Texarkana Baby")
    assert tex["b_side"] and tex["peaks"]["billboard_hot100"] == (18, "US")


def test_filmfare_parse_and_maestro_names(store_for):
    mod = load("rafi", "filmfare")
    rows = mod.parse(FILMFARE)
    assert [(r["singer"], r["song"], r["won"]) for r in rows] == [
        ("Mohammed Rafi", "Chaudhvin Ka Chand", True), ("Mukesh", "Some Song", False)]
    s = store_for("rafi")
    assert names_maestro(s, "Mohammad Rafi, Geeta Dutt") and not names_maestro(s, "Mukesh")


def test_rank_scores():
    r = _rank_scores({1: 10, 2: 5, 3: 5}, [1, 2, 3, 4])
    assert r[4] == 0 and r[1] == 0.875 and r[2] == r[3] == 0.5


def _canon(s):
    burman = person(s, "hfgk_online", "1", "S D Burman")
    other = person(s, "hfgk_online", "2", "Someone Else")
    hit_film = film(s, "hfgk_online", "10", "Pyaasa", 1957)
    s.upsert_claim(hit_film, "title", value="Pyaasa", source_id="hfgk_online")
    hit = song(s, "hfgk_online", "100", "Jinhen Naaz Hai Hind Par Vo Kahan Hain", hit_film, burman)
    side = song(s, "hfgk_online", "101", "Hum Aapki Aankhon Mein Is Dil Ko Basa Den", hit_film, burman)
    plain = [song(s, "hfgk_online", str(200 + i), f"Plain Song Number {i} Of The Canon", None, other) for i in range(30)]
    return hit, side, plain, hit_film


def test_mentions_match_and_score(store_for):
    s = store_for("rafi")
    hit, side, plain, f = _canon(s)
    c = ctx(s)
    record_mention(c, "wikipedia", "filmfare", "Jinhen Naaz Hai Hind Par", strength=2, year=1957,
                   container="Pyaasa", note="Filmfare, won")
    record_mention(c, "wikipedia", "binaca_geetmala", "Jinhen Naaz Hai Hind Par", strength=40, year=1957,
                   container="Pyaasa (film)", note="#1")
    record_mention(c, "wikipedia", "filmfare", "A Song Not In The Canon", strength=1, year=1960,
                   container="Unknown Film", note="nominated")
    record_mention(c, "wikipedia", "not_in_pack", "Jinhen Naaz Hai Hind Par", strength=1, note="x")
    s.upsert_claim(plain[0], "on_release", value="rel-1", source_id="musicbrainz")
    res = score_canon(s)
    u = res["units"]
    assert {e.signal for e in u[hit].evidence} == {"filmfare", "binaca_geetmala"}
    assert u[hit].popularity > u[plain[0]].popularity > u[plain[1]].popularity == 0
    assert res["unmatched_mentions"][0]["title"] == "A Song Not In The Canon"
    assert res["ignored_mentions"] == {"not_in_pack": 1}
    assert "youtube" in res["listed_without_data"]
    # Hit lists name 1 unit of 32: too thin to call anything forgotten, so no team or shadow gems.
    assert not res["gems"]["reach_known"] and not any(x.gem for x in u.values())
    summary = unit_summary(u[hit])
    assert summary["score"] > 80 and summary["evidence"][0]["source"] == "wikipedia"
    json.dumps(summary)


def test_gems_from_picks_and_shadow(store_for):
    s = store_for("rafi")
    hit, side, plain, f = _canon(s)
    c = ctx(s)
    # Deep enough hit lists: the hit plus one plain song charted (2 of 32 units).
    record_mention(c, "wikipedia", "binaca_geetmala", "Jinhen Naaz Hai Hind Par", strength=40, container="Pyaasa", note="#1")
    record_mention(c, "wikipedia", "binaca_geetmala", "Plain Song Number 3 Of The Canon", strength=5, note="#36")
    s.upsert_claim(plain[5], "pick", value=True, source_id="user", kind="decided")
    res = score_canon(s)
    u = res["units"]
    assert res["gems"]["reach_known"] and res["gems"]["hit_list_years"] == [1957, 1957]
    # A hit beside it makes a lead to listen to, not a gem: only experts and picks speak for quality.
    assert not u[side].gem and u[side].lead and any(r.startswith("sibling:") for r in u[side].lead_reasons)
    assert u[plain[5]].gem and u[plain[5]].gem_reasons[0] == "your pick"
    assert not u[hit].gem


def test_gems_come_from_expert_opinion(store_for):
    s = store_for("rafi")
    hit, side, plain, f = _canon(s)
    other = person(s, "hfgk_online", "3", "A Third Composer")
    rare = song(s, "hfgk_online", "301", "Koi Sagar Dil Ko Behlata Nahin Ab", None, other)
    listed = song(s, "hfgk_online", "302", "Tasveer Banata Hoon Teri Khoon E Jigar Se", None, other)
    vetoed = song(s, "hfgk_online", "303", "Mujhe Duniya Walo Sharabi Na Samjho", None, other)
    # A bigger canon, so that a voice naming four units is a choosy one.
    for i in range(400):
        song(s, "hfgk_online", str(1000 + i), f"Filler Song {i} Nobody Wrote About", None, other)
    c = ctx(s)
    record_mention(c, "songsofyore", "songsofyore", "Koi Sagar Dil Ko Behlata Nahin Ab", strength=1,
                   note="a less heard gem", expert="AK", framing="gem")
    record_mention(c, "songsofyore", "songsofyore", "Tasveer Banata Hoon Teri Khoon E Jigar Se", strength=1,
                   note="in a best-of list", expert="AK", framing="listed")
    # An expert's word on a hit does not make it a gem: its reach is too high.
    record_mention(c, "wikipedia", "binaca_geetmala", "Jinhen Naaz Hai Hind Par", strength=40, container="Pyaasa", note="#1")
    record_mention(c, "songsofyore", "songsofyore", "Jinhen Naaz Hai Hind Par", strength=1, container="Pyaasa",
                   note="a masterpiece", expert="AK", framing="gem")
    s.upsert_claim(vetoed, "pick", value=False, source_id="user", kind="decided")
    record_mention(c, "songsofyore", "songsofyore", "Mujhe Duniya Walo Sharabi Na Samjho", strength=1,
                   note="underrated", expert="AK", framing="gem")
    res = score_canon(s)
    u = res["units"]
    assert u[rare].gem and u[rare].gem_reasons == ["a less heard gem"]
    assert u[rare].quality > u[listed].quality > 0 and not u[listed].gem
    assert u[hit].quality > 0.6 and not u[hit].gem
    assert u[vetoed].quality > 0.6 and not u[vetoed].gem  # the user said it is not a gem
    assert res["gems"]["voices"]["AK"]["units"] == 4


def test_snapshot_carries_popularity(store_for):
    s = store_for("rafi")
    hit, side, plain, f = _canon(s)
    record_mention(ctx(s), "wikipedia", "filmfare", "Jinhen Naaz Hai Hind Par", strength=2, container="Pyaasa", note="won")
    snap = snapshot(s)
    units = {x["id"]: x for x in snap["units"]}
    assert units[hit]["pop"]["evidence"][0]["note"] == "won"
    assert "pop" not in units[plain[1]]
    assert snap["popularity"]["signals"]["filmfare"]["units"] == 1


def test_wikidata_articles_parse():
    mod = load("rafi", "wikidata_articles")
    data = {"results": {"bindings": [
        {"item": {"value": "http://www.wikidata.org/entity/Q1"}, "en": {"value": "Song One"}, "links": {"value": "3"},
         "types": {"value": "http://www.wikidata.org/entity/Q7366"}},
        {"item": {"value": "http://www.wikidata.org/entity/Q2"}, "en": {"value": "A Film"}, "links": {"value": "9"},
         "year": {"value": "1960"}, "types": {"value": "http://www.wikidata.org/entity/Q11424"}}]}}
    items = {i["qid"]: i for i in mod.parse(data)}
    assert items["Q1"]["links"] == 3 and not items["Q1"]["container"]
    assert items["Q2"]["container"] and items["Q2"]["year"] == 1960


BINACA_PAGE = """<meta itemprop="name" content="Binaca Geetmala Annual List for Year 1977 - Page 1 of 5"/>
<a href="/geetmala/binaca_geetmala_1953.php">1953</a><a href="/geetmala/binaca_geetmala_1977.php?page=2">2</a>
<table class="w760">
<tr itemprop="track" itemscope itemtype="https://schema.org/MusicRecording"><td class="w760"><table class="b1"><tr><td rowspan="2"><a href="/song/x.htm"><img alt="x"></a><br><span class="song">2</span></td>
<td rowspan="2"><a href="/song/x.htm"  itemprop="url"><span itemprop="name">Shabaab Pe Main, Pardaa Hai Pardaa</span></a></td>
<td class="w150"><span itemprop="byArtist"><a itemprop="url" href="/singer/mohammed_rafi.php"><span itemprop="name">Mohammed Rafi</span></a></span></td>
<td class="w150"><span itemprop="byArtist"><a itemprop="url" href="/music_director/laxmikant_pyarelal.php"><span itemprop="name">Laxmikant Pyarelal</span></a></span></td>
<td class="w150"></td></tr>
<tr><td class="w150 bgee"><span itemprop="inAlbum"><a itemprop="url" href="/movie/amar_akbar_anthony_1977.htm"><span itemprop="name">Amar Akbar Anthony</span> (1977)</a></span></td>
<td></td><td></td></tr></table></td></tr>
</table>"""


def test_hindigeetmala_binaca_parse():
    d = load("rafi", "hindigeetmala_binaca").parse(BINACA_PAGE)
    assert d["year"] == 1977 and d["pages"] == [2] and d["years"] == [1953]
    assert d["songs"] == [{"rank": 2, "title": "Shabaab Pe Main, Pardaa Hai Pardaa", "singers": ["Mohammed Rafi"],
                           "composers": ["Laxmikant Pyarelal"], "film": "Amar Akbar Anthony", "film_year": 1977}]


def test_expert_post_lines():
    p = load("rafi", "songsofyore").parse_line
    assert p("1. Ishq deewana husn bhi ghayal – Sanghursh (1968) – Lyrics: Shakeel Badayuni")["film"] == "Sanghursh"
    assert p("2. Tasveer banata hun teri – Deewana – 1952 – Naushad – Shakeel Badayuni")["year"] == 1952
    assert p("3. Music Composer S Mohindar: Hazaaron rang badlega zamana from the film Shiri Farhad (1956), lyrics")["title"] \
        == "Hazaaron rang badlega zamana"
    assert p("4. Hue hum jinke liye barbaad from Deedar (1951), lyrics Shakeel Badayuni")["film"] == "Deedar"
    assert p("5. 1946 – Kah ke bhi na aaye tum – Safar – C Ramachandra – G.S Nepali")["title"] == "Kah ke bhi na aaye tum"
    assert p("6. Hai kali-kali ke labh par (Lala Rukh, 1957): Lotan. Based on a poem")["film"] == "Lala Rukh"
    assert p("Not a numbered line") is None
