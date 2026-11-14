import asyncio

from maestro import mcp_server


def test_tools_are_registered():
    tools = asyncio.run(mcp_server.server.list_tools())
    names = {t.name for t in tools}
    assert names == {"pack_summary", "create_entity", "upsert_claim", "merge_entities",
                     "enqueue_crawl", "query_graph", "coverage_report", "find_gaps",
                     "resolve_entities", "list_reviews", "decide_review", "completeness_report"}


def test_tools_run_against_a_store(store_for, pg_server, monkeypatch):
    from conftest import ROOT

    store = store_for("eddy-arnold")  # a fresh database of its own, never the server's default one
    monkeypatch.setenv("MAESTRO_PACK", str(ROOT / "maestros" / "eddy-arnold"))
    monkeypatch.setenv("MAESTRO_DSN", pg_server.rsplit("/", 1)[0] + "/" + store.conn.info.dbname)
    mcp_server._store.cache_clear()
    try:
        summary = mcp_server.pack_summary()
        assert summary["canon_model"]["unit"] == "recording"
        rec = mcp_server.create_entity("recording", "Make the World Go Away (1965)")
        assert mcp_server.upsert_claim(rec, "year", "discogs", value=1965) > 0
        assert mcp_server.coverage_report()["maestro"] == "eddy-arnold"
        assert mcp_server.query_graph("SELECT count(*) AS n FROM entity")[0]["n"] >= 1
        assert mcp_server.resolve_entities() == {"merged": {}, "queued": {}}
        assert mcp_server.list_reviews() == []
        assert mcp_server.completeness_report()["maestro"] == "eddy-arnold"
    finally:
        mcp_server._store().close()
        mcp_server._store.cache_clear()
