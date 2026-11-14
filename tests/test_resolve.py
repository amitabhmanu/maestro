"""Entity resolution and the review queue."""

import pytest

from maestro.resolve import (
    Normaliser,
    apply_groups,
    decide_review,
    export_reviews,
    fold,
    open_reviews,
    resolve,
    review_questions,
)


def person(s, source, xid, name):
    return s.find_or_create_by_xref("person", source, xid, name)


def song(s, source, xid, title, film=None, composer=None, rafi=True):
    u = s.find_or_create_by_xref("song", source, xid, title)
    if film:
        s.upsert_claim(u, "part_of", ref_entity_id=film, value={"position": 1}, source_id=source)
    if composer:
        s.upsert_claim(u, "credit", ref_entity_id=composer, value={"role": "composer"}, source_id=source)
    if rafi:
        s.upsert_claim(u, "credit", ref_entity_id=s.maestro_entity(), value={"role": "singer"}, source_id=source)
        s.upsert_claim(u, "tier", value="A", source_id="claude", kind="decided")
    return u


def film(s, source, xid, title, year):
    f = s.find_or_create_by_xref("container", source, xid, title)
    s.upsert_claim(f, "year", value=year, source_id=source)
    return f


def test_fold_and_normaliser():
    assert fold("Mohd. Rafi’s  Ghazal!") == "mohd rafis ghazal"
    assert fold("Café") == "cafe"
    n = Normaliser(translit={"tadpat": "tarpat"})
    assert n.title("Man Tadpat Hari Darshan Ko Aaj (Hindi)") == "man tarpat hari darshan ko aaj"


def test_cross_source_duplicates_merge_and_same_source_ones_queue(store_for):
    s = store_for("rafi")
    sdb_a = person(s, "hfgk_online", "10", "S. D. Burman")
    sdb_b = person(s, "mohdrafi", "name:S D Burman", "S D Burman")
    twin_a = person(s, "hfgk_online", "11", "Ravi")
    twin_b = person(s, "hfgk_online", "12", "Ravi")
    f1 = film(s, "hfgk_online", "100", "Baiju Bawra", 1952)
    f2 = film(s, "mohdrafi", "Baiju Bawra", "Baiju Bawra", 1952)
    u1 = song(s, "hfgk_online", "1000", "O Duniya Ke Rakhwale", f1, sdb_a)
    u2 = song(s, "mohdrafi", "o-duniya", "O Duniya Ke Rakhwale", f2, sdb_b)
    song(s, "hfgk_online", "1001", "Other Song", f1, twin_a)
    song(s, "hfgk_online", "1002", "Third Song", f1, twin_b)
    # Same name in one source but different roles: kept apart, sent to review.
    twin_c = person(s, "hfgk_online", "13", "Ravi")
    u3 = song(s, "hfgk_online", "1003", "Fourth Song", f1)
    s.upsert_claim(u3, "credit", ref_entity_id=twin_c, value={"role": "lyricist"}, source_id="hfgk_online")

    stats = resolve(s).as_dict()
    assert stats["merged"] == {"person": 2, "container": 1, "unit": 1}  # S. D. Burman, and the two composer Ravis
    assert stats["queued"] == {"person": 1}
    assert s.coverage_report()["tiers"]["A"]["observed"] == 4  # u2 merged into u1
    merged = s.query_graph("SELECT entity_id, ref_entity_id FROM claim WHERE field = 'same_as' ORDER BY id")
    assert (merged[-1]["entity_id"], merged[-1]["ref_entity_id"]) == (u2, u1)
    (item,) = open_reviews(s)
    assert item["kind"] == "same_person_same_source" and sorted(item["subject_ids"]) == sorted([twin_a, twin_c])

    # Re-running changes nothing.
    assert resolve(s).as_dict() == {"merged": {}, "queued": {}}


