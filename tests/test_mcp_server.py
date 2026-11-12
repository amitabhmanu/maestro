import asyncio

from maestro import mcp_server


def test_tools_are_registered():
    tools = asyncio.run(mcp_server.server.list_tools())
    names = {t.name for t in tools}
    assert names == {"pack_summary", "create_entity", "upsert_claim", "merge_entities",
                     "enqueue_crawl", "query_graph", "coverage_report", "find_gaps"}


def test_tools_run_against_a_store(pg_server, monkeypatch):
    from conftest import ROOT

    monkeypatch.setenv("MAESTRO_PACK", str(ROOT / "maestros" / "eddy-arnold"))
    monkeypatch.setenv("MAESTRO_DSN", pg_server)
    mcp_server._store.cache_clear()
    try:
        summary = mcp_server.pack_summary()
        assert summary["canon_model"]["unit"] == "recording"
        rec = mcp_server.create_entity("recording", "Make the World Go Away (1965)")
        assert mcp_server.upsert_claim(rec, "year", "discogs", value=1965) > 0
        assert mcp_server.coverage_report()["maestro"] == "eddy-arnold"
        assert mcp_server.query_graph("SELECT count(*) AS n FROM entity")[0]["n"] >= 1
    finally:
        mcp_server._store().close()
        mcp_server._store.cache_clear()
