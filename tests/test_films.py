"""The film register: HFGK year pages, Wikipedia year lists, Wikidata films, matching and the report."""

import csv
import importlib.util
import json
from pathlib import Path

from conftest import ROOT
from maestro import films
from maestro.crawl.fetcher import Page
from maestro.crawl.runner import ExtractContext
from maestro.extractors import hfgk, wikidata_language_films as wlf

FIX = Path(__file__).parent / "fixtures" / "films"
spec = importlib.util.spec_from_file_location(
    "wikipedia_year", ROOT / "maestros" / "hindi-films" / "extractors" / "wikipedia_year.py")
wikipedia_year = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wikipedia_year)


def page(url, body):
    return Page(url=url, status=200, body=body.encode() if isinstance(body, str) else body, sha256="x", path=Path("p"))


def test_hfgk_year_list_and_index():
    text = (FIX / "hfgk_year.html").read_text("utf-8")
    assert hfgk.parse_years(text) == ["1949", "1950", "Fifties / पचास का दशक"]
    got = hfgk.parse_year_list(text)
    assert [(f["id"], f["year"], f["title"], f["title_hi"]) for f in got] == [
        ("21", 1950, "Film Twenty One", "फ़िल्म"), ("22", 1950, "Second Film", None)]


def test_hfgk_home_page_queues_years_in_range(store_for):
    s = store_for("hindi-films")
    raw = s.record_raw_page("u", 200, "x", "p")
    text = (FIX / "hfgk_year.html").read_text("utf-8")
    hfgk.extract(ExtractContext(s, {}, page(hfgk.BASE + "index.php", text), raw, {"years": "1950-1960"}))
    assert [r["url"] for r in s.query_graph("SELECT url FROM job ORDER BY id")] == [hfgk.year_url("1950")]
    hfgk.extract(ExtractContext(s, {}, page(hfgk.year_url("1950"), text), raw))
    urls = [r["url"] for r in s.query_graph("SELECT url FROM job WHERE extractor = 'hfgk' ORDER BY id")]
    assert urls[1:] == [hfgk.BASE + "film.php?id=21", hfgk.BASE + "film.php?id=22"]


def test_hfgk_film_page_writes_every_song(store_for):
    s = store_for("hindi-films")
    raw = s.record_raw_page("u", 200, "x", "p")
    body = (Path(__file__).parent / "fixtures" / "hamraaz" / "film.html").read_bytes()
    hfgk.extract(ExtractContext(s, {}, page(hfgk.BASE + "film.php?id=11", body), raw))
    rows = s.query_graph("""SELECT e.type, count(*) AS n FROM entity e GROUP BY 1 ORDER BY 1""")
    counts = {r["type"]: r["n"] for r in rows}
    assert counts["container"] == 1 and counts["song"] == 2
    tiers = s.query_graph("SELECT value #>> '{}' AS t FROM claim WHERE field = 'tier'")
    assert {r["t"] for r in tiers} == {"A"}
    assert s.coverage_report()["tiers"]["A"]["observed"] == 2  # a register's canon is every song


def test_wikipedia_year_page():
    text = (FIX / "wikipedia_1950.html").read_text("utf-8")
    got = wikipedia_year.parse_year_page(text)
    assert [f["title"] for f in got] == ["Aahuti", "Red Film"]
    assert got[0]["article"] == "Aahuti (1950 film)" and got[1]["article"] is None
    assert got[0]["composer"] == ["D. C. Dutta"] and got[0]["lyricist"] == ["Indeevar", "Tandon"]
    assert got[1]["composer"] == ["R. C. Boral"] and got[1]["singer"] == ["K. L. Saigal", "Kanan Devi"]
    assert wikipedia_year.parse_index(text) == [1951]


def test_wikidata_parse():
    data = {"results": {"bindings": [{
        "film": {"value": "http://www.wikidata.org/entity/Q7"}, "en": {"value": "Barsaat"},
        "native": {"value": "बरसात"}, "year": {"value": "1950"}, "imdbid": {"value": "tt0041197"},
        "enwiki": {"value": "https://en.wikipedia.org/wiki/Barsaat_(1949_film)"},
        "composers": {"value": "Q1|Shankar–Jaikishan"}, "directors": {"value": "Q2|Raj Kapoor"}}]}}
    (f,) = wlf.parse(data)
    assert (f["title"], f["title_native"], f["year"], f["imdb"]) == ("Barsaat", "बरसात", 1950, "tt0041197")
    assert f["wikipedia"] == "Barsaat (1949 film)" and f["composers"] == [("Q1", "Shankar–Jaikishan")]


