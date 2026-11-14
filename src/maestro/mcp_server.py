"""MCP server exposing the canon store to Claude Code.

Configuration (environment):
  MAESTRO_PACK  path to a maestro folder, e.g. maestros/rafi
  MAESTRO_DSN   Postgres connection string for that maestro's database (or MAESTRO_DSN_<SLUG>; .env is read)
"""

from __future__ import annotations

import os
from functools import cache
from typing import Any

from mcp.server.mcpserver import MCPServer

from maestro.pack import load_pack
from maestro.store import Store

server = MCPServer(
    name="maestro",
    instructions=(
        "Tools for building one maestro's canon. Every fact is a claim with a source. "
        "Plan crawls with enqueue_crawl; never browse freely. Use find_gaps and coverage_report "
        "to decide what to collect next."
    ),
)


@cache
def _store() -> Store:
    from maestro import config

    config.load_env()
    pack_path = os.environ.get("MAESTRO_PACK")
    if not pack_path:
        raise RuntimeError("set MAESTRO_PACK (and MAESTRO_DSN or MAESTRO_DSN_<SLUG>) before starting the maestro MCP server")
    pack = load_pack(pack_path)
    dsn = config.dsn_for(pack.slug)
    if not dsn:
        raise RuntimeError(f"set {config.dsn_env_name(pack.slug)} or MAESTRO_DSN before starting the maestro MCP server")
    store = Store.connect(dsn, pack)
    store.migrate()
    store.sync_sources()
    return store


@server.tool()
def pack_summary() -> dict[str, Any]:
    """The active maestro pack: identity, roles, canon model, tiers and source ids."""
    d = _store().pack.data
    return {
        "identity": d["identity"],
        "status": d["status"],
        "roles": d["roles"],
        "canon_model": d["canon_model"],
        "tiers": d["tiers"],
        "sources": [s["id"] for s in d["sources"]],
        "open_questions": d.get("open_questions", []),
    }


@server.tool()
def create_entity(type: str, label: str) -> int:
    """Create an entity (work, recording, release, container, person, cue, lyric, song). Returns its id."""
    return _store().create_entity(type, label)


@server.tool()
def upsert_claim(
    entity_id: int,
    field: str,
    source_id: str,
    value: Any = None,
    ref_entity_id: int | None = None,
    confidence: float = 1.0,
    kind: str = "observed",
    extractor: str | None = None,
    private: bool = False,
) -> int:
    """Record a sourced claim about an entity. Use ref_entity_id for links (credit, part_of).

    A credit's value is {"role": "<role>"}. Returns the claim id.
    """
    return _store().upsert_claim(
        entity_id, field, source_id=source_id, value=value, ref_entity_id=ref_entity_id,
        confidence=confidence, kind=kind, extractor=extractor, private=private,
    )


@server.tool()
def merge_entities(keep_id: int, merge_id: int, reason: str) -> int:
    """Record that merge_id is the same as keep_id (reversible decided claim). Returns the claim id."""
    return _store().merge_entities(keep_id, merge_id, reason)


@server.tool()
def enqueue_crawl(
    url: str,
    extractor: str,
    purpose: str,
    entity_id: int | None = None,
    gap_id: str | None = None,
    priority: int = 0,
) -> dict[str, Any]:
    """Queue a crawl job (purpose: spine, container_sweep, enrich or gap). Workers run it later."""
    job = _store().enqueue_crawl(url, extractor, purpose, entity_id=entity_id, gap_id=gap_id, priority=priority)
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in job.items()}


@server.tool()
def query_graph(sql: str) -> list[dict[str, Any]]:
    """Run a read-only SQL query over entity, claim, source, job and raw_page (max 500 rows)."""
    rows = _store().query_graph(sql)
    return [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in rows]


@server.tool()
def coverage_report() -> dict[str, Any]:
    """Observed canon units per tier and per source lineage."""
    return _store().coverage_report()


@server.tool()
def find_gaps(limit: int = 50) -> list[dict[str, Any]]:
    """Ranked gaps (empty containers, single-lineage units, units without a tier) with suggested actions."""
    return _store().find_gaps(limit)


@server.tool()
def resolve_entities() -> dict[str, Any]:
    """Run entity resolution: merge clear duplicates across sources, queue unclear pairs for review."""
    from maestro.resolve import resolve

    return resolve(_store()).as_dict()


@server.tool()
def list_reviews(limit: int = 30, kind: str | None = None) -> list[dict[str, Any]]:
    """Open review items (ambiguous merges and other judgement calls), highest priority first."""
    from maestro.resolve import open_reviews

    return open_reviews(_store(), limit, kind)


@server.tool()
def decide_review(item_id: int, decision: str, note: str | None = None, keep_id: int | None = None) -> dict[str, Any]:
    """Record the user's decision on a review item: merge, distinct or dismiss. Only call this with the
    user's own decision; the claim is recorded with source 'user'."""
    from maestro.resolve import decide_review as decide

    return {"item": item_id, "decision": decision, "claim_id": decide(_store(), item_id, decision, note, keep_id)}


@server.tool()
def completeness_report() -> dict[str, Any]:
    """Tier estimates (capture-recapture), published-count checks and container checks."""
    from maestro.completeness import completeness_report as report

    return report(_store())


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
