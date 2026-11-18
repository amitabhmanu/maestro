import json

from maestro.gemtags import FORMAT, is_active, load_ledger, main, merge, read_source, record
from test_resolve import song


def tag(unit, title, at="2026-11-15T10:00:00Z", maestro="rafi"):
    return {"maestro": maestro, "unit": unit, "title": title, "year": 1960, "container": "F", "tagged_at": at}


def test_merge_never_drops_tags():
    ledger = {}
    c = merge(ledger, [tag(1, "A"), tag(2, "B")], complete_for={"rafi"}, now="t1")
    assert c["added"] == 2 and len(ledger) == 2
    # B untagged in the explorer: kept, marked; an eddy-arnold tag is untouched by a rafi listing.
    ledger["eddy-arnold~9"] = tag(9, "C", maestro="eddy-arnold")
    c = merge(ledger, [tag(1, "A", at="2026-11-16T00:00:00Z")], complete_for={"rafi"}, now="t2")
    assert c == {"added": 0, "retagged": 0, "updated": 0, "untagged": 1, "unchanged": 1}
    assert not is_active(ledger["rafi~2"]) and is_active(ledger["eddy-arnold~9"])
    assert ledger["rafi~1"]["tagged_at"] == "2026-11-15T10:00:00Z"  # earliest time kept
    # An export (not complete) never untags; tagging B again revives it.
    c = merge(ledger, [tag(2, "B")], now="t3")
    assert c["retagged"] == 1 and is_active(ledger["rafi~2"]) and len(ledger) == 3


def test_cli_merge_reads_artifact_docs_and_exports(tmp_path):
    docs = tmp_path / "out" / "gem_tags"
    docs.mkdir(parents=True)
    (docs / "rafi@1.json").write_text(json.dumps(tag(1, "A")))
    export = tmp_path / "export.json"
    export.write_text(json.dumps({"format": FORMAT, "tags": [tag(2, "B")]}))
    led = tmp_path / "ledger.json"
    assert main(["merge", "--ledger", str(led), "--history", str(tmp_path / "h"), str(tmp_path / "out"), str(export)]) == 0
    assert set(load_ledger(led)) == {"rafi~1", "rafi~2"}
    assert len(list((tmp_path / "h").glob("*.json"))) == 1
    assert len(read_source(led)) == 2
    # The ledger is a maestro-score --picks-file.
    from maestro.popularity import explorer_tags
    assert sorted(explorer_tags(str(led), "rafi")) == [1, 2]


def test_record_picks_checks_titles(store_for):
    s = store_for("rafi")
    a = song(s, "hfgk_online", "1", "Dil Ka Haal Sune Dilwala")
    b = song(s, "hfgk_online", "2", "Some Other Song")
    ledger = {}
    merge(ledger, [tag(a, "Dil Ka Haal Sune Dilwala"), tag(b, "Not The Same Song"), tag(99999, "Gone")])
    res = record(s, ledger, "rafi")
    assert res["picked"] == 1 and len(res["mismatch"]) == 1 and len(res["missing"]) == 1
    assert record(s, ledger, "rafi")["already"] == 1
    merge(ledger, [], complete_for={"rafi"})
    assert record(s, ledger, "rafi")["unpicked"] == 1
    assert not s.conn.execute("SELECT 1 FROM claim WHERE field = 'pick' AND status = 'active'").fetchall()