def test_names_agree():
    assert films.names_agree(["S. D. Burman"], ["Sachin Dev Burman"])
    assert films.names_agree(["R. D. Burman"], ["S. D. Burman"]) is False
    assert films.names_agree(["Shankar–Jaikishan"], ["Shankar Jaikishan"])
    assert films.names_agree(["Shankar - Jaikishan"], ["Jaikishan"])
    assert films.names_agree([], ["Naushad"]) is None


def _film(s, source, xref, title, year=None, composers=(), **claims):
    fid = s.find_or_create_by_xref("container", source, xref, title)
    s.upsert_claim(fid, "title", value=title, source_id=source)
    if year:
        s.upsert_claim(fid, "year", value=year, source_id=source)
    for name in composers:
        s.upsert_claim(fid, "credit_unlinked", value={"role": "composer", "name": name}, source_id=source)
    for k, v in claims.items():
        s.upsert_claim(fid, k, value=v, source_id=source)
    return fid


def _song(s, film, xref, title, singers):
    sid = s.find_or_create_by_xref("song", "hfgk_online", xref, title)
    s.upsert_claim(sid, "part_of", value={"position": 1}, ref_entity_id=film, source_id="hfgk_online")
    for n in singers:
        pid = s.find_or_create_by_xref("person", "hfgk_online", n, n)
        s.upsert_claim(sid, "credit", value={"role": "singer"}, ref_entity_id=pid, source_id="hfgk_online")


def _register(s):
    w1 = _film(s, "wikidata", "Q1", "Barsaat", 1949, ["Shankar–Jaikishan"], wikidata="Q1", imdb="tt1",
               wikipedia="Barsaat (1949 film)")
    _film(s, "wikipedia", "1949:Barsaat (1949 film)", "Barsaat", 1949, wikipedia="Barsaat (1949 film)")
    h1 = _film(s, "hfgk_online", "101", "Barsat", 1949, ["Shankar Jaikishan"], song_count=2)
    _song(s, h1, "s1", "Hawa Mein Udta Jaye", ["Lata Mangeshkar"])
    _song(s, h1, "s2", "Jiya Bekarar Hai", ["Lata Mangeshkar", "Mukesh"])
    _film(s, "wikidata", "Q2", "Pyaasa", 1957, ["S. D. Burman"], wikidata="Q2")
    _film(s, "hfgk_online", "102", "Pyasa", 1957, ["R. D. Burman"], song_count=9)        # composer differs
    _film(s, "wikidata", "Q3", "Awaara", 1951, wikidata="Q3")
    _film(s, "hfgk_online", "103", "Awara", 1952, song_count=11)                       # a year apart
    _film(s, "hfgk_online", "104", "Lost Reel", 1940, song_count=5)                    # no Wikidata item
    _film(s, "wikidata", "Q5", "Not In Hfgk", 1960, wikidata="Q5")                     # not in HFGK
    _film(s, "wikidata", "Q6", "Remake", 1962, wikidata="Q6")
    _film(s, "wikidata", "Q7", "Remake", 1962, wikidata="Q7")
    _film(s, "hfgk_online", "107", "Remake", 1962, song_count=6)                        # tie: review
    _film(s, "wikidata", "Q8", "Modern Film", 2015, wikidata="Q8")


def test_match_and_report(store_for, tmp_path):
    s = store_for("hindi-films")
    _register(s)
    stats = films.match(s)
    assert stats.by_article == 1 and stats.by_title == 3 and stats.queued == 1
    assert films.match(s).by_title == 0  # idempotent
    summary = films.report(s, tmp_path)
    assert summary["films"] == 9 and summary["films_with_hfgk"] == 5 and summary["hfgk_matched_to_wikidata"] == 3
    assert summary["hfgk_years"] == [1940, 1962] and summary["songs_hfgk"] == 33
    rows = {r["title"]: r for r in csv.DictReader((tmp_path / "films.csv").open(encoding="utf-8-sig"))}
    b = rows["Barsat"]
    assert (b["wikidata"], b["imdb"], b["hfgk_id"], b["song_count"]) == ("Q1", "tt1", "101", "2")
    assert b["singers"] == "Lata Mangeshkar; Mukesh" and b["music_directors"] == "Shankar Jaikishan"
    flags = {(r["title"], r["flag"]) for r in csv.DictReader((tmp_path / "review.csv").open(encoding="utf-8-sig"))}
    assert ("Pyasa", "composer_mismatch") in flags
    assert ("Awara", "year_mismatch") in flags
    assert ("Lost Reel", "no_wikidata") in flags
    assert ("Not In Hfgk", "not_in_hfgk") in flags
    assert ("Remake", "match_review") in flags
    assert not any(t == "Barsat" for t, _ in flags)  # spelling twins are not flagged
    assert not any(t == "Modern Film" for t, _ in flags)  # after HFGK's years: not expected there
    assert json.loads((tmp_path / "summary.json").read_text())["flags"]["composer_mismatch"] == 1


