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
