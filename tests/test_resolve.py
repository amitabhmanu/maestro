"""Entity resolution and the review queue."""

import pytest

from maestro.resolve import (
    Groups,
    Normaliser,
    apply_groups,
    decide_review,
    export_reviews,
    fold,
    open_reviews,
    resolve,
    review_questions,
    unlink_non_people,
)


def person(s, source, xid, name):
    return s.find_or_create_by_xref("person", source, xid, name)


def song(s, source, xid, title, film=None, composer=None, rafi=True, tier="A"):
    u = s.find_or_create_by_xref("song", source, xid, title)
    if film:
        s.upsert_claim(u, "part_of", ref_entity_id=film, value={"position": 1}, source_id=source)
    if composer:
        s.upsert_claim(u, "credit", ref_entity_id=composer, value={"role": "composer"}, source_id=source)
    if rafi:
        s.upsert_claim(u, "credit", ref_entity_id=s.maestro_entity(), value={"role": "singer"}, source_id=source)
        if tier:
            s.upsert_claim(u, "tier", value=tier, source_id="claude", kind="decided")
    return u


def film(s, source, xid, title, year):
    f = s.find_or_create_by_xref("container", source, xid, title)
    if year is not None:
        s.upsert_claim(f, "year", value=year, source_id=source)
    return f


def test_fold_and_normaliser():
    assert fold("Mohd. Rafi’s  Ghazal!") == "mohd rafis ghazal"
    assert fold("Café") == "cafe"
    n = Normaliser(translit={"tadpat": "tarpat"})
    assert n.title("Man Tadpat Hari Darshan Ko Aaj (Hindi)") == "man tarpat hari darshan ko aaj"


def test_lookalike_letters_fold_to_latin():
    # Cyrillic letters inside a Latin name ("Chitragupт") fold as their Latin twins.
    assert fold("Chitragupт") == fold("Chitragupt") == "chitragupt"
    assert fold("Nirupа Roy") == "nirupa roy"
    assert fold("Москва") == "москва"  # a Cyrillic word stays


def test_pack_people_aliases_set_the_key_and_the_name(store_for):
    n = Normaliser.for_store(store_for("rafi"))
    assert n.person("Manna De") == n.person("Mannade") == n.person("Manna Dey") == "manna dey"
    assert n.person_name("MANNA DE") == "Manna Dey" and n.person_name("Manna Dey") == "Manna Dey"
    assert n.person("Suman [?]") == "suman kalyanpur" and n.person_name("Suman [?]") == "Suman Kalyanpur"
    assert n.person_name("Suman [2]") is None and n.person("Suman [2]") == "suman 2"


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
    a = person(s, "hfgk_online", "30", "Roshan")
    b = person(s, "hfgk_online", "31", "Roshan Lal")
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


def test_catalogue_numbers_from_an_enrich_source_do_not_match_units(store_for):
    # Discogs in enrich mode tags existing units with a compilation's number by title likeness;
    # that number must not then pull two different songs together.
    s = store_for("eddy-arnold")
    assert next(x for x in s.pack.data["sources"] if x["id"] == "discogs")["unit_mode"] == "enrich"
    a = s.find_or_create_by_xref("recording", "praguefrank", "seq:1", "Mujhe Kitna Pyar Hai")
    b = s.find_or_create_by_xref("recording", "musicbrainz", "r1", "Kitna Pyara Vada Hai")
    for r, src in ((a, "praguefrank"), (b, "musicbrainz")):
        s.upsert_claim(r, "credit", ref_entity_id=s.maestro_entity(), value={"role": "singer"}, source_id=src)
        s.upsert_claim(r, "catalogue", value="CDF 131011", source_id="discogs")
    resolve(s)
    assert not s.conn.execute("SELECT 1 FROM claim WHERE field = 'same_as' AND status = 'active'").fetchall()
    assert not open_reviews(s)


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


def test_title_similarity_floor_is_exact_at_or_above_it():
    import random

    from maestro.resolve import title_similarity

    rng = random.Random(3)
    words = ["dil", "deewana", "mera", "tera", "pyar", "jaane", "kahan", "gaye", "woh", "din", "o", "duniya",
             "ke", "rakhwale", "sun", "dard", "bhare", "mere", "naale", "chaudhvin", "ka", "chand"]
    for _ in range(1500):
        a = " ".join(rng.choice(words) for _ in range(rng.randint(2, 7)))
        b = " ".join(rng.choice(words) for _ in range(rng.randint(2, 7)))
        if rng.random() < 0.3:
            b = a.replace("a", "aa", 1)
        exact, fast = title_similarity(a, b), title_similarity(a, b, 0.8)
        assert (fast == exact) if exact >= 0.8 else fast < 0.8