def test_check_canon(store_for, tmp_path):
    reg = store_for("hindi-films")
    _register(reg)
    films.match(reg)
    canon = store_for("rafi")
    m = canon.maestro_entity()
    f = canon.find_or_create_by_xref("container", "hfgk_online", "101", "Barsat")
    canon.upsert_claim(f, "title", value="Barsat", source_id="hfgk_online")
    canon.upsert_claim(f, "year", value=1950, source_id="hfgk_online")
    canon.upsert_claim(f, "song_count", value=3, source_id="hfgk_online")
    song = canon.find_or_create_by_xref("song", "hfgk_online", "s9", "Main Zindagi Mein")
    canon.upsert_claim(song, "part_of", value={"position": 1}, ref_entity_id=f, source_id="hfgk_online")
    canon.upsert_claim(song, "credit", value={"role": "singer"}, ref_entity_id=m, source_id="hfgk_online")
    summary = films.check_canon(reg, canon, tmp_path)
    assert summary["canon_films"] == 1 and summary["matched_to_register"] == 1
    assert summary["flags"] == {"year_mismatch": 1, "song_count_changed": 1}


def test_names_agree_by_initials():
    assert films.names_agree(["R. C. Boral"], ["Raichand Boral"])
    assert films.names_agree(["Khurshid Anwar"], ["Khawaja Khurshid Anwar"])
    assert films.names_agree(["S. K. Pal"], ["Sankar Kumar Pal"])
    assert films.names_agree(["Hemant Kumar"], ["Basant Kumar"]) is False


def test_explorer_films_view(store_for, tmp_path):
    from maestro.explore import write_site

    s = store_for("hindi-films")
    _register(s)
    films.match(s)
    snap = films.films_snapshot(s)
    assert snap["register"]["slug"] == "hindi-films" and len(snap["films"]) == 9
    barsat = next(f for f in snap["films"] if f["hfgk"] == "101")
    assert barsat["wikidata"] == "Q1" and barsat["sing"] == ["Lata Mangeshkar", "Mukesh"] and not barsat["flags"]
    # every song carries its store id: the explorer keys gem tags on register songs by it
    for sid in [r["id"] for r in s.conn.execute("SELECT id FROM entity WHERE type = 'song'").fetchall()]:
        s.upsert_claim(sid, "title", value=f"Song {sid}", source_id="hfgk_online")
    rows = [x for xs in films.films_snapshot(s)["songs"].values() for x in xs]
    assert rows and all(isinstance(x["id"], int) for x in rows) and len({x["id"] for x in rows}) == len(rows)
    write_site(tmp_path, [], [snap])
    index = json.loads((tmp_path / "canon" / "index.json").read_text())
    assert index["registers"][0]["file"] == "canon/films/hindi-films.json" and index["maestros"] == []
    assert (tmp_path / "canon" / "films" / "hindi-films.json").is_file()
    assert 'id="v-films"' in (tmp_path / "index.html").read_text()


def test_skeleton_matches_hfgk_spellings(store_for):
    assert films.skeleton("teree bahon men") == films.skeleton("teri baahon mein")
    assert films.skeleton("aakhri ghalati") == films.skeleton("aakhri galti")
    s = store_for("hindi-films")
    _film(s, "wikidata", "Q10", "Teri Baahon Mein", 1984, ["Bappi Lahiri"], wikidata="Q10")
    _film(s, "hfgk_online", "110", "Teree Bahon Men", 1984, ["Bappi Lahiri"], song_count=5)
    _film(s, "wikidata", "Q11", "Rusvai", 1985, wikidata="Q11")
    _film(s, "hfgk_online", "111", "Rusavaee", 1984, song_count=4)  # a year apart, no music director: left
    assert films.match(s).by_title == 1


def test_names_agree_spellings_and_titles():
    assert films.names_agree(["Ajit Varman"], ["Ajit Verman"])
    assert films.names_agree(["Master Krishnarao"], ["Krishnarao Phulambrikar"])
    assert films.names_agree(["Pt Badri Prasad"], ["Badri Prasad"])
    assert films.names_agree(["Kanu Roy [1]"], ["Kanu Roy"])
    assert films.names_agree(["Manas Mukherjee"], ["Bhaskar Chandavarkar"]) is False
    assert films.names_agree(["R. D. Burman"], ["S. D. Burman"]) is False


def test_names_agree_more_spellings():
    assert films.names_agree(["Master Muhammad"], ["Master Mohammed"])
    assert films.names_agree(["Salil Chaudhri"], ["Salil Chowdhury"])
    assert films.names_agree(["Ram-Lakshman"], ["Raam Laxman"])


