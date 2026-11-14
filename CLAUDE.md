# maestro

A Claude Code harness that builds a complete, sourced canon for one specified maestro (singer, composer, lyricist or other music role, from any country or genre). Pilots: Mohammed Rafi, Eddy Arnold, Jean Wiener.

## Architecture in one paragraph

A generic core is driven by a per-maestro pack, `maestros/<slug>/maestro.yaml`. The profiler researches a maestro and proposes the pack; the user approves it. The orchestrator then runs a loop: crawl and extract claims, resolve entities, measure completeness, turn gaps into new crawl jobs, until each tier meets its target. Enrichment, popularity and gem scoring, and the canon finder read the finished canon.

## Rules

- **Nothing maestro-specific in `src/`.** Anything about one maestro (sources, tiers, aliases, extractors, gold sets) belongs in that maestro's folder. If the core needs a new kind of canon, extend the pack schema deliberately.
- **Every fact is a claim** with a source (`maestro.store.Store.upsert_claim`). Never overwrite or delete claims; supersede them. Merges are reversible `same_as` claims.
- **The agent plans, workers fetch.** Queue crawls with `enqueue_crawl`; do not browse freely to collect canon data. Respect robots.txt and the pack's `per_domain_rps`. Public APIs a pack lists as `access: api` (MusicBrainz) skip robots.txt but keep the API's own rate (`api_rps`). Never get around a site's bot protection: import pages the user saves instead.
- **Legal posture:** personal use now, eventual FOSS release. Never download full audio; keep only derived features from lawfully obtained audio. Lyrics beyond what the pack's `legal.lyrics` allows must not be stored or published: `upsert_claim` enforces it (`src/maestro/lyrics.py`; fields that quote lyrics, such as Rafi's titles, are listed in `legal.lyric_fields`), so text past the first line stays private beside a publishable `first_line` claim. Run `maestro-lyrics` before publishing anything. Anything from a non-publishable source, or marked private, stays in the private layer (`data/`, git-ignored) and never ships.

## Layout

- `src/maestro/maestro-pack.schema.json`: JSON Schema for packs. `pack.py` adds cross-field checks.
- `src/maestro/schema.sql`: Postgres schema baseline (source, entity, claim, job, raw_page); later changes are numbered files in `src/maestro/migrations/` (see its README), applied by `Store.migrate()`.
- `src/maestro/config.py`: `.env` loading, per-maestro DSNs (`MAESTRO_DSN_<SLUG>`), the private data folder (`MAESTRO_DATA_DIR`).
- `src/maestro/rawstore.py`: raw pages kept by SHA-256 under `<data dir>/<slug>/raw`; `maestro-raw` finds and refills missing ones.
- `src/maestro/lyrics.py`: the lyrics policy (`first_line`, the plan `upsert_claim` applies) and `maestro-lyrics`, the publish check.
- `src/maestro/ops.py`: `maestro-migrate`, `maestro-doctor`, `maestro-backup`.
- `src/maestro/store.py`: store API (claims, merges, crawl queue, coverage, gaps).
- `src/maestro/mcp_server.py`: MCP server exposing the store to Claude Code (configured in `.mcp.json`).
- `src/maestro/profiler/`: profiler helpers (`identify.py`: Wikidata candidates and external IDs).
- `src/maestro/resolve.py`: entity resolution (normalise, block, merge across sources, review queue).
- `src/maestro/completeness.py`: completeness engine (checksums with `filter`, container check, Chapman/Chao1 per tier, split by the years each lineage covers).
- `src/maestro/orchestrate.py`: the orchestrator loop (resolve, report, plan jobs from a source's `sweep` and short containers' `container_url`, list the needs only a person or new code can meet, crawl, repeat).
- `src/maestro/extractors/`: extractors for sources any maestro can use (`musicbrainz` releases, `musicbrainz_works`, `wikidata_films`, `bnf_works`, `dahr`); a maestro's own extractor of the same name wins.
- `src/maestro/explore.py` and `src/maestro/explorer/index.html`: the canon explorer (snapshot of a finished canon as JSON, and a static page that searches it in plain words, with Ask Claude when published as an Artifact).
- `src/maestro/popularity.py`: popularity and rare-gem scoring. Extractors record ranking evidence (chart peaks, awards, encyclopedia articles) as `mention` claims on the maestro with `record_mention`; scoring matches them to units, adds derived signals (`reissues`, `remakes`, source presence), ranks each signal within the canon and groups them by the pack's `popularity_signals` facets. `wikitable.py` reads Wikipedia tables (spans expanded).
- `src/maestro/crawl/`: polite fetcher (robots.txt, per-domain rate limit, raw cache in `data/<slug>/raw`) and the job runner that calls a maestro's extractors (`maestros/<slug>/extractors/<name>.py`, function `extract(ctx)`).
- `.claude/skills/profile-maestro/`: the `/profile-maestro` skill (research a maestro, propose its pack).
- `maestros/<slug>/`: pack, profiler dossier, and maestro-specific lexicons, extractors and gold data.