def test_reissued_soundtracks_join_their_film_and_never_date_a_song(store_for):
    """Rafi died in 1980: a soundtrack MusicBrainz dates 2017 is the 1960 film put out again, a CD of two
    old films is no film, and a late date counts only when two sources give it (a shelved film released late)."""
    from maestro.completeness import container_years, load_units

    s = store_for("rafi")
    assert s.pack.data["identity"]["reissues_after"] == 1986
    old = film(s, "hfgk_online", "300", "Saranga", 1960)
    reissue = film(s, "musicbrainz", "release-group:a", "Saranga", 2017)
    combo = film(s, "musicbrainz", "release-group:b", "Aar Paar / Mr. & Mrs. 55", 2000)
    shelved = film(s, "hfgk_online", "301", "Rat Ki Uljhan", None)
    shelved_mb = film(s, "musicbrainz", "release-group:c", "Rat Ki Uljhan", 2024)
    late_film = film(s, "hfgk_online", "302", "Love And God", 1986)
    aahat = film(s, "hfgk_online", "303", "Aahat", 2010)
    aahat_mb = film(s, "musicbrainz", "release-group:e", "Aahat", 2011)
    song(s, "hfgk_online", "3000", "Saranga Teri Yaad Mein", old)
    u_re = song(s, "musicbrainz", "rec-1", "Saranga Teri Yaad Mein", reissue)
    u_combo = song(s, "musicbrainz", "rec-2", "Udhar Tum Haseen Ho", combo)
    u_shelved = song(s, "hfgk_online", "3010", "Gul Khilen Ya Na Khilen", shelved)
    u_late = song(s, "hfgk_online", "3020", "Allah Tere Sath Hai", late_film)

    resolve(s)
    groups = Groups(s)
    assert groups.find(reissue) == groups.find(old)
    assert groups.find(shelved_mb) == groups.find(shelved)  # no year on one side: merged as before
    years = container_years(s, groups)
    assert years[groups.find(old)] == 1960
    assert groups.find(combo) not in years
    assert groups.find(shelved) not in years  # HFGK lists it undated: 2024 is only MusicBrainz's reissue
    assert groups.find(aahat) == groups.find(aahat_mb)
    assert years[groups.find(aahat)] == 2010  # two sources date it: a shelved film released late
    units = load_units(s, groups)
    assert units[groups.find(u_re)].year == 1960
    assert units[groups.find(u_combo)].year is None
    assert units[groups.find(u_shelved)].year is None
    assert units[groups.find(u_late)].year == 1986


def test_a_late_soundtrack_with_two_earlier_films_of_its_title_stays_apart(store_for):
    s = store_for("rafi")
    a = film(s, "hfgk_online", "400", "Naya Daur", 1957)
    b = film(s, "hfgk_online", "401", "Naya Daur", 1978)
    re = film(s, "musicbrainz", "release-group:d", "Naya Daur", 2003)
    resolve(s)
    g = Groups(s)
    assert g.find(re) not in {g.find(a), g.find(b)}


def test_a_recording_reused_in_a_later_film_is_the_original_song(store_for):
    """'Aaj Mausam Bada Beimaan Hai' (Loafer, 1973) in Monsoon Wedding (2001) is not a new song; a
    shelved film's song with no earlier twin stays; two earlier twins go to review."""
    from maestro.completeness import load_units

    s = store_for("rafi")
    loafer = film(s, "hfgk_online", "500", "Loafer", 1973)
    orig = song(s, "hfgk_online", "5000", "Aaj Mausam Bada Beimaan Hai, Bada Beimaan Hai Aaj Mausam", loafer)
    monsoon = film(s, "musicbrainz", "release-group:m", "Monsoon Wedding", 2001)
    film(s, "atulsongaday", "Monsoon Wedding", "Monsoon Wedding", 2001)
    reused = song(s, "musicbrainz", "rec-m", "Aaj Mausam Bada Beimann Hai (Today the Weather Plays Tricks on Me)",
                  monsoon, tier=None)
    shelved = film(s, "hfgk_online", "501", "Aahat", 2010)
    film(s, "musicbrainz", "release-group:n", "Aahat", 2010)
    late_only = song(s, "hfgk_online", "5010", "Zamana Hai Pyar Ka Suno Dilwalo", shelved, tier="C")
    # A shelved film's own song (HFGK gives it a tier) is never matched away, even to a like title.
    zamana = film(s, "mohdrafi", "Zamana", "Zamana", 1960)
    lookalike = song(s, "mohdrafi", "zamana-1", "Zamana Hai Pyar Ka Suno Dilwalo Re", zamana)
    a = film(s, "hfgk_online", "502", "Dosti", 1964)
    b = film(s, "hfgk_online", "503", "Dost", 1974)
    song(s, "hfgk_online", "5020", "Gadi Bula Rahi Hai Seeti Baja Rahi Hai", a)
    song(s, "hfgk_online", "5021", "Gadi Bula Rahi Hai Seeti Baja Rahi Hai", b)
    later = film(s, "musicbrainz", "release-group:o", "Train Songs", 2005)
    twin = song(s, "musicbrainz", "rec-o", "Gadi Bula Rahi Hai Seeti Baja Rahi Hai", later, tier=None)
    loose = song(s, "musicbrainz", "rec-p", "Gadi Chali", later, tier=None)

    resolve(s)
    g = Groups(s)
    assert g.find(reused) == g.find(orig)
    units = load_units(s, g)
    assert units[g.find(orig)].year == 1973
    assert units[g.find(late_only)].year == 2010
    assert g.find(late_only) != g.find(lookalike)
    reused_reviews = [i for i in open_reviews(s, limit=100) if i["kind"] == "unit_reused"]
    assert len(reused_reviews) == 2 and all(g.find(twin) in i["subject_ids"] for i in reused_reviews)
    assert not any(g.find(loose) in i["subject_ids"] for i in open_reviews(s, limit=100))