def test_hfgk_bot_check_page_writes_nothing(store_for):
    import pytest

    s = store_for("hindi-films")
    raw = s.record_raw_page("u", 200, "x", "p")
    body = "<html><head><title>One moment, please...</title><script>window.location.reload();</script></head></html>"
    with pytest.raises(hfgk.BotCheck):
        hfgk.extract(ExtractContext(s, {}, page(hfgk.BASE + "film.php?id=5", body), raw))
    assert s.query_graph("SELECT count(*) AS n FROM entity WHERE type = 'container'")[0]["n"] == 0


def test_latin_fixes_cyrillic_lookalikes():
    assert films.latin("Chitragupт") == "Chitragupt"
    assert films.latin("मोहम्मद रफ़ी") == "मोहम्मद रफ़ी"


def test_resolutions_and_decisions(store_for, tmp_path):
    s = store_for("hindi-films")
    _register(s)
    films.match(s)
    films.report(s, tmp_path)
    review = {(r["title"], r["flag"]): r for r in csv.DictReader((tmp_path / "review.csv").open(encoding="utf-8-sig"))}
    awara = review[("Awara", "year_mismatch")]
    assert awara["status"] == "resolved" and awara["value"] == "1952" and "censor" in awara["rule"]
    pyasa = review[("Pyasa", "composer_mismatch")]
    assert pyasa["status"] == "resolved" and pyasa["value"] == "R. D. Burman"
    assert review[("Lost Reel", "no_wikidata")]["status"] == "info"
    todo = {(r["title"], r["flag"]) for r in csv.DictReader((tmp_path / "to-review.csv").open(encoding="utf-8-sig"))}
    assert ("Remake", "match_review") in todo and ("Not In Hfgk", "not_in_hfgk") in todo
    assert ("Awara", "year_mismatch") not in todo
    rows = {r["title"]: r for r in csv.DictReader((tmp_path / "films.csv").open(encoding="utf-8-sig"))}
    assert rows["Awara"]["year"] == "1952"
    # The user's decision wins over the rule, and a second decision replaces the first.
    fid = int(awara["film_id"])
    films.decide(s, fid, "year_mismatch", "1950", note="first try")
    films.decide(s, fid, "year_mismatch", "1951", note="release date")
    films.report(s, tmp_path)
    review = {(r["title"], r["flag"]): r for r in csv.DictReader((tmp_path / "review.csv").open(encoding="utf-8-sig"))}
    assert review[("Awara", "year_mismatch")]["value"] == "1951"
    assert review[("Awara", "year_mismatch")]["rule"] == "your decision: release date"


def test_tie_break_rules(store_for):
    s = store_for("hindi-films")
    _film(s, "wikidata", "Q21", "Gangs of Wasseypur – Part 1", 2012, wikidata="Q21")
    _film(s, "wikidata", "Q22", "Gangs of Wasseypur – Part 2", 2012, wikidata="Q22")
    _film(s, "wikidata", "Q23", "Gangs of Wasseypur", 2012, wikidata="Q23")
    _film(s, "wikipedia", "2012:Gangs of Wasseypur – Part 2", "Gangs of Wasseypur – Part 2", 2012)
    _film(s, "wikidata", "Q31", "Agaman", 1982, wikidata="Q31", wikipedia="Aagaman")
    _film(s, "wikidata", "Q32", "Agaman", 1982, wikidata="Q32")
    _film(s, "hfgk_online", "301", "Agaman", 1982)
    films.match(s)
    report = {(r["title"], tuple(r["sources"].split("; "))) for r in
              (films.film_row(f) for f in films.films_in_groups(s))}
    assert ("Gangs of Wasseypur – Part 2", ("wikidata", "wikipedia")) in report
    assert ("Agaman", ("hfgk_online", "wikidata")) in report
    reasons = [r["value"]["reason"] for r in s.conn.execute(
        "SELECT value FROM claim WHERE field = 'same_as' ORDER BY id").fetchall()]
    assert any("exact title" in r for r in reasons) and any("Wikipedia article or IMDb" in r for r in reasons)


def test_names_agree_first_word_teams_and_b_v():
    assert films.names_agree(["Jaidev"], ["Jaidev Verma"])
    assert films.names_agree(["Naushad Ali"], ["Naushad"])
    assert films.names_agree(["Kalyanji-Anandji"], ["Kalyanji Virji Shah"])
    assert films.names_agree(["Vrijlal Varma"], ["Brijlal Varma"])
    assert films.names_agree(["Master Sundar Das"], ["Sunderdas Bhatia"])
    assert films.names_agree(["Madhulal Damodar Master"], ["Master Madhavlal Damodar"])
    assert films.names_agree(["R. D. Burman"], ["S. D. Burman"]) is False
    assert films.names_agree(["C Ramchandra"], ["Annasaheb Mainkar"]) is False
