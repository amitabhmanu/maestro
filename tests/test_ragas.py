"""Ragas: the core Wikipedia raga list extractor for a maestro canon, the raga table and the snapshot's styles."""

from conftest import ROOT
from test_resolve import film, song

from maestro.crawl.fetcher import Page
from maestro.crawl.runner import ExtractContext, load_extractor
from maestro.explore import snapshot
from maestro.styles import Names, add_style, load_table

TABLE = ROOT / "maestros" / "hindi-films" / "lexicon" / "ragas.tsv"
SEED = "https://en.wikipedia.org/wiki/List_of_film_songs_based_on_ragas"
PAGE = """<table class="wikitable"><tr><th>Rāg</th><th>Song</th><th>Film</th><th>Music composer</th>
<th>Singer(s)</th><th>Lang</th></tr>
<tr><td>Darbari Kanada</td><td>"O Duniya Ke Rakhwale"</td><td><a href="/wiki/Baiju_Bawra_(film)">Baiju Bawra</a></td><td>Naushad</td><td>Mohammed Rafi</td><td>Hindi</td></tr>
<tr><td>Yaman / Kalyani (raga)</td><td>"Chaudhvin Ka Chand Ho"</td><td><a href="/wiki/Chaudhvin_Ka_Chand_(1960_film)">Chaudhvin Ka Chand</a></td><td>Ravi</td><td>Mohammad Rafi</td><td>Hindi</td></tr>
<tr><td>Bhairavi</td><td>"Tute Na Dil"</td><td><a href="/wiki/Andaz_(1949_film)">Andaz</a></td><td>Naushad</td><td>Mukesh</td><td>Hindi</td></tr>
<tr><td>Malkauns</td><td>"Man Tadpat Hari Darshan Ko Aaj"</td><td><a href="/wiki/Baiju_Bawra_(film)">Baiju Bawra</a></td><td>Naushad</td><td>Mohammed Rafi</td><td>Hindi</td></tr>
<tr><td>Kalyani</td><td>Something</td><td>X</td><td>Y</td><td>Mohammed Rafi</td><td>Telugu</td></tr>
</table>"""


def _values(s, eid, field):
    return [r["value"] for r in s.conn.execute(
        "SELECT value FROM claim WHERE entity_id = %s AND field = %s AND status = 'active' ORDER BY id", (eid, field))]


def test_parse_reads_singers_year_and_hindi_rows_only():
    wr = load_extractor(ROOT / "maestros" / "rafi", "wikipedia_ragas")
    rows = wr.parse(PAGE)
    assert [r["film"] for r in rows] == ["Baiju Bawra", "Chaudhvin Ka Chand", "Andaz", "Baiju Bawra"]
    assert rows[1]["year"] == 1960 and rows[0]["year"] is None and rows[1]["song"] == "Chaudhvin Ka Chand Ho"
    assert wr.sung_by(rows[1], ["Mohammed Rafi", "Mohammad Rafi"]) and not wr.sung_by(rows[2], ["Mohammed Rafi"])
    assert not wr.sung_by({"singers": "Hemlata (singer)"}, ["Lata Mangeshkar", "Lataji"])


def test_extract_adds_ragas_only_and_lists_gaps(store_for):
    s = store_for("rafi")
    wr = load_extractor(s.pack.path.parent, "wikipedia_ragas")
    baiju = film(s, "hfgk_online", "200", "Baiju Bawra", 1952)
    duniya = song(s, "hfgk_online", "201", "O Duniya Ke Rakhwale, Sun Dard Bhare Mere Nale", baiju)
    chand = film(s, "hfgk_online", "100", "Chaudhvin Ka Chand", 1960)
    cka = song(s, "hfgk_online", "101", "Chaudhvin Ka Chand Ho Ya Aaftab Ho", chand)
    raw = s.record_raw_page(SEED, 200, "x", "p")
    ctx = ExtractContext(s, {"purpose": "enrich"}, Page(url=SEED, status=200, body=PAGE.encode(), sha256="x", path=None), raw)
    wr.extract(ctx)
    wr.extract(ctx)                                          # read whole again: gaps are not kept twice
    assert _values(s, duniya, "raga") == ["Darbari Kanada"] and _values(s, cka, "raga") == ["Yaman / Kalyani (raga)"]
    assert not s.conn.execute("SELECT 1 FROM claim WHERE source_id = 'wikipedia_ragas' AND field IN "
                              "('title', 'xref', 'credit', 'part_of', 'tier')").fetchall()   # no lineage
    gaps = _values(s, s.maestro_entity(), "listing_gap")
    assert [(g["title"], g["reason"], g["raga"]) for g in gaps] == [("Man Tadpat Hari Darshan Ko Aaj", "song_not_in_film", "Malkauns")]


