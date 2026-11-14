"""Core Discogs extractor, against synthetic pages shaped like the Discogs API, and API tokens."""

import json
from pathlib import Path

from maestro.crawl.fetcher import Fetcher, Page, api_headers, api_hosts
from maestro.crawl.runner import ExtractContext
from maestro.extractors import discogs
from maestro.resolve import resolve

FIX = Path(__file__).parent / "fixtures" / "discogs"
RAFI = 99


def _load(name):
    return json.loads((FIX / name).read_text("utf-8"))


def test_roles_and_track_ranges():
    assert discogs.canon_role("Vocals [Playback]") == "singer"
    assert discogs.canon_role("Lyrics By") == "lyricist"
    assert discogs.canon_role("Mastered By") is None
    pos = ["A1", "A2", "A3", "B1"]
    assert discogs.covered("", pos) == set(pos)
    assert discogs.covered("A1 to A3, B1", pos) == set(pos)
    assert discogs.covered("A2", pos) == {"A2"}


def test_listing_queues_main_releases_and_skips_unofficial():
    listing = discogs.parse_listing(_load("listing.json"))
    assert listing == {"releases": [5001, 5002], "next": 2}


def test_release_keeps_the_maestros_tracks_and_credits():
    rel = discogs.parse_release(_load("soundtrack.json"), RAFI)
    assert rel["soundtrack"] and rel["master"] == 111 and rel["catalogues"] == ["MOCE 4021"]
    assert [t["position"] for t in rel["tracks"]] == ["A1", "A2"]      # B1 is Lata's solo
    a2 = rel["tracks"][1]
    assert a2["maestro_roles"] == ["singer"] and a2["artist_credit"] == "Naushad"
    assert sorted((c["name"], discogs.canon_role(c["role"])) for c in a2["credits"]) == [
        ("Lata Mangeshkar", "singer"), ("Shakeel Badayuni", "lyricist")]

    nfs = discogs.parse_release(_load("nonfilm.json"), RAFI)
    assert not nfs["soundtrack"] and nfs["master"] is None
    assert [(t["position"], t["main"]) for t in nfs["tracks"]] == [("1", True), ("3a", True)]  # 2 is Asha's
    assert [c["name"] for c in nfs["tracks"][0]["credits"]] == ["Khayyam"]


def _ctx(s, url, body, purpose="spine"):
    raw = s.record_raw_page(url, 200, "x", "p")
    page = Page(url=url, status=200, body=body, sha256="x", path=FIX)
    return ExtractContext(s, {"purpose": purpose}, page, raw)


def test_extract_writes_units_containers_and_matches_hfgk(store_for):
    from test_resolve import film, song

    s = store_for("rafi")
    s.pack.data["identity"]["ids"]["discogs"] = str(RAFI)
    hfgk_film = film(s, "hfgk_online", "400", "Kohinoor", 1960)
    hfgk_song = song(s, "hfgk_online", "401", "Madhuban Mein Radhika Nache Re", hfgk_film)

    s.conn.execute("DELETE FROM job")
    discogs.extract(_ctx(s, discogs.artist_releases_url(RAFI), (FIX / "listing.json").read_bytes()))
    urls = [r["url"] for r in s.conn.execute("SELECT url FROM job ORDER BY id")]
    assert urls == [discogs.release_url(5001), discogs.release_url(5002), discogs.artist_releases_url(RAFI, 2)]

    discogs.extract(_ctx(s, discogs.release_url(5001), (FIX / "soundtrack.json").read_bytes()))
    # A reissue of the same master adds a sighting of the same units, not new ones.
    reissue = _load("soundtrack.json") | {"id": 5009, "labels": [{"name": "Saregama", "catno": "CDF 1"}]}
    discogs.extract(_ctx(s, discogs.release_url(5009), json.dumps(reissue).encode()))
    discogs.extract(_ctx(s, discogs.release_url(5002), (FIX / "nonfilm.json").read_bytes()))
    units = s.conn.execute("SELECT count(DISTINCT c.entity_id) AS n FROM claim c JOIN entity e ON e.id = c.entity_id "
                           "WHERE c.field = 'xref' AND c.source_id = 'discogs' AND e.type = 'song'").fetchone()["n"]
    assert units == 4
    a1 = s.entity_by_xref("discogs", "master:111:madhuban mein radhika nache re", "song")
    cats = {r["value"] for r in s.conn.execute("SELECT value FROM claim WHERE entity_id = %s AND field = 'catalogue'", (a1,))}
    assert cats == {"MOCE 4021", "CDF 1"}
    container = s.entity_by_xref("discogs", "master:111", "container")
    assert container is not None
    lyricist = s.entity_by_xref("discogs", "artist:301", "person")
    roles = {r["value"]["role"] for r in s.conn.execute(
        "SELECT value FROM claim WHERE entity_id = %s AND field = 'credit' AND ref_entity_id = %s", (a1, lyricist))}
    assert roles == {"lyricist"}

    resolve(s)
    merged = s.conn.execute("SELECT ref_entity_id FROM claim WHERE field = 'same_as' AND entity_id = %s", (a1,)).fetchall()
    assert [r["ref_entity_id"] for r in merged] == [hfgk_song]
    assert s.coverage_report()["units_per_lineage"]["discogs"] >= 1