def test_near_names_go_to_review_and_decisions_stick(store_for):
    s = store_for("rafi")
    a = person(s, "hfgk_online", "20", "Naushad")
    b = person(s, "hfgk_online", "21", "Naushad Ali")
    f = film(s, "hfgk_online", "200", "Mela", 1948)
    for i, title in enumerate(["Song one", "Song two", "Song three"]):
        song(s, "hfgk_online", f"20{i}", title, f, a)
    song(s, "hfgk_online", "210", "Song four", f, b)
    resolve(s)
    (item,) = open_reviews(s, kind="person_near_name")
    assert item["evidence"]["labels"] == ["Naushad", "Naushad Ali"] and item["priority"] == 4

    claim = decide_review(s, item["id"], "merge", "same composer", keep_id=a)
    row = s.query_graph("SELECT entity_id, ref_entity_id, source_id FROM claim WHERE id = %(c)s", {"c": claim})[0]
    assert (row["entity_id"], row["ref_entity_id"], row["source_id"]) == (b, a, "user")
    with pytest.raises(ValueError, match="already decided"):
        decide_review(s, item["id"], "dismiss")
    resolve(s)
    assert open_reviews(s) == []


def test_export_gives_each_subject_its_label_sources_and_credits(store_for):
    s = store_for("rafi")
    a = person(s, "hfgk_online", "40", "Roshan")
    b = person(s, "hfgk_online", "41", "Roshan Lal")
    f = film(s, "hfgk_online", "400", "Bawre Nain", 1950)
    song(s, "hfgk_online", "401", "Khayalon Mein", f, a)
    song(s, "hfgk_online", "402", "Sun Bairi Balam", f, b)
    resolve(s)
    (item,) = export_reviews(s)
    assert item["kind"] == "person_near_name"
    subjects = {x["label"]: x for x in item["subjects"]}
    assert set(subjects) == {"Roshan", "Roshan Lal"}
    assert subjects["Roshan"]["sources"] == ["hfgk_online"] and subjects["Roshan"]["type"] == "person"
    assert any(k.startswith("credited as") for k in subjects["Roshan Lal"]["facts"])


def test_distinct_decision_is_not_queued_again(store_for):
    s = store_for("rafi")
    a = person(s, "hfgk_online", "30", "Ravi")
    b = person(s, "hfgk_online", "31", "Ravi Shankar Sharma")
    f = film(s, "hfgk_online", "300", "Film", 1960)
    song(s, "hfgk_online", "301", "One", f, a)
    song(s, "hfgk_online", "302", "Two", f, b)
    resolve(s)
    (item,) = open_reviews(s)
    decide_review(s, item["id"], "distinct")
    s.conn.execute("DELETE FROM review_item")  # even if the queue is lost, the claim keeps them apart
    resolve(s)
    assert open_reviews(s) == []


def test_titles_in_one_film_from_two_sources(store_for):
    s = store_for("rafi")
    f1 = film(s, "hfgk_online", "400", "Kohinoor", 1960)
    f2 = film(s, "musicbrainz", "rg:k", "Kohinoor", 1961)  # a soundtrack dated a year later
    full = song(s, "hfgk_online", "401", "Madhuban Mein Radhika Nache Re, Giridhar Ki Muraliya Baje Re", f1)
    song(s, "musicbrainz", "r1", "Madhuban Men Radhika Nache Re", f2)  # first line only, other spelling
    a = song(s, "hfgk_online", "402", "Dil Todne Wale Tujhe Dil Dhundh Raha Hai", f1)
    b = song(s, "musicbrainz", "r2", "Dil Tod Ke Jane Wale", f2)  # close, but not close enough to merge unseen
    song(s, "musicbrainz", "r3", "Koi Pyar Ki Dekhe Jaadugari", f2)  # nothing like it in HFGK
    stats = resolve(s).as_dict()
    assert stats["merged"] == {"container": 1, "unit": 1}
    assert stats["queued"] == {"unit": 1}
    (item,) = open_reviews(s)
    assert item["kind"] == "unit_near_title" and sorted(item["subject_ids"]) == sorted([a, b])
    assert s.coverage_report()["tiers"]["A"]["observed"] == 4  # the first-line record merged into the full one
    assert full in [u["id"] for u in s.query_graph("SELECT entity_id AS id FROM claim WHERE field = 'tier'")]


