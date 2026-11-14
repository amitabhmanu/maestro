"""Canon explorer: snapshot contents, private claims, and the written site."""

import json

from maestro.explore import _xref_link, main, snapshot, write_site
from test_resolve import film, person, song


def build(s):
    naushad = person(s, "hfgk_online", "1", "Naushad")
    lata = person(s, "hfgk_online", "2", "Lata Mangeshkar")
    lata2 = person(s, "musicbrainz", "x2", "Lata")
    s.merge_entities(lata, lata2, "test")
    f = film(s, "hfgk_online", "10", "Mela", 1948)
    s.upsert_claim(f, "title", value="Mela", source_id="hfgk_online")
    s.upsert_claim(f, "song_count", value=2, source_id="hfgk_online")
    a = song(s, "hfgk_online", "100", "Ye Zindagi Ke Mele", f, naushad)
    s.upsert_claim(a, "title", value="Ye Zindagi Ke Mele", source_id="hfgk_online")
    s.upsert_claim(a, "title_hi", value="ये ज़िंदगी के मेले", source_id="hfgk_online")
    b = song(s, "hfgk_online", "101", "Mohan Ki Muraliya", f, naushad)
    s.upsert_claim(b, "credit", ref_entity_id=lata, value={"role": "singer"}, source_id="hfgk_online")
    # The same song from MusicBrainz, merged: its title becomes an alternative, its link a source link.
    mb = song(s, "musicbrainz", "4257aa51-13d4-4a64-a977-689fcfd9bbd8", "Mohan Ki Muraliyaa")
    s.upsert_claim(mb, "title", value="Mohan Ki Muraliyaa", source_id="musicbrainz")
    s.upsert_claim(mb, "credit", ref_entity_id=lata2, value={"role": "singer"}, source_id="musicbrainz")
    s.merge_entities(b, mb, "test")
    return a, b, f, lata


def test_snapshot(store_for):
    s = store_for("rafi")
    a, b, f, lata = build(s)
    snap = snapshot(s)
    assert snap["maestro"]["slug"] == "rafi" and snap["maestro"]["unit"] == "song"
    units = {u["id"]: u for u in snap["units"]}
    assert set(units) == {a, b}
    ub = units[b]
    assert ub["year"] == 1948 and ub["tier"] == "A" and ub["container"] == f
    assert ub["alt_titles"] == ["Mohan Ki Muraliyaa"]
    assert sorted(ub["credits"]["singer"]) == sorted([s.maestro_entity(), lata])  # merged person counts once
    assert ub["lineages"] == ["hfgk", "musicbrainz"]
    assert {"source": "musicbrainz", "url": "https://musicbrainz.org/recording/4257aa51-13d4-4a64-a977-689fcfd9bbd8"} in ub["links"]
    assert units[a]["title_alt_script"] == ["ये ज़िंदगी के मेले"]
    assert snap["containers"][str(f)]["title"] == "Mela" and snap["containers"][str(f)]["listed"] == 2
    assert snap["people"][str(lata)]["name"] == "Lata Mangeshkar" and snap["people"][str(lata)]["aliases"] == ["Lata"]
    assert snap["coverage"]["canon_units"] == 2
    json.dumps(snap, default=str)


def test_private_claims_stay_out(store_for):
    s = store_for("rafi")
    a, *_ = build(s)
    secret = song(s, "hfgk_online", "900", "Private find", rafi=False)
    s.upsert_claim(secret, "credit", ref_entity_id=s.maestro_entity(), value={"role": "singer"},
                   source_id="hfgk_online", private=True)
    s.upsert_claim(secret, "xref", value="p", source_id="mohdrafi", private=True)
    s.conn.execute("UPDATE claim SET visibility = 'private' WHERE entity_id = %s", (secret,))
    s.upsert_claim(a, "note", value="x", source_id="hfgk_online")
    assert secret not in {u["id"] for u in snapshot(s)["units"]}
    assert secret in {u["id"] for u in snapshot(s, include_private=True)["units"]}


