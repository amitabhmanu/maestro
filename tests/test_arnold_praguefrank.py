"""Eddy Arnold's Praguefrank release-list extractor, against a trimmed copy of the page's layout."""

import importlib.util
from pathlib import Path

from conftest import ROOT
from maestro.crawl.runner import import_page

FIX = Path(__file__).parent / "fixtures" / "praguefrank" / "arnold-part2.txt"
spec = importlib.util.spec_from_file_location(
    "praguefrank", ROOT / "maestros" / "eddy-arnold" / "extractors" / "praguefrank.py")
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)


def test_parse_sections_and_wrapped_lines():
    rs = pf.parse(FIX.read_text("utf-8"))
    assert [(r["kind"], r["label"], r["catalogue"], r["year"]) for r in rs] == [
        ("album", "RCA Victor", "LPM-3027", 1952),
        ("album", "Mercury", "314-526 879-2", 1995),
        ("ep", "RCA Victor", "EPA 239", 1952),
        ("single", "RCA Victor", "33-0520", 1945),
        ("single", "RCA Victor", "20-2488", 1947),
        ("single", "MGM", "K 14478", 1972),
    ]
    assert rs[0]["title"] == "Country Classics" and rs[0]["tracks"] == ["Anytime", "Bouquet Of Roses", "It's A Sin"]
    assert rs[0]["notes"] == "10“ LP"
    assert rs[2]["tracks"][1] == "M-O-T-H-E-R" and len(rs[2]["tracks"]) == 4
    assert rs[4]["tracks"] == ["Rockin' Alone (In An Old Rockin' Chair)", "I'm Thinking Tonight Of My Blue Eyes"]


def test_import_releases(store_for, tmp_path):
    store = store_for("eddy-arnold")
    import_page(store, "praguefrank", "https://countrydiscography.blogspot.com/", FIX, tmp_path)
    kinds = store.query_graph(
        "SELECT value #>> '{}' AS k, count(*) AS n FROM claim WHERE field = 'release_kind' GROUP BY 1 ORDER BY 1")
    assert [(r["k"], r["n"]) for r in kinds] == [("album", 2), ("ep", 1), ("single", 3)]
    tracks = store.query_graph("SELECT count(*) AS n FROM claim WHERE field = 'track'")
    assert tracks[0]["n"] == 3 + 2 + 4 + 2 + 2 + 2
