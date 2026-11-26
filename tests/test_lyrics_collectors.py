"""Lyrics collectors: hindigeetmala_lyrics (list and song pages) and lrclib (planner and API results).

Song pages and API answers are hand-made with invented lines: no real lyrics in the repository."""

import json

from conftest import ROOT
from test_resolve import film, song

from maestro.crawl.fetcher import Page
from maestro.crawl.runner import ExtractContext, load_extractor
from maestro.extractors import hindigeetmala_lyrics as hl
from maestro.extractors import lrclib
from maestro.lyrics import check_store

PACK = ROOT / "maestros" / "rafi"
FIX = ROOT / "tests" / "fixtures" / "hindigeetmala"
SEED = "https://www.hindigeetmala.net/singer/mohammed_rafi.php"

INVENTED = "Chanda ki naav chali saagar ke paar\nTaaron ki baat sune koi baar baar\n\nNadiya ke tat par jhoome pawan"


def song_page(singer: str = "mohammed_rafi", lyrics: str | None = INVENTED) -> bytes:
    """A song page shaped like the site's: navigation menu (which links every big singer), the song's own
    row, then the lyrics block."""
    nav = "".join(f'<a href="/singer/{s}.php">{s}</a>' for s in ("lata_mangeshkar", "mohammed_rafi", "mukesh"))
    row = ('<table class="w760"><tr itemprop="track" itemscope itemtype="https://schema.org/MusicRecording">'
           '<td class="w760"><table class="b1 w760 bgff pad2 allef"><tr><td rowspan="2">'
           '<a href="/song/chanda_ki_naav.htm" itemprop="url"><span itemprop="name">Chanda Ki Naav</span></a></td>'
           '<td class="w150"><span itemprop="byArtist" itemscope itemtype="http://schema.org/MusicGroup">'
           f'<a itemprop="url" href="/singer/{singer}.php"><span itemprop="name">Someone</span></a></span></td>'
           '</tr></table></td></tr></table>')
    block = ""
    if lyrics is not None:
        body = "<br />\n".join(lyrics.split("\n"))
        block = ('<h2>Chanda Ki Naav Lyrics</h2><div class="song" itemprop="recordingOf" itemscope>'
                 '<span itemprop="lyrics" itemscope itemtype="http://schema.org/CreativeWork">'
                 f'<span itemprop="text">{body}</span></span></div>')
    return f"<html><body><div class='nav'>{nav}</div>{row}{block}</body></html>".encode()


def _run(s, module, url, body, job=None):
    raw = s.record_raw_page(url, 200, "x", "p")
    page = Page(url=url, status=200, body=body, sha256="x", path=FIX)
    module.extract(ExtractContext(s, job or {"purpose": "enrich"}, page, raw))


def _claims(s, eid, field):
    return s.conn.execute("SELECT value, visibility, source_id FROM claim WHERE entity_id = %s AND field = %s "
                          "AND status = 'active' ORDER BY id", (eid, field)).fetchall()


def test_script_and_meta():
    assert hl.script_of(INVENTED) == "latin"
    assert hl.script_of("चंदा की नाव चली") == "devanagari"
    assert hl.script_of("چاند کی ناؤ") == "urdu"
    assert hl.script_of("chanda चंदा की नाव") == "mixed"
    assert hl.lyrics_meta(INVENTED, "hindigeetmala") == {"source": "hindigeetmala", "script": "latin",
                                                          "lines": 3, "words": 20}


def test_lyrics_text_keeps_lines():
    text = hl.lyrics_text(song_page().decode())
    assert text == INVENTED
    assert hl.lyrics_text(song_page(lyrics=None).decode()) is None
    assert hl.song_singers(song_page(singer="mukesh").decode()) == ["/singer/mukesh.php"]


def test_list_page_queues_song_pages_of_placed_units(store_for):
    s = store_for("rafi")
    mod = load_extractor(PACK, "hindigeetmala_lyrics")
    f = film(s, "hfgk_online", "100", "Chaudhvin Ka Chand", 1960)
    u = song(s, "hfgk_online", "101", "Chaudhvin Ka Chand Ho Ya Aaftab Ho", f)
    s.conn.execute("DELETE FROM job")
    _run(s, mod, SEED, (FIX / "page1.html").read_bytes())
    jobs = s.conn.execute("SELECT url, entity_id, priority FROM job WHERE extractor = 'hindigeetmala_lyrics' "
                          "ORDER BY id").fetchall()
    pages = [j for j in jobs if "/singer/" in j["url"]]
    songs = [j for j in jobs if "/song/" in j["url"]]
    assert len(pages) == 455 and pages[0]["url"] == SEED + "?page=2"
    # Only the song the canon holds is queued, with its unit, ahead of the list pages.
    assert [(j["url"], j["entity_id"], j["priority"]) for j in songs] == [
        ("https://www.hindigeetmala.net/song/chaudavi_ka_chaand_ho_ya_aaftaab_ho.htm", u, 1)]
    assert not s.conn.execute("SELECT 1 FROM claim WHERE source_id = 'hindigeetmala_lyrics'").fetchall()