def test_title_similarity():
    from maestro.resolve import title_similarity

    assert title_similarity("chaudhvin ka chand ho", "chaudhvin ka chand ho ya aaftab ho") == 1.0
    assert title_similarity("chaudahavin ka chand ho", "chaudhvin ka chand ho ya aaftab ho") > 0.93
    assert title_similarity("pyar", "pyar kiya to darna kya") == 0.0
    assert title_similarity("tan rang lo ji", "tan ranglo ji aaj man ranglo") == 1.0
    assert title_similarity("jadugar qatil", "jaadoogar kaatil haazir hai meraa dil") == 1.0
    # A lead-in before the song proper, on either side.
    assert title_similarity("do din ki zindagi hai is par na phool pyaare",
                            "duniya chala chali ka mela do din ki zindagi hai") > 0.95
    assert title_similarity("ae dil hai mushkil", "ae dil mujhe bata de") < 0.8


def test_a_tie_goes_to_review(store_for):
    s = store_for("rafi")
    f1 = film(s, "hfgk_online", "600", "Barsat", 1949)
    f2 = film(s, "atulsongaday", "film:Barsat|1949", "Barsat", 1949)
    a = song(s, "hfgk_online", "601", "Hawa Mein Udta Jaye Mora Lal Dupatta Malmal Ka (Happy)", f1)
    b = song(s, "hfgk_online", "602", "Hawa Mein Udta Jaye Mora Lal Dupatta Malmal Ka (Sad)", f1)
    c = song(s, "atulsongaday", "x", "Hawa mein udta jaaye mora laal dupatta", f2, rafi=False)
    stats = resolve(s).as_dict()
    assert "unit" not in stats["merged"]
    assert {tuple(sorted(i["subject_ids"])) for i in open_reviews(s, kind="unit_near_title")} == {
        tuple(sorted((a, c))), tuple(sorted((b, c)))}


def test_versions_from_a_loose_source_join_one_song(store_for):
    # MusicBrainz (distinct_ids: false) keeps one recording per release version.
    s = store_for("rafi")
    f1 = film(s, "hfgk_online", "700", "Kohinoor", 1960)
    f2 = film(s, "musicbrainz", "release-group:k", "Kohinoor", 1960)
    h = song(s, "hfgk_online", "701", "Madhuban Mein Radhika Nache Re, Giridhar Ki Muraliya Baje Re", f1)
    song(s, "musicbrainz", "r1", "Madhuban Men Radhika Nache Re", f2)
    song(s, "musicbrainz", "r2", "Madhuban Men Radhika Nache Re (Revival)", f2)
    resolve(s)
    from maestro.resolve import Groups

    g = Groups(s)
    ids = [r["entity_id"] for r in s.query_graph(
        "SELECT entity_id FROM claim WHERE source_id = 'musicbrainz' AND field = 'xref'")]
    assert {g.find(i) for i in ids if i != f2} == {h}
    assert s.coverage_report()["tiers"]["A"]["observed"] == 1


def test_catalogue_key():
    from maestro.resolve import catalogue_key

    assert catalogue_key("LPM-3027") == catalogue_key("LPM 3027") == "LPM3027"
    assert catalogue_key("BCD 16953 AR") == catalogue_key("BCD-16953") == "BCD16953"
    assert catalogue_key("[none]") is None and catalogue_key("12") is None


def test_recordings_without_a_session_match_through_a_shared_catalogue_number(store_for):
    # Eddy Arnold: Praguefrank places takes in sessions and lists their issues; MusicBrainz knows
    # only the releases. Two recordings of one song are told apart by the issue they are on.
    s = store_for("eddy-arnold")

    def rec(source, xid, title, catalogues, session=None):
        r = s.find_or_create_by_xref("recording", source, xid, title)
        if session:
            s.upsert_claim(r, "part_of", ref_entity_id=session, value={"position": 1}, source_id=source)
        for c in catalogues:
            s.upsert_claim(r, "catalogue", value=c, source_id=source)
        s.upsert_claim(r, "credit", ref_entity_id=s.maestro_entity(), value={"role": "singer"}, source_id=source)
        return r

    s1 = s.find_or_create_by_xref("container", "praguefrank", "session:1", "1947 session")
    s2 = s.find_or_create_by_xref("container", "praguefrank", "session:2", "1965 session")
    old = rec("praguefrank", "seq:1", "BOUQUET OF ROSES", ["20-2806", "LPM 3027"], s1)
    new = rec("praguefrank", "seq:2", "BOUQUET OF ROSES", ["LSP-3361"], s2)
    mb_old = rec("musicbrainz", "r1", "Bouquet of Roses", ["LPM-3027"])
    mb_new = rec("musicbrainz", "r2", "Bouquet of Roses", ["LSP 3361"])
    mb_other = rec("musicbrainz", "r3", "Bouquet of Roses", ["XYZ-1"])
    resolve(s)
    from maestro.resolve import Groups

    g = Groups(s)
    assert g.find(mb_old) == g.find(old) and g.find(mb_new) == g.find(new)
    assert g.find(old) != g.find(new) and g.find(mb_other) == mb_other