def test_xref_links():
    assert _xref_link("wikidata", "film:Q3225241") == "https://www.wikidata.org/wiki/Q3225241"
    assert _xref_link("musicbrainz", "work:348be3d3-a436-452a-9848-deb64186113d").startswith("https://musicbrainz.org/work/")
    assert _xref_link("bnf", "work:cb169477798") == "https://data.bnf.fr/ark:/12148/cb169477798"
    assert _xref_link("atulsongaday", "http://wp.me/phTr2-edz") == "http://wp.me/phTr2-edz"
    assert _xref_link("discogs", "master:1883154:ghazal") == "https://www.discogs.com/master/1883154"
    assert _xref_link("discogs", "release:20601124:A") == "https://www.discogs.com/release/20601124"
    assert _xref_link("hfgk_online", "5814") is None


def test_site(store_for, tmp_path):
    s = store_for("rafi")
    build(s)
    write_site(tmp_path, [snapshot(s)])
    assert "<title>Canon Explorer</title>" in (tmp_path / "index.html").read_text("utf-8")
    index = json.loads((tmp_path / "canon" / "index.json").read_text("utf-8"))
    assert index["maestros"][0]["slug"] == "rafi" and index["maestros"][0]["units"] == 2
    assert json.loads((tmp_path / "canon" / "rafi.json").read_text("utf-8"))["format"] == 1
    # A later run for another maestro keeps the first one listed.
    main(["--out", str(tmp_path)])
    assert len(json.loads((tmp_path / "canon" / "index.json").read_text("utf-8"))["maestros"]) == 1


def test_media_links():
    from maestro.explore import add_media, media_kind

    assert media_kind("https://www.youtube.com/watch?v=abc") == ("watch", "YouTube")
    assert media_kind("https://www.last.fm/music/Mohammed+Rafi/_/Chaudhvin+Ka+Chand") == ("listen", "Last.fm")
    assert media_kind("https://www.last.fm/music/Mohammed+Rafi") is None  # the artist, not a track
    assert media_kind("https://www.hindigeetmala.net/geetmala/binaca_geetmala_1953.php") is None  # a chart, not a song
    assert media_kind("http://wp.me/phTr2-u4g", "atulsongaday", {"atulsongaday": "lyrics"}) == ("lyrics", "atulsongaday")
    snap = {"sources": {"atulsongaday": {"url": "https://atulsongaday.me"}}, "units": [
        {"id": 1, "links": [{"source": "atulsongaday", "url": "http://wp.me/phTr2-u4g"},
                            {"source": "hfgk_online", "url": "https://hamraaz.org/x"}],
         "pop": {"evidence": [{"signal": "youtube", "source": "youtube", "note": "9,000 YouTube views",
                               "url": "https://www.youtube.com/watch?v=best"},
                              {"signal": "youtube", "source": "youtube", "note": "10 YouTube views",
                               "url": "https://www.youtube.com/watch?v=other"},
                              {"signal": "lastfm", "source": "lastfm", "note": "5 listeners",
                               "url": "https://www.last.fm/music/Mohammed+Rafi/_/Song"}]}},
        {"id": 2, "media": [{"kind": "watch", "site": "YouTube", "url": "https://youtu.be/stale0"}]}]}
    pack = {"media_links": {"lyrics_sites": ["hindigeetmala.net"], "sources": {"atulsongaday": "lyrics"}}}
    add_media(snap, pack)
    u1, u2 = snap["units"]
    assert u1["media"] == [
        {"kind": "watch", "site": "YouTube", "url": "https://www.youtube.com/watch?v=best", "note": "9,000 YouTube views"},
        {"kind": "listen", "site": "Last.fm", "url": "https://www.last.fm/music/Mohammed+Rafi/_/Song", "note": "5 listeners"},
        {"kind": "lyrics", "site": "atulsongaday.me", "url": "http://wp.me/phTr2-u4g"}]
    assert "media" not in u2  # recomputed from the snapshot, so stale links go
    assert "site%3Ahindigeetmala.net" in snap["media_search"]["lyrics"][0]["url"]
    assert "lyrics" not in add_media({"units": []}, {"media_links": {"lyrics_sites": []}})["media_search"]
