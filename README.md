# maestro

A Claude Code harness that builds a complete, sourced canon of one specified *maestro*: a singer, composer, lyricist or other contributor to music, from any country or genre.

Pilot maestros: Mohammed Rafi (film playback singer), Eddy Arnold (country recording artist) and Jean Wiener (French composer).

Unlike a general music engine, the harness knows its target in advance and its canon is bounded, so every stage can use maestro-specific knowledge and spend real effort per item. A generic core is driven by a per-maestro *pack* (`maestros/<slug>/maestro.yaml`). A profiler researches the maestro and proposes the pack; an orchestrator then runs a crawl, resolve and measure loop until each canon tier meets its completeness target.

## Status

Phase 0 (foundations): pack schema and validator, Postgres canon store with claims and a crawl queue, and an MCP server exposing the store to Claude Code. The three pilot packs are drafts awaiting the profiler.

## Quick start

```sh
uv venv && uv pip install -e ".[dev]"
uv run maestro-validate          # validate all packs under maestros/
uv run pytest                    # needs Postgres (pg_ctl on PATH) or MAESTRO_TEST_DSN
docker compose up -d             # local Postgres for the MCP server (see .mcp.json)
```

## Licence

Apache-2.0. Code is open source; canon data is split into a publishable layer and a private layer (cached pages, lyric text, audio features from private sources) that is never committed or released.