def test_sessions_on_different_days_stay_apart(store_for):
    s = store_for("eddy-arnold")
    a = s.find_or_create_by_xref("container", "praguefrank", "s1", "5 February 1962 RCA Victor Studio, Nashville")
    b = s.find_or_create_by_xref("container", "praguefrank", "s2", "23 February 1962 RCA Victor Studio, Nashville")
    for c, d in ((a, "1962-02-05"), (b, "1962-02-23")):
        s.upsert_claim(c, "year", value=1962, source_id="praguefrank")
        s.upsert_claim(c, "date", value=d, source_id="praguefrank")
    resolve(s)
    assert open_reviews(s, kind="same_container_same_source") == []


def test_floating_recordings_match_by_title_across_the_canon(store_for):
    s = store_for("eddy-arnold")
    me = s.maestro_entity()

    def session(xid, year):
        c = s.find_or_create_by_xref("container", "praguefrank", xid, f"session {xid}")
        s.upsert_claim(c, "container_kind", value="session", source_id="praguefrank")
        s.upsert_claim(c, "year", value=year, source_id="praguefrank")
        return c

    def take(xid, title, sess, tier="A"):
        r = s.find_or_create_by_xref("recording", "praguefrank", xid, title)
        s.upsert_claim(r, "part_of", ref_entity_id=sess, value={"position": 1}, source_id="praguefrank")
        s.upsert_claim(r, "credit", ref_entity_id=me, value={"role": "singer"}, source_id="praguefrank")
        s.upsert_claim(r, "tier", value=tier, source_id="claude", kind="decided")
        return r

    def mb(xid, title, released):
        r = s.find_or_create_by_xref("recording", "musicbrainz", xid, title)
        s.upsert_claim(r, "title", value=title, source_id="musicbrainz")
        s.upsert_claim(r, "credit", ref_entity_id=me, value={"role": "singer"}, source_id="musicbrainz")
        s.upsert_claim(r, "release_year", value=released, source_id="musicbrainz")
        return r

    s44, s55, s65 = session("s44", 1944), session("s55", 1955), session("s65", 1965)
    roving = take("1", "THE ROVIN' GAMBLER", s44)
    call44, call55 = take("2", "CATTLE CALL", s44), take("3", "THE CATTLE CALL", s55)
    world = take("4", "TURN THE WORLD AROUND", s65)
    m_roving = mb("a", "Roving Gambler", 1990)
    m_call = mb("b", "Cattle Call", 1990)       # two session recordings fit: review
    m_early = mb("c", "Cattle Call", 1950)      # released before the 1955 remake: only 1944 fits
    m_other = mb("d", "Turn the World Around the Other Way", 1990)  # another song
    resolve(s)
    from maestro.completeness import load_units
    from maestro.resolve import Groups

    g = Groups(s)
    assert g.find(m_roving) == g.find(roving) and g.find(m_early) == g.find(call44)
    assert g.find(m_call) == m_call and g.find(m_other) == m_other and g.find(world) == world
    pairs = {tuple(sorted(i["subject_ids"])) for i in open_reviews(s, kind="unit_same_title")}
    assert pairs == {tuple(sorted((m_call, g.find(call44)))), tuple(sorted((m_call, call55)))}
    units = load_units(s, g)
    assert units[m_call].pending_match and not units[m_other].pending_match
    assert "musicbrainz" in units[call55].lineages or "musicbrainz" in units[g.find(call44)].lineages

    # Grouped review asks "which one is it?" once, and picking one leaves the candidates apart.
    (q,) = review_questions(s)
    assert q["floating"] == m_call and {m["id"] for m in q["members"]} == {m_call, g.find(call44), call55}
    assert apply_groups(s, [p["item"] for p in q["pairs"]], [[m_call, call55], [call44]]) == {"merge": 1, "distinct": 1}
    g = Groups(s)
    assert g.find(m_call) == g.find(call55) != g.find(call44)
    assert open_reviews(s) == []