def test_reuse_matching_respects_credits_named_films_and_original_years(store_for):
    """A later film's song with no Rafi credit is not matched; a CD named after two films only gives
    songs of those films; a merged late copy's own year never dates the original."""
    from maestro.completeness import load_units

    s = store_for("rafi")
    bhai = film(s, "hfgk_online", "600", "Bhai Bahen", 1969)
    orig = song(s, "hfgk_online", "6000", "Dheere Dheere Muhabbat Javan Ho Gaee", bhai)
    aashiqui = film(s, "atulsongaday", "Aashiqui", "Aashiqui", 1990)
    film(s, "musicbrainz", "release-group:q", "Aashiqui", 1990)
    other = song(s, "atulsongaday", "dheere", "Dheere Dheere Muhabbat Javan Ho Gaee", aashiqui, rafi=False, tier=None)
    s.upsert_claim(other, "year", value=1990, source_id="atulsongaday")

    ddd = film(s, "hfgk_online", "601", "Dil Deke Dekho", 1959)
    song(s, "hfgk_online", "6010", "Bolo Bolo Kuchh To Bolo Jee", ddd)
    film(s, "hfgk_online", "602", "Zamane Ko Dikhana Hai", 1981)
    combo = film(s, "musicbrainz", "release-group:r", "Zamaane Ko Dikhana Hain / Abdullah", 1996)
    cd_song = song(s, "musicbrainz", "rec-r", "Bolo Bolo Kuchh To Bolo Jee", combo, tier=None)

    loafer = film(s, "hfgk_online", "603", "Loafer", 1973)
    aaj = song(s, "hfgk_online", "6030", "Aaj Mausam Bada Beimaan Hai", loafer)
    monsoon = film(s, "musicbrainz", "release-group:s", "Monsoon Wedding", 2001)
    copy = song(s, "musicbrainz", "rec-s", "Aaj Mausam Bada Beimaan Hai", monsoon, tier=None)
    s.upsert_claim(copy, "year", value=2001, source_id="musicbrainz")

    resolve(s)
    g = Groups(s)
    assert g.find(other) != g.find(orig)
    assert g.find(cd_song) not in {g.find(x) for x in (orig, aaj)}
    assert any(i["kind"] == "unit_reused" and g.find(cd_song) in i["subject_ids"] for i in open_reviews(s, limit=100))
    assert g.find(copy) == g.find(aaj)
    units = load_units(s, g)
    assert units[g.find(aaj)].year == 1973
    assert units[g.find(orig)].year == 1969


def test_credits_to_a_chorus_become_unlinked_credits(store_for):
    s = store_for("rafi")
    korus = person(s, "hfgk_online", "28083", "Korus")
    suman = person(s, "hfgk_online", "4777", "Suman Kalyanpur")
    a = song(s, "hfgk_online", "100", "Song A")
    for p in (korus, suman):
        s.upsert_claim(a, "credit", ref_entity_id=p, value={"role": "singer"}, source_id="hfgk_online")
    assert resolve(s).as_dict()["unlinked_credits"] == 1
    active = s.conn.execute("SELECT field, value, ref_entity_id FROM claim WHERE entity_id = %s AND status = 'active'"
                            " AND field LIKE 'credit%%'", (a,)).fetchall()
    assert {"field": "credit_unlinked", "value": {"role": "singer", "name": "Chorus"}, "ref_entity_id": None} in active
    assert not any(r["ref_entity_id"] == korus for r in active)
    assert any(r["ref_entity_id"] == suman for r in active)
    # A re-crawl that credits Korus again is turned once more; the unlinked claim is not doubled.
    s.upsert_claim(a, "credit", ref_entity_id=korus, value={"role": "singer"}, source_id="hfgk_online")
    assert unlink_non_people(s) == 1
    assert s.conn.execute("SELECT count(*) AS n FROM claim WHERE entity_id = %s AND field = 'credit_unlinked'"
                          " AND status = 'active'", (a,)).fetchone()["n"] == 1


def test_shared_hindi_film_lexicon_applies_after_the_packs_own(store_for):
    # Rafi's pack lists ../hindi-films/lexicon/people.tsv after its own table: "Ravi Shankar Sharma"
    # is Ravi there, so the two HFGK records merge without a review.
    s = store_for("rafi")
    a = person(s, "hfgk_online", "30", "Ravi")
    b = person(s, "hfgk_online", "31", "Ravi Shankar Sharma")
    f = film(s, "hfgk_online", "300", "Film", 1960)
    song(s, "hfgk_online", "301", "One", f, a)
    song(s, "hfgk_online", "302", "Two", f, b)
    resolve(s)
    assert open_reviews(s) == []
    assert Normaliser.for_store(s).person("Manna De") == Normaliser.for_store(s).person("Manna Dey")
