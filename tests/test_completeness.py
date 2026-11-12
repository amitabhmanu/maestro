"""Completeness engine: checksums, container check and capture-recapture."""

from dataclasses import replace

from maestro.completeness import chao1, completeness_report, render_markdown
from test_resolve import film, person, song


def test_estimators():
    from maestro.completeness import chapman

    assert chapman(99, 49, 24) == 199
    assert chao1(100, 10, 5) == 110
    assert chao1(10, 3, 0) == 13  # bias-corrected form


def pack_with(pack, checksums):
    return replace(pack, data={**pack.data, "checksums": checksums})


def test_report(store_for):
    from conftest import ROOT
    from maestro.pack import load_pack

    pack = pack_with(load_pack(ROOT / "maestros" / "rafi"), [
        {"scope": "1948", "total": 3, "source": "x", "filter": {"tiers": ["A"], "years": [1948, 1948]},
         "breakdown": {"solo": 2, "duet": 1},
         "breakdown_by": {"role": "singer", "buckets": {"solo": [1, 1], "duet": [2, None]}}},
        {"scope": "Naushad", "total": 10, "source": "x",
         "filter": {"tiers": ["A"], "collaborator": {"role": "composer", "name": "Naushad"}}},
        {"scope": "non-film", "total": 1, "source": "x", "filter": {"tiers": ["B"], "contained": False}},
        {"scope": "unchecked", "total": 5, "source": "x"},
    ])
    s = store_for("rafi", pack)
    naushad = person(s, "hfgk_online", "1", "Naushad")
    lata = person(s, "hfgk_online", "2", "Lata Mangeshkar")
    f = film(s, "hfgk_online", "10", "Mela", 1948)
    s.upsert_claim(f, "song_count", value=4, source_id="hfgk_online")
    u = [song(s, "hfgk_online", str(100 + i), f"Song {i}", f, naushad) for i in range(3)]
    s.upsert_claim(u[0], "credit", ref_entity_id=lata, value={"role": "singer"}, source_id="hfgk_online")
    # Seen again by a second lineage.
    s.upsert_claim(u[1], "title", value="Song 1", source_id="atulsongaday")
    nf = s.create_entity("song", "A ghazal")
    s.upsert_claim(nf, "credit", ref_entity_id=s.maestro_entity(), value={"role": "singer"}, source_id="mohdrafi")
    s.upsert_claim(nf, "tier", value="B", source_id="claude", kind="decided")

    r = completeness_report(s)
    assert r["canon_units"] == 4
    by_scope = {c["scope"]: c for c in r["checksums"]}
    assert by_scope["1948"]["observed"] == 3 and by_scope["1948"]["status"] == "match"
    assert by_scope["1948"]["breakdown"] == {"solo": {"published": 2, "observed": 2}, "duet": {"published": 1, "observed": 1}}
    assert by_scope["Naushad"]["observed"] == 3 and by_scope["Naushad"]["status"] == "under"
    assert by_scope["non-film"]["status"] == "match"
    assert by_scope["unchecked"]["status"] == "unchecked"
    tier_a = r["tiers"]["A"]
    assert (tier_a["observed"], tier_a["f1"], tier_a["f2"], tier_a["lineages"]) == (3, 2, 1, ["atul", "hfgk"])
    # Two lineages: Chapman. hfgk saw 3, atul 1, both 1 -> (4 * 2 / 2) - 1 = 3: nothing missing.
    assert tier_a["method"] == "chapman" and tier_a["estimate"] == 3.0 and tier_a["meets_target"] is True
    assert r["tiers"]["B"]["estimate"] is None  # one lineage only
    assert r["containers"]["mismatched"] == 1 and r["containers"]["examples"][0]["stored"] == 3
    assert "| A | 3 |" in render_markdown(r)


def test_source_default_tier(store_for):
    s = store_for("eddy-arnold")
    me = s.maestro_entity()
    a = s.find_or_create_by_xref("recording", "musicbrainz", "r1", "Studio song")
    b = s.find_or_create_by_xref("recording", "musicbrainz", "r2", "Live song")
    for r in (a, b):
        s.upsert_claim(r, "title", value="x", source_id="musicbrainz")
        s.upsert_claim(r, "credit", ref_entity_id=me, value={"role": "singer"}, source_id="musicbrainz")
    s.upsert_claim(b, "live", value=True, source_id="musicbrainz")
    from maestro.completeness import load_units

    units = load_units(s)
    assert (units[a].tier, units[a].tier_default) == ("A", True)
    assert units[b].tier == "C"
    assert completeness_report(s)["tiers"]["A"]["default_tier"] == 1