def test_a_floating_unit_matched_since_closes_its_other_questions(store_for):
    s = store_for("eddy-arnold")
    me = s.maestro_entity()
    sessions = []
    for xid, year in (("s44", 1944), ("s55", 1955)):
        c = s.find_or_create_by_xref("container", "praguefrank", xid, f"session {xid}")
        s.upsert_claim(c, "container_kind", value="session", source_id="praguefrank")
        s.upsert_claim(c, "year", value=year, source_id="praguefrank")
        r = s.find_or_create_by_xref("recording", "praguefrank", xid + "r", "CATTLE CALL")
        s.upsert_claim(r, "part_of", ref_entity_id=c, value={"position": 1}, source_id="praguefrank")
        s.upsert_claim(r, "credit", ref_entity_id=me, value={"role": "singer"}, source_id="praguefrank")
        sessions.append(r)
    m = s.find_or_create_by_xref("recording", "musicbrainz", "m", "Cattle Call")
    s.upsert_claim(m, "credit", ref_entity_id=me, value={"role": "singer"}, source_id="musicbrainz")
    s.upsert_claim(m, "release_year", value=1990, source_id="musicbrainz")
    resolve(s)
    assert len(open_reviews(s)) == 2
    s.merge_entities(sessions[0], m, "same matrix")  # matched another way before anyone answered
    resolve(s)
    assert open_reviews(s) == []
    from maestro.resolve import Groups

    g = Groups(s)
    assert g.find(sessions[0]) != g.find(sessions[1])


def test_grouped_review_keeps_a_chain_whole_and_refuses_contradictions(store_for):
    s = store_for("rafi")
    short = person(s, "hfgk_online", "50", "Krishna")
    k1 = person(s, "hfgk_online", "51", "Krishna Mukherjee")
    k2 = person(s, "hfgk_online", "52", "Krishna Ganguli")
    f = film(s, "hfgk_online", "500", "Film", 1950)
    titles = ["Aaja Re", "Bahaaron Phool", "Chaudhvin Ka Chand", "Dil Ka Bhanwar", "Ehsan Tera"]
    for i, who in enumerate([short, k1, k1, k2, k2]):
        u = song(s, "hfgk_online", f"50{i}", titles[i], f)
        s.upsert_claim(u, "credit", ref_entity_id=who, value={"role": "singer"}, source_id="hfgk_online")
    resolve(s)
    assert len(open_reviews(s, kind="person_near_name")) == 2
    (q,) = review_questions(s)
    assert {m["label"] for m in q["members"]} == {"Krishna", "Krishna Mukherjee", "Krishna Ganguli"}
    items = [p["item"] for p in q["pairs"]]
    with pytest.raises(ValueError, match="needs a group"):
        apply_groups(s, items, [[short, k2]])
    assert apply_groups(s, items, [[short, k2], [k1]]) == {"merge": 1, "distinct": 1}
    from maestro.resolve import Groups

    g = Groups(s)
    assert g.find(short) == g.find(k2) != g.find(k1)
    # An answer that contradicts a recorded "distinct" writes nothing.
    k3 = person(s, "hfgk_online", "53", "Krishna Goyal")
    with pytest.raises(ValueError, match="marked distinct"):
        apply_groups(s, [], [[k1, short, k3]])


def test_whole_title_index_matches_brute_force():
    import random

    from maestro.resolve import WholeTitleIndex, whole_title_similarity

    rng = random.Random(7)
    words = ["dil", "mera", "tera", "pyar", "the", "roving", "gambler", "jaane", "kahan", "gaye", "woh", "din"]
    titles = {i: {" ".join(rng.choice(words) for _ in range(rng.randint(1, 5)))} for i in range(300)}
    index = WholeTitleIndex(titles)
    for q in list(titles.values())[:60]:
        want = {i: max(whole_title_similarity(a, b) for a in q for b in ts) for i, ts in titles.items()}
        want = {i: v for i, v in want.items() if v >= 0.9}
        got = index.matches(q, 0.9)
        assert got.keys() == want.keys() and all(abs(got[i] - want[i]) < 1e-9 for i in got)