def test_api_token_is_sent_but_never_stored(tmp_path, monkeypatch):
    pack = {"budget": {"per_domain_rps": 0.5}, "sources": [
        {"id": "discogs", "access": "api", "url": "https://api.discogs.com/", "api_rps": 0.4,
         "api_auth": {"env": "DISCOGS_TOKEN", "format": "Discogs token={token}", "rps": 0.9}}]}
    monkeypatch.delenv("DISCOGS_TOKEN", raising=False)
    assert api_headers(pack) == {} and api_hosts(pack) == {"api.discogs.com": 0.4}
    monkeypatch.setenv("DISCOGS_TOKEN", "sekret")
    assert api_headers(pack) == {"api.discogs.com": {"Authorization": "Discogs token=sekret"}}
    assert api_hosts(pack) == {"api.discogs.com": 0.9}

    seen = []

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def opener(req, timeout):
        seen.append(dict(req.header_items()))
        return Resp()

    f = Fetcher(tmp_path, 1, opener=opener, sleep=lambda s: None, api_hosts=api_hosts(pack), api_headers=api_headers(pack))
    page = f.fetch(discogs.release_url(1))
    assert seen[0]["Authorization"] == "Discogs token=sekret"
    assert b"sekret" not in page.path.read_bytes() and "sekret" not in page.url


def test_real_rafi_records(store_for):
    """Trimmed real API responses (Discogs data is CC0): a film 78 and a non-film EP."""
    s = store_for("rafi")
    s.pack.data["identity"]["ids"]["discogs"] = "386904"
    film78 = discogs.parse_release(_load("rafi_film_78.json"), 386904)
    assert film78["title"] == "Baiju Bawra" and film78["soundtrack"] and film78["catalogues"] == ["FT. 17508"]
    assert [t["title"] for t in film78["tracks"]] == ["ओ दुनिया के रखवाले", "ओ दुनिया के रखवाले"]  # two sides
    assert sorted((c["name"], discogs.canon_role(c["role"])) for c in film78["tracks"][0]["credits"]) == [
        ("Naushad", "composer"), ("Shakeel Badayuni", "lyricist")]
    ep = discogs.parse_release(_load("rafi_ghazals_ep.json"), 386904)
    assert ep["tracks"][0]["maestro_roles"] == ["composer"] and ep["tracks"][0]["main"]

    discogs.extract(_ctx(s, discogs.release_url(9969135), (FIX / "rafi_ghazals_ep.json").read_bytes()))
    u = s.entity_by_xref("discogs", "master:1883154:ghata hai bagh hai mai hai", "song")
    roles = [r["value"]["role"] for r in s.conn.execute(
        "SELECT value FROM claim WHERE entity_id = %s AND field = 'credit' AND ref_entity_id = %s", (u, s.maestro_entity()))]
    assert roles == ["singer"]  # composing is outside Rafi's roles in scope