## Commands

- Install: `uv venv && uv pip install -e ".[dev]"`
- Validate packs: `uv run maestro-validate`
- Identity lookup: `uv run maestro-identify "<name>"` (needs access to wikidata.org)
- Crawl: `uv run maestro-crawl --pack maestros/rafi --dsn ... --seed <url> --extractor hamraaz --purpose spine [--opt years=1944-1950] [--max-jobs N]`
- Import a saved or copied page (sites whose robots.txt forbids crawling): `uv run maestro-import --pack maestros/<slug> --dsn ... --extractor <name> --url <page url> [--opt key=value] <file>` (extractors: rafi/mohdrafi, rafi/atul, eddy-arnold/praguefrank, core dahr for saved DAHR name pages)
- Rebuild a store from the raw cache after an extractor or core fix: `uv run maestro-replay --pack maestros/<slug> --from-dsn <old> --dsn <new empty db>`
- Resolve entities (merge clear duplicates, queue unclear ones): `uv run maestro-resolve --pack ... --dsn ...`
- Review queue: `uv run maestro-review` lists open items; `uv run maestro-review <id> merge|distinct|dismiss [--keep ID]` records the user's decision. `uv run maestro-review --export FILE` writes every open item with its evidence and each subject's facts as JSON. For a review page use `--export FILE --grouped` (items that share a subject become one question, a floating unit's candidates one "which one is it?" question) and record answers with `--apply FILE`: merges are transitive, so never ask or apply pairwise "same" answers that chain (A=B and A=C makes B=C). Only record decisions the user made.
- Completeness report (tier estimates, published counts, container check): `uv run maestro-report [--save]` (`--save` writes `maestros/<slug>/runs/`)
- Popularity evidence: crawl a source with its extractor and `--purpose enrich`, e.g. `--seed https://en.wikipedia.org/wiki/Eddy_Arnold_discography --extractor wikipedia_singles` (rafi: `filmfare`, `binaca`, `wikipedia_list`, `hindigeetmala_binaca` seeded with https://www.hindigeetmala.net/geetmala/binaca_geetmala_1953.php for every year's full countdown; core `wikidata_articles` with the URL from `maestro.extractors.wikidata_articles.articles_url(qid)`).
- Popularity and gems: `uv run maestro-score --pack ... --dsn ... [--top N] [--pick UNIT ...] [--not-gem UNIT ...] [--json]` lists the most popular units, gems, leads, expert voices and mentions matching no unit (possible gaps). Gems come only from expert opinion (esteem mentions with `expert` and `framing` gem|praise|listed, weighted by each voice's selectivity) or the user's picks; popularity only filters out well-known units. Team, same-film and "never a hit" signals make leads, not gems. `--pick` and `--not-gem` record the user's calibration: from 10 picks on, each voice is weighted by how often it names them.
- Canon explorer: `uv run maestro-explore --maestro maestros/rafi=<dsn> [--maestro maestros/eddy-arnold=<dsn> ...] --out data/explorer --serve` writes `index.html` plus `canon/<slug>.json` per maestro (publishable claims only unless `--include-private`) and serves it on localhost:8765. Snapshots outlive the database, so keep them when a canon is rebuilt.
- Orchestrator: `uv run maestro-run --pack maestros/<slug> --dsn ... [--rounds 3] [--max-jobs N] [--dry-run] [--save]` runs rounds until every tier meets its target or nothing is left to crawl; its needs list says what a person or a new extractor must add.
- Tests: `uv run pytest` (starts a throwaway Postgres if `pg_ctl` is installed; otherwise set `MAESTRO_TEST_DSN`)
- Persistent stores (see `docs/local-store.md`): one database per maestro, DSNs as `MAESTRO_DSN_<SLUG>` in a git-ignored `.env` (then `--dsn` can be left out). `uv run maestro-doctor --all` checks them, `uv run maestro-migrate --all` updates schemas, `uv run maestro-backup --all` dumps them and copies raw pages, `uv run maestro-raw --pack ... status|fill DIR|refetch` checks and refills raw pages. Resolve, review decisions and `maestro-run` hold a per-canon lock, so two sessions never merge at once.
- Lyrics check: `uv run maestro-lyrics --pack maestros/<slug> [--snapshot data/explorer/canon/<slug>.json]` exits 1 if publishable claims or a snapshot hold lyric text past the first line; `--fix` supersedes claims stored before the guard and stores them again under the policy (back up first).
- Local database alternative: `docker compose up -d` (pgvector image, localhost only).
