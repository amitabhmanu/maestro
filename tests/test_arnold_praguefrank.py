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


SESSIONS = Path(__file__).parent / "fixtures" / "praguefrank" / "arnold-part1.txt"


def test_parse_sessions():
    ss = pf.parse_sessions(SESSIONS.read_text("utf-8"))
    assert [(s["iso_date"], s["year"], s["place"], len(s["takes"])) for s in ss] == [
        ("1944-12-04", 1944, "WSM Radio Station Studio, Nashville, TN", 2),
        ("1971-12-05", 1971, "RCA Victor Studio, Nashville, TN", 2),
        (None, 2005, "The Cowboy Arms Hotel and Recording Spa, Nashville, TN", 2),
    ]
    assert ss[1]["notes"] == ["split session with Jim Reeves"]
    takes = {t["seq"]: t for s in ss for t in s["takes"]}
    assert takes[2]["title"] == "WHY DIDN'T YOU TAKE THAT TOO" and not takes[2]["issued"]
    assert (takes[822]["title"], takes[822]["releases"]) == ("I LOVE YOU, DEAR", "74-0559/LSP-4625")
    assert takes[655]["matrix"] == "TWA4-0783-?"
    assert takes[1030]["title"] == "BLUE EYES CRYING IN THE RAIN" and takes[1030]["matrix"] is None
    assert takes[1128]["releases"] == "RCA 82876-70710-2"


def test_import_sessions(store_for, tmp_path):
    store = store_for("eddy-arnold")
    import_page(store, "praguefrank", "https://countrydiscography.blogspot.com/", SESSIONS, tmp_path)
    tiers = store.query_graph(
        "SELECT value #>> '{}' AS t, count(*) AS n FROM claim WHERE field = 'tier' GROUP BY 1 ORDER BY 1")
    assert [(r["t"], r["n"]) for r in tiers] == [("A", 5), ("B", 1)]
    parts = store.query_graph("SELECT count(*) AS n FROM claim WHERE field = 'part_of'")
    assert parts[0]["n"] == 6
