"""Rafi's hindigeetmala.net song list: trimmed real pages (page 1 and the last page)."""

from conftest import ROOT
from test_resolve import film, song

from maestro.crawl.fetcher import Page
from maestro.crawl.runner import ExtractContext, load_extractor
from maestro.resolve import resolve

hgm = load_extractor(ROOT / "maestros" / "rafi", "hindigeetmala")
FIX = ROOT / "tests" / "fixtures" / "hindigeetmala"
SEED = "https://www.hindigeetmala.net/singer/mohammed_rafi.php"


def _run(s, url, name):
    raw = s.record_raw_page(url, 200, "x", "p")
    page = Page(url=url, status=200, body=(FIX / name).read_bytes(), sha256="x", path=FIX)
    hgm.extract(ExtractContext(s, {"purpose": "spine"}, page, raw))


def _values(s, eid, field):
    return [r["value"] for r in s.conn.execute(
        "SELECT value FROM claim WHERE entity_id = %s AND field = %s AND status = 'active' ORDER BY id", (eid, field))]


def test_parse():
    first = hgm.parse((FIX / "page1.html").read_text("utf-8"))
    assert first["pages"] == 456
    chand = first["songs"][0]
    assert chand["film"] == {"key": "chaudhvin_ka_chand", "title": "Chaudhvin Ka Chand", "year": 1960}
    assert [n for _p, n in chand["actors"]] == ["Waheeda Rehman", "Guru Dutt"]
    assert "Raga - Pahadi" in chand["categories"]
    last = hgm.parse((FIX / "page456.html").read_text("utf-8"))
    assert [n for _p, n in last["songs"][1]["lyricists"]] == ["Naqsh Lyallpuri", "Asad Bhopali"]
    assert last["songs"][2]["film"]["title"] == "Non-Film (Mohammed Rafi)"


def test_extract_queues_pages_and_writes_units(store_for):
    s = store_for("rafi")
    s.conn.execute("DELETE FROM job")
    _run(s, SEED, "page1.html")
    urls = [r["url"] for r in s.conn.execute("SELECT url FROM job ORDER BY id")]
    assert len(urls) == 455 and urls[0] == SEED + "?page=2" and urls[-1] == SEED + "?page=456"
    _run(s, SEED + "?page=456", "page456.html")
    assert s.conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"] == 455   # later pages queue nothing

    chand = s.entity_by_xref("hindigeetmala", "song:chaudavi_ka_chaand_ho_ya_aaftaab_ho", "song")
    assert _values(s, chand, "tier") == ["A"] and _values(s, chand, "raga") == ["Pahadi"]
    assert "Raga - Pahadi" not in _values(s, chand, "category")
    assert len(_values(s, chand, "on_screen")) == 2
    container = s.entity_by_xref("hindigeetmala", "movie:chaudhvin_ka_chand", "container")
    assert _values(s, container, "year") == [1960]

    ghazal = s.entity_by_xref("hindigeetmala", "song:zulmat_kade_men_mere_shab_rafi", "song")
    assert _values(s, ghazal, "tier") == ["B"] and _values(s, ghazal, "category") == ["Non-Film (Mohammed Rafi)"]
    assert _values(s, ghazal, "part_of") == []
    assert s.entity_by_xref("hindigeetmala", "movie:non_film_mohammed_rafi", "container") is None
    roles = {r["value"]["role"] for r in s.conn.execute(
        "SELECT value FROM claim WHERE entity_id = %s AND field = 'credit' AND ref_entity_id = %s", (ghazal, s.maestro_entity()))}
    assert roles == {"singer"}


def test_songs_match_hfgk_films(store_for):
    s = store_for("rafi")
    f = film(s, "hfgk_online", "100", "Chaudhvin Ka Chand", 1960)
    u = song(s, "hfgk_online", "101", "Chaudhvin Ka Chand Ho Ya Aaftab Ho", f)
    _run(s, SEED + "?page=1", "page1.html")
    resolve(s)
    chand = s.entity_by_xref("hindigeetmala", "song:chaudavi_ka_chaand_ho_ya_aaftaab_ho", "song")
    merged = {r["ref_entity_id"] for r in s.conn.execute(
        "SELECT ref_entity_id FROM claim WHERE field = 'same_as' AND status = 'active' AND entity_id IN (%s, %s)", (u, chand))}
    assert merged & {u, chand}