def test_names_read_the_table_out_of_source_labels():
    n = Names(load_table(TABLE))
    assert n.names("Yaman / Kalyani (raga)") == ["Yaman"] and n.names("Yaman Kalyan") == ["Yaman"]
    assert n.names("Charukesi") == ["Charukeshi"] and n.names("Desh or Des") == ["Desh"]
    assert n.names("Pahadi [circular reference]") == ["Pahadi"] and n.names("(Alaiya) Bilawal") == ["Bilawal"]
    assert n.names("Sohni / Hamsanandi & Bahar") == ["Sohni", "Bahar"]
    assert n.names("Bhāgeshri (Carnatic) Bageshri (Hindustani)") == ["Bageshri"]
    assert n.names("Bheem / Gavati") == ["Bheem"]           # unknown to the table: kept as written


def test_add_style_statuses_sources_and_disputes():
    pack = {"taxonomies": {"style": "raga"}, "lexicon": {"styles": "ragas.tsv"}}
    snap = {"units": [
        {"id": 1, "raga": ["Charukeshi", "Charukesi"], "raga_src": {"Charukeshi": ["hindigeetmala"], "Charukesi": ["wikipedia_ragas"]}},
        {"id": 2, "raga": ["Darbari", "Adana"], "raga_src": {"Darbari": ["hindigeetmala"], "Adana": ["wikipedia_ragas"]}},
        {"id": 3, "raga": ["Darbari", "Adana"], "raga_src": {"Darbari": ["hindigeetmala"], "Adana": ["user"]}},
        {"id": 4, "raga": ["Pilu"]},                          # a snapshot written before sources were kept
        {"id": 5},
    ]}
    add_style(snap, pack, TABLE.parent)
    u = {x["id"]: x for x in snap["units"]}
    assert u[1]["styles"] == [{"n": "Charukeshi", "src": ["hindigeetmala", "wikipedia_ragas"], "as": ["Charukesi"], "st": "sourced"}]
    assert "style_disputed" not in u[1]
    assert u[2]["style_disputed"] and {e["n"] for e in u[2]["styles"]} == {"Darbari", "Adana"}
    assert u[3]["styles"][0] == {"n": "Adana", "src": ["user"], "st": "confirmed"} and "style_disputed" not in u[3]
    assert u[4]["styles"][0]["src"] == ["unrecorded"] and "styles" not in u[5]
    st = snap["style"]
    assert st["taxonomy"] == "raga" and st["units"] == 4 and st["disputed"] == 1
    assert st["table"]["Darbari"]["time"] == "n3" and st["table"]["Pilu"]["light"] is True
    assert st["by_status"] == {"sourced": 3, "confirmed": 1}
    add_style(snap, pack, TABLE.parent)                       # again (--relink): same result, sources kept
    assert snap["units"][0]["styles"][0]["src"] == ["hindigeetmala", "wikipedia_ragas"]
    assert add_style({"units": []}, {}, None) == {"units": []}


def test_snapshot_keeps_each_ragas_source(store_for):
    s = store_for("rafi")
    f = film(s, "hfgk_online", "10", "Mela", 1948)
    a = song(s, "hfgk_online", "100", "Ye Zindagi Ke Mele", f)
    s.upsert_claim(a, "title", value="Ye Zindagi Ke Mele", source_id="hfgk_online")
    s.upsert_claim(a, "raga", value="Pahadi", source_id="hindigeetmala")
    s.upsert_claim(a, "raga", value="Pahadi [circular reference]", source_id="wikipedia_ragas")
    snap = snapshot(s)
    u = next(x for x in snap["units"] if x["id"] == a)
    assert u["styles"] == [{"n": "Pahadi", "src": ["hindigeetmala", "wikipedia_ragas"], "as": ["Pahadi [circular reference]"], "st": "sourced"}]
    assert snap["style"]["taxonomy"] == "raga" and snap["style"]["table"]["Pahadi"]["light"] is True
