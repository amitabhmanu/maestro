"""Rafi's mohdrafi.com extractor and the saved-page import path, against a synthetic page."""

import importlib.util
from pathlib import Path

from conftest import ROOT
from maestro.crawl.runner import import_page

FIX = Path(__file__).parent / "fixtures" / "mohdrafi"
spec = importlib.util.spec_from_file_location("mohdrafi", ROOT / "maestros" / "rafi" / "extractors" / "mohdrafi.py")
mohdrafi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mohdrafi)


def test_parse_category_page():
    (block,) = mohdrafi.parse(mohdrafi.decode((FIX / "category.html").read_bytes()))
    assert block["name"] == "Non-Filmi Ghazals" and block["year"] is None
    assert [t["title"] for t in block["tracks"]] == ["Pehli Ghazal", "Doosri Ghazal"]
    first, second = block["tracks"]
    assert first == {"track_key": "Pehli Ghazal", "title": "Pehli Ghazal", "singers": ["Mohd Rafi"],
                     "lyricists": ["Some Poet"], "composers": ["Some Composer"]}
    assert second["lyricists"] == [] and second["composers"] == ["Mohd Rafi"]


def test_import_saved_page(store_for, tmp_path):
    store = store_for("rafi")
    url = "http://www.mohdrafi.com/database/findfilm.php?FILM=Non-Filmi+Ghazals"
    import_page(store, "mohdrafi", url, FIX / "category.html", tmp_path, {"tier": "B"})
    import_page(store, "mohdrafi", url, FIX / "category.html", tmp_path, {"tier": "B"})  # idempotent
    rows = store.query_graph(
        "SELECT e.label, c.value->>'role' AS role FROM claim c JOIN entity e ON e.id = c.entity_id "
        "WHERE c.field = 'credit' AND c.ref_entity_id = "
        "(SELECT entity_id FROM claim WHERE field = 'is_maestro' LIMIT 1) ORDER BY 1, 2")
    assert [(r["label"], r["role"]) for r in rows] == [
        ("Doosri Ghazal", "composer"), ("Doosri Ghazal", "singer"), ("Pehli Ghazal", "singer")]
    tiers = store.query_graph("SELECT count(*) AS n FROM claim WHERE field = 'tier' AND value = '\"B\"'")
    assert tiers[0]["n"] == 2
    cats = store.query_graph("SELECT DISTINCT value #>> '{}' AS v FROM claim WHERE field = 'category'")
    assert cats == [{"v": "Non-Filmi Ghazals"}]
    songs = store.query_graph("SELECT count(*) AS n FROM entity WHERE type = 'song'")
    assert songs[0]["n"] == 2


def test_parse_pasted_table():
    blocks = mohdrafi.parse_any((FIX / "language.tsv").read_bytes())
    assert [(b["name"], b["year"], [t["title"] for t in b["tracks"]]) for b in blocks] == [
        ("Film X (Telugu)", None, ["Song A (Telugu)", "Song C (Telugu)"]),
        ("Film Y (Telugu)", 1978, ["Song B (Telugu)"]),
    ]
    assert blocks[0]["tracks"][0]["singers"] == ["Mohd Rafi"]


def test_import_pasted_table(store_for, tmp_path):
    store = store_for("rafi")
    url = "http://www.mohdrafi.com/database/findid.php?ID=369,422,6186"
    import_page(store, "mohdrafi", url, FIX / "language.tsv", tmp_path, {"tier": "B", "language": "Telugu"})
    rows = store.query_graph(
        "SELECT e.label, (SELECT f.label FROM entity f WHERE f.id = c.ref_entity_id) AS film "
        "FROM claim c JOIN entity e ON e.id = c.entity_id WHERE c.field = 'part_of' ORDER BY 1")
    assert [(r["label"], r["film"]) for r in rows] == [
        ("Song A (Telugu)", "Film X (Telugu)"), ("Song B (Telugu)", "Film Y (Telugu)"), ("Song C (Telugu)", "Film X (Telugu)")]
    langs = store.query_graph("SELECT count(*) AS n FROM claim WHERE field = 'language'")
    assert langs[0]["n"] == 2


def test_pasted_non_filmi_rows_become_categories():
    text = "Track Name\tFilm Name\tYear\nAn English Song (English)\tNon-Filmi (1970)\t1970\n\nSearch\t\nTrack\tMovie\n"
    (block,) = mohdrafi.parse_table(text)
    assert (block["name"], block["year"], len(block["tracks"])) == ("Non-Filmi (1970)", 1970, 1)