def test_song_page_writes_private_lyrics_and_public_meta(store_for):
    s = store_for("rafi")
    mod = load_extractor(PACK, "hindigeetmala_lyrics")
    f = film(s, "hfgk_online", "100", "Chanda", 1960)
    u = song(s, "hfgk_online", "101", "Chanda Ki Naav Chali", f)
    url = "https://www.hindigeetmala.net/song/chanda_ki_naav.htm"
    _run(s, mod, url, song_page(), {"purpose": "enrich", "entity_id": u})
    lyr = _claims(s, u, "lyrics")
    assert [(c["value"], c["visibility"]) for c in lyr] == [(INVENTED, "private")]
    meta = _claims(s, u, "lyrics_meta")
    assert [(c["value"]["script"], c["value"]["lines"], c["visibility"]) for c in meta] == [("latin", 3, "publishable")]
    # No publishable first-line copy of the lyrics, and the publish check stays clean.
    assert not s.conn.execute("SELECT 1 FROM claim WHERE entity_id = %s AND field = 'first_line' "
                              "AND source_id = 'hindigeetmala_lyrics'", (u,)).fetchall()
    assert check_store(s)["ok"]
    _run(s, mod, url, song_page(), {"purpose": "enrich", "entity_id": u})   # a re-read adds nothing
    assert len(_claims(s, u, "lyrics")) == 1


def test_song_page_of_another_singer_writes_nothing(store_for):
    s = store_for("rafi")
    mod = load_extractor(PACK, "hindigeetmala_lyrics")
    u = song(s, "hfgk_online", "101", "Chanda Ki Naav Chali", film(s, "hfgk_online", "100", "Chanda", 1960))
    url = "https://www.hindigeetmala.net/song/chanda_ki_naav.htm"
    _run(s, mod, url, song_page(singer="mukesh"), {"purpose": "enrich", "entity_id": u})
    _run(s, mod, url, song_page(lyrics=None), {"purpose": "enrich", "entity_id": u})
    assert not _claims(s, u, "lyrics") and not _claims(s, u, "lyrics_meta")


def _result(**kw):
    r = {"id": 1, "trackName": "Chanda Ki Naav Chali", "artistName": "Mohammed Rafi", "albumName": "Chanda",
         "instrumental": False, "plainLyrics": INVENTED, "syncedLyrics": None}
    r.update(kw)
    return r


def test_lrclib_plan_and_extract(store_for):
    s = store_for("rafi")
    f = film(s, "hfgk_online", "100", "Chanda", 1960)
    u = song(s, "hfgk_online", "101", "2 Chanda Ki Naav Chali Sagar Ke Paar, Taaron Ki Baat", f)
    done = song(s, "hfgk_online", "102", "Pehle Se Hai Geet", f)
    s.upsert_claim(done, "lyrics", value=INVENTED, source_id="hindigeetmala_lyrics")
    s.conn.execute("DELETE FROM job")
    counts = lrclib.plan(s)
    assert counts["queued"] == 1 and counts["with_lyrics"] == 1
    job = s.conn.execute("SELECT * FROM job WHERE extractor = 'lrclib'").fetchone()
    assert job["entity_id"] == u
    assert job["url"] == lrclib.search_url("Chanda Ki Naav Chali Sagar Ke", "Mohammed Rafi")
    assert lrclib.plan(s)["queued"] == 0     # an open or finished search is not queued again

    mod = load_extractor(PACK, "lrclib")
    other = [_result(artistName="Mukesh"), _result(trackName="Kuchh Aur Hi Geet"), _result(instrumental=True)]
    _run(s, mod, job["url"], json.dumps(other).encode(), job)
    assert not _claims(s, u, "lyrics")
    devanagari = "चंदा की नाव चली\nतारों की बात सुने"
    good = [_result(albumName="Other Album"), _result(id=2, artistName="Mohd. Rafi, Asha Bhosle", plainLyrics=devanagari)]
    _run(s, mod, job["url"], json.dumps(good).encode(), job)
    assert [(c["value"], c["visibility"]) for c in _claims(s, u, "lyrics")] == [(devanagari, "private")]
    assert [c["value"] for c in _claims(s, u, "lyrics_meta")] == [
        {"source": "lrclib", "script": "devanagari", "lines": 2, "words": 8}]
    assert check_store(s)["ok"]
