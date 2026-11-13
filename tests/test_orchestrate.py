"""Orchestrator: sweeps and container refetches become jobs, the rest becomes needs, and the loop stops."""

from dataclasses import replace

from test_resolve import film, person, song

from maestro.completeness import completeness_report
from maestro.orchestrate import plan, render_markdown, run_rounds, sweep_urls


def test_sweep_urls():
    assert sweep_urls({"id": "x"}) == []
    assert sweep_urls({"sweep": {"url": "https://a/{year}", "years": [1950, 1952]}}) == [
        "https://a/1950", "https://a/1951", "https://a/1952"]
    assert sweep_urls({"sweep": {"url": "https://a?p={page}&o={offset}", "pages": 2, "page_size": 100}}) == [
        "https://a?p=1&o=0", "https://a?p=2&o=100"]
    assert sweep_urls({"sweep": {"url": "https://a/one"}}) == ["https://a/one"]


def without_sweeps(pack):
    sources = [{k: v for k, v in s.items() if k != "sweep"} for s in pack.data["sources"]]
    return replace(pack, data={**pack.data, "sources": sources})


def test_plan_turns_gaps_into_jobs_and_needs(store_for):
    s = store_for("rafi")
    naushad = person(s, "hfgk_online", "1", "Naushad")
    f = film(s, "hfgk_online", "10", "Mela", 1948)
    s.upsert_claim(f, "song_count", value=4, source_id="hfgk_online")
    for i in range(3):
        song(s, "hfgk_online", str(100 + i), f"Song {i}", f, naushad)
    stray = s.create_entity("song", "No tier")
    s.upsert_claim(stray, "credit", ref_entity_id=s.maestro_entity(), value={"role": "singer"}, source_id="mohdrafi")

    p = plan(s, completeness_report(s))
    urls = {j["url"]: j for j in p["jobs"]}
    sweeps = [j for j in p["jobs"] if j["kind"] == "sweep"]
    assert {j["source"] for j in sweeps} == {"musicbrainz", "hfgk_online"}
    listing = urls["https://hamraaz.org/filmssite/public/film.php?id=10"]
    assert listing["kind"] == "container_listing" and listing["entity_id"] == f
    assert "3 of 4" in listing["why"] and listing["purpose"] == "container_sweep"
    kinds = {n["kind"] for n in p["needs"]}
    assert {"person", "untiered"} <= kinds  # print registers; a unit with no tier
    assert "A" in p["open_tiers"]  # one lineage: not estimable, so not met

    # Anything queued or fetched is not planned again.
    s.enqueue_crawl("https://hamraaz.org/filmssite/public/film.php?id=10", "hamraaz", "container_sweep")
    for j in sweeps:
        s.enqueue_crawl(j["url"], j["extractor"], "spine")
    assert plan(s, completeness_report(s))["jobs"] == []


def test_run_rounds_stops(store_for):
    s = store_for("rafi")
    rounds = run_rounds(s, None, dry_run=True)
    assert len(rounds) == 1 and rounds[0]["stop"].startswith("dry run") and rounds[0]["jobs"]
    assert "## Round 1" in render_markdown("rafi", rounds)

    from conftest import ROOT

    from maestro.pack import load_pack

    quiet = store_for("rafi", without_sweeps(load_pack(ROOT / "maestros" / "rafi")))
    rounds = run_rounds(quiet, None, rounds=3)
    assert len(rounds) == 1 and rounds[0]["stop"].startswith("nothing left to crawl")
    assert rounds[0]["needs"]
