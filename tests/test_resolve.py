"""Entity resolution and the review queue."""

import pytest

from maestro.resolve import Normaliser, decide_review, fold, open_reviews, resolve


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
