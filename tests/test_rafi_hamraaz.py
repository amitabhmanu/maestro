"""Rafi's HFGK Online extractor, against synthetic pages shaped like the real site."""

import importlib.util
from pathlib import Path

from conftest import ROOT
from maestro.crawl.fetcher import Page
from maestro.crawl.runner import ExtractContext

FIX = Path(__file__).parent / "fixtures" / "hamraaz"
spec = importlib.util.spec_from_file_location("hamraaz", ROOT / "maestros" / "rafi" / "extractors" / "hamraaz.py")
hamraaz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hamraaz)


def page(name, url):
    body = (FIX / name).read_bytes()
    return Page(url=url, status=200, body=body, sha256="x", path=FIX / name)


def test_parse_person():
    films = hamraaz.parse_person((FIX / "person.html").read_text("utf-8"))
    assert [(f["id"], f["year"], f["title"]) for f in films] == [
        ("11", 1947, "Film One"), ("12", 1955, "Film Two"), ("13", None, "Shelved Film")]
    assert films[0]["title_hi"] == "फ़िल्म एक" and films[1]["title_hi"] is None
    assert films[1]["roles"] == ["Artists", "singer"]
    assert films[2]["period"].startswith("Sixties")


def test_parse_film():
    film = hamraaz.parse_film((FIX / "film.html").read_text("utf-8"))
    assert film["title"] == "Film One" and film["details"]["Year"] == "1947"
    assert film["crew"] == [
        {"role": "director", "person_id": "501", "name": "Some Director"},
        {"role": "composer", "person_id": "502", "name": "Some Composer"},
    ]
    first, second = film["songs"]
    assert (first["number"], first["title"], first["title_hi"]) == (1, "Pehla Geet, Duet Wala", "पहला गीत, डुएट वाला")
    assert [(c["role"], c["person_id"]) for c in first["credits"]] == [
        ("singer", "4357"), ("singer", "601"), ("lyricist", "503"), ("composer", "502")]
    assert first["on_screen"] == [{"person_id": "701", "name": "Lead Actor"}]
    assert second["title_hi"] is None


def test_person_page_queues_films_in_year_range(store_for):
    s = store_for("rafi")
    raw = s.record_raw_page("u", 200, "x", "p")
    ctx = ExtractContext(s, {}, page("person.html", hamraaz.BASE + "person.php?id=4357"), raw, {"years": "1944-1950"})
    hamraaz.extract(ctx)
    urls = [r["url"] for r in s.query_graph("SELECT url FROM job ORDER BY id")]
    assert urls == [hamraaz.BASE + "film.php?id=11"]


def test_maestro_filmography_records_roles_and_gaps(store_for):
    s = store_for("rafi")
    raw = s.record_raw_page("u", 200, "x", "p")
    hamraaz.extract(ExtractContext(s, {}, page("person.html", hamraaz.BASE + "person.php?id=4357"), raw))
    roles = s.query_graph(
        """SELECT e.label, c.value ->> 'role' AS role FROM claim c JOIN entity e ON e.id = c.entity_id
           WHERE c.field = 'credit' ORDER BY e.id, role""")
    assert [(r["label"], r["role"]) for r in roles] == [
        ("Film One", "singer"), ("Film Two", "actor"), ("Film Two", "singer"), ("Shelved Film", "singer")]
    # No film page fetched yet: every film is a gap. Once Film One is read it holds Rafi's song.
    assert len([g for g in s.find_gaps() if g["kind"] == "empty_container"]) == 3
    hamraaz.extract(ExtractContext(s, {}, page("film.html", hamraaz.BASE + "film.php?id=11"), raw))
    gaps = [g["label"] for g in s.find_gaps() if g["kind"] == "empty_container"]
    assert gaps == ["Film Two", "Shelved Film"]


def test_film_page_writes_canon(store_for):
    s = store_for("rafi")
    raw = s.record_raw_page("u", 200, "x", "p")
    hamraaz.extract(ExtractContext(s, {}, page("film.html", hamraaz.BASE + "film.php?id=11"), raw))
    hamraaz.extract(ExtractContext(s, {}, page("film.html", hamraaz.BASE + "film.php?id=11"), raw))  # idempotent
    report = s.coverage_report()
    assert report["tiers"]["A"]["observed"] == 1  # only the song Rafi sings
    assert report["unassigned"] == 0
    songs = s.query_graph("SELECT count(*) AS n FROM entity WHERE type = 'song'")[0]["n"]
    assert songs == 2
    unlinked = s.query_graph("SELECT value FROM claim WHERE field = 'credit_unlinked'")
    assert [r["value"] for r in unlinked] == [{"role": "singer", "name": "Others"}]
    on_screen = s.query_graph(
        "SELECT e.label FROM claim c JOIN entity e ON e.id = c.ref_entity_id WHERE c.field = 'on_screen'")
    assert [r["label"] for r in on_screen] == ["Lead Actor"]
    # The maestro carries the database's person id, and the film credits him.
    assert s.entity_by_xref("hfgk_online", "4357") == s.maestro_entity()
    assert not [g for g in s.find_gaps() if g["kind"] == "empty_container"]


def test_film_and_person_with_the_same_id_stay_apart(store_for):
    # HFGK numbers films and people separately: film 502 is not person 502 (the film's composer).
    s = store_for("rafi")
    raw = s.record_raw_page("u", 200, "x", "p")
    hamraaz.extract(ExtractContext(s, {}, page("film.html", hamraaz.BASE + "film.php?id=502"), raw))
    film = s.entity_by_xref("hfgk_online", "502", "container")
    composer = s.entity_by_xref("hfgk_online", "502", "person")
    assert film and composer and film != composer
    types = s.query_graph("SELECT type FROM entity WHERE id IN (%(a)s, %(b)s) ORDER BY type", {"a": film, "b": composer})
    assert [r["type"] for r in types] == ["container", "person"]


def test_replay_rebuilds_a_store_from_the_cache(store_for, tmp_path):
    from maestro.crawl.runner import import_page, replay

    old, new = store_for("rafi"), store_for("rafi")
    raw = old.record_raw_page(hamraaz.BASE + "film.php?id=11", 200, "x", str(FIX / "film.html"))
    job = old.enqueue_crawl(hamraaz.BASE + "film.php?id=11", "hamraaz", "container_sweep")
    old.conn.execute("UPDATE job SET status = 'done', raw_page_id = %s WHERE id = %s", (raw, job["id"]))
    import_page(old, "mohdrafi", "http://www.mohdrafi.com/x", ROOT / "tests" / "fixtures" / "mohdrafi" / "language.tsv",
                tmp_path, {"tier": "B", "language": "Telugu"})
    import_page(old, "mohdrafi", "http://www.mohdrafi.com/x", ROOT / "tests" / "fixtures" / "mohdrafi" / "language.tsv",
                tmp_path, {"tier": "B", "language": "Telugu"})  # the same file again: replayed once
    stats = replay(old, new)
    assert stats.as_dict() == {"done": 2, "failed": 0, "skipped": 0}
    assert new.coverage_report()["tiers"]["A"]["observed"] == 1
    assert new.coverage_report()["tiers"]["B"]["observed"] == old.coverage_report()["tiers"]["B"]["observed"] > 0
    assert new.query_graph("SELECT count(*) AS n FROM job WHERE status = 'queued'")[0]["n"] == 0
    # The new store can be replayed in turn.
    assert len(new.query_graph("SELECT id FROM job")) == 1
    assert len(new.query_graph("SELECT id FROM import_log")) == 1
