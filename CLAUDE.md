# maestro

A Claude Code harness that builds a complete, sourced canon for one specified maestro (singer, composer, lyricist or other music role, from any country or genre). Pilots: Mohammed Rafi, Eddy Arnold, Jean Wiener.

## Architecture in one paragraph

A generic core is driven by a per-maestro pack, `maestros/<slug>/maestro.yaml`. The profiler researches a maestro and proposes the pack; the user approves it. The orchestrator then runs a loop: crawl and extract claims, resolve entities, measure completeness, turn gaps into new crawl jobs, until each tier meets its target. Enrichment, popularity and gem scoring, and the canon finder read the finished canon.

## Rules

- **Nothing maestro-specific in `src/`.** Anything about one maestro (sources, tiers, aliases, extractors, gold sets) belongs in that maestro's folder. If the core needs a new kind of canon, extend the pack schema deliberately.
- **Every fact is a claim** with a source (`maestro.store.Store.upsert_claim`). Never overwrite or delete claims; supersede them. Merges are reversible `same_as` claims.
- **The agent plans, workers fetch.** Queue crawls with `enqueue_crawl`; do not browse freely to collect canon data. Respect robots.txt and the pack's `per_domain_rps`.
- **Legal posture:** personal use now, eventual FOSS release. Never download full audio; keep only derived features from lawfully obtained audio. Lyrics beyond what the pack's `legal.lyrics` allows must not be stored. Anything from a non-publishable source, or marked private, stays in the private layer (`data/`, git-ignored) and never ships.

## Layout

- `src/maestro/maestro-pack.schema.json`: JSON Schema for packs. `pack.py` adds cross-field checks.
- `src/maestro/schema.sql`: Postgres schema (source, entity, claim, job, raw_page).
- `src/maestro/store.py`: store API (claims, merges, crawl queue, coverage, gaps).
- `src/maestro/mcp_server.py`: MCP server exposing the store to Claude Code (configured in `.mcp.json`).
- `src/maestro/profiler/`: profiler helpers (`identify.py`: Wikidata candidates and external IDs).
- `src/maestro/crawl/`: polite fetcher (robots.txt, per-domain rate limit, raw cache in `data/<slug>/raw`) and the job runner that calls a maestro's extractors (`maestros/<slug>/extractors/<name>.py`, function `extract(ctx)`).
- `.claude/skills/profile-maestro/`: the `/profile-maestro` skill (research a maestro, propose its pack).
- `maestros/<slug>/`: pack, profiler dossier, and maestro-specific lexicons, extractors and gold data.

## Commands

- Install: `uv venv && uv pip install -e ".[dev]"`
- Validate packs: `uv run maestro-validate`
- Identity lookup: `uv run maestro-identify "<name>"` (needs access to wikidata.org)
- Crawl: `uv run maestro-crawl --pack maestros/rafi --dsn ... --seed <url> --extractor hamraaz --purpose spine [--opt years=1944-1950] [--max-jobs N]`
- Import a saved or copied page (sites whose robots.txt forbids crawling): `uv run maestro-import --pack maestros/<slug> --dsn ... --extractor <name> --url <page url> [--opt key=value] <file>` (extractors: rafi/mohdrafi, eddy-arnold/praguefrank)
- Tests: `uv run pytest` (starts a throwaway Postgres if `pg_ctl` is installed; otherwise set `MAESTRO_TEST_DSN`)
- Local database: `docker compose up -d`, then `MAESTRO_PACK=maestros/rafi` and `MAESTRO_DSN=...` for the MCP server.
