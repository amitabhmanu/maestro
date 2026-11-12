import pytest

from maestro.store import StoreError


def credit(store, unit, role="singer"):
    return store.upsert_claim(unit, "credit", ref_entity_id=store.maestro_entity(),
                              value={"role": role}, source_id="hindigeetmala")


def test_claims_are_deduplicated_and_keep_provenance(store_for):
    s = store_for("rafi")
    song = s.create_entity("song", "Chaudhvin Ka Chand")
    a = s.upsert_claim(song, "year", value=1960, source_id="hindigeetmala")
    b = s.upsert_claim(song, "year", value=1960, source_id="hindigeetmala")
    c = s.upsert_claim(song, "year", value=1961, source_id="wikipedia")
    assert a == b and c != a
    rows = s.query_graph("SELECT value, source_id FROM claim WHERE field = 'year' ORDER BY id")
    assert [(r["value"], r["source_id"]) for r in rows] == [(1960, "hindigeetmala"), (1961, "wikipedia")]


def test_unknown_source_is_refused(store_for):
    s = store_for("rafi")
    song = s.create_entity("song", "x")
    with pytest.raises(StoreError, match="unknown source"):
        s.upsert_claim(song, "year", value=1960, source_id="nowhere")


def test_private_flag_and_visibility(store_for):
    s = store_for("rafi")
    song = s.create_entity("song", "x")
    s.upsert_claim(song, "first_line", value="...", source_id="hindigeetmala", private=True)
    s.upsert_claim(song, "year", value=1960, source_id="hindigeetmala")
    vis = {r["field"]: r["visibility"] for r in s.query_graph("SELECT field, visibility FROM claim WHERE entity_id = %(e)s", {"e": song})}
    assert vis == {"first_line": "private", "year": "publishable"}


def test_query_graph_is_read_only(store_for):
    s = store_for("rafi")
    with pytest.raises(Exception, match="read-only"):
        s.query_graph("DELETE FROM claim")


def test_coverage_counts_canon_units_by_tier(store_for):
    s = store_for("rafi")
    hit, other, not_rafi = (s.create_entity("song", n) for n in ("a", "b", "c"))
    credit(s, hit)
    credit(s, other)
    s.upsert_claim(not_rafi, "year", value=1955, source_id="wikipedia")
    s.upsert_claim(hit, "tier", value="A", source_id="claude", kind="decided")
    report = s.coverage_report()
    assert report["tiers"]["A"]["observed"] == 1
    assert report["unassigned"] == 1
    assert report["units_per_lineage"]["hfgk"] == 2


def test_merged_entities_leave_the_canon(store_for):
    s = store_for("rafi")
    a, b = s.create_entity("song", "Chaudhvin Ka Chand"), s.create_entity("song", "Chaudhavin Ka Chand")
    credit(s, a)
    credit(s, b)
    s.upsert_claim(a, "tier", value="A", source_id="claude", kind="decided")
    s.upsert_claim(b, "tier", value="A", source_id="claude", kind="decided")
    s.merge_entities(a, b, "transliteration variant")
    assert s.coverage_report()["tiers"]["A"]["observed"] == 1


def test_gaps(store_for):
    s = store_for("rafi")
    film = s.create_entity("container", "Chaudhvin Ka Chand (1960)")
    empty_film = s.create_entity("container", "Some 1948 film")
    for f in (film, empty_film):
        s.upsert_claim(f, "credit", ref_entity_id=s.maestro_entity(), value={"role": "singer"}, source_id="wikidata")
    song = s.create_entity("song", "Chaudhvin Ka Chand")
    credit(s, song)
    s.upsert_claim(song, "part_of", ref_entity_id=film, source_id="hindigeetmala")
    kinds = {(g["kind"], g["entity_id"]) for g in s.find_gaps()}
    assert ("empty_container", empty_film) in kinds
    assert ("empty_container", film) not in kinds
    assert ("single_lineage", song) in kinds
    assert ("no_tier", song) in kinds


def test_no_container_gaps_without_full_listings(store_for):
    s = store_for("jean-wiener")
    score = s.create_entity("container", "Some film score")
    s.upsert_claim(score, "credit", ref_entity_id=s.maestro_entity(), value={"role": "composer"}, source_id="bnf")
    assert not [g for g in s.find_gaps() if g["kind"] == "empty_container"]


def test_crawl_queue_dedupes_and_enforces_budget(store_for):
    from maestro.pack import Pack
    from conftest import ROOT
    from maestro.pack import load_pack

    base = load_pack(ROOT / "maestros" / "rafi")
    data = {**base.data, "budget": {**base.data["budget"], "crawl_requests_per_round": 2}}
    s = store_for("rafi", pack=Pack(path=base.path, data=data))
    j1 = s.enqueue_crawl("https://example.org/a", "hgm", "spine")
    again = s.enqueue_crawl("https://example.org/a", "hgm", "spine")
    assert again["id"] == j1["id"] and again["already_queued"]
    s.enqueue_crawl("https://example.org/b", "hgm", "spine")
    with pytest.raises(StoreError, match="budget"):
        s.enqueue_crawl("https://example.org/c", "hgm", "gap", gap_id="x")


def test_non_utf8_database_is_refused(pg_server):
    import psycopg

    from conftest import ROOT
    from maestro.pack import load_pack
    from maestro.store import Store

    with psycopg.connect(pg_server, autocommit=True) as admin:
        admin.execute('DROP DATABASE IF EXISTS "t_ascii"')
        admin.execute("CREATE DATABASE \"t_ascii\" ENCODING 'SQL_ASCII' TEMPLATE template0")
    try:
        with pytest.raises(StoreError, match="UTF8"):
            Store.connect(pg_server.rsplit("/", 1)[0] + "/t_ascii", load_pack(ROOT / "maestros" / "rafi"))
    finally:
        with psycopg.connect(pg_server, autocommit=True) as admin:
            admin.execute('DROP DATABASE IF EXISTS "t_ascii"')
