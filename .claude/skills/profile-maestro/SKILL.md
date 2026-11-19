---
name: profile-maestro
description: Research a maestro (singer, composer, lyricist or other music role) and propose their maestro pack. Use when starting a new maestro, or when re-profiling one after the canon build found something the pack did not anticipate.
---

# Profile a maestro

You turn a name into an approved maestro pack at `maestros/<slug>/`. You research, ask the user only what research cannot settle, propose every setting with evidence, and test the proposal on a small sample before the full build.

Outputs, all in `maestros/<slug>/`:

- `dossier.md`: what you found, with a source link for every fact (template: `dossier-template.md` in this skill folder).
- `maestro.yaml`: the pack, `status: draft` until the user approves it. Schema: `src/maestro/maestro-pack.schema.json`.
- `open-questions.md` is not a separate file: open questions live in the pack's `open_questions` list.

Re-profiling an existing maestro: read the current pack and dossier first, change only what the new evidence supports, bump `version`, and say what changed and why.

## Stage 1: Identify

1. Run `uv run maestro-identify "<name>"` (Wikidata). It lists human candidates with Wikidata, MusicBrainz, VIAF, ISNI and BnF IDs, life dates and occupations. Try other spellings and scripts if the first search misses.
2. Pick the candidate that matches the user's description. Record every other candidate with the same name as a namesake in `identity.namesakes`, with the reason it is ruled out (e.g. "Q123: Belgian cyclist").
3. If two candidates could both be the maestro, stop and ask the user before going on.
4. Collect name variants: spellings, transliterations, native scripts, stage names. These feed `identity.variants` and later the lexicon.
5. Fill every external ID the sources below key on, from Wikidata where it holds them (Discogs P1953, DAHR P4457) or from the site itself (`ids.hfgk`: the person.php?id= on hamraaz.org; `ids.hindigeetmala`: the /singer/<name>.php page). Wikimedia often answers 429 from cloud IPs: retry later rather than guess.
6. Record `identity.died` and, for a maestro whose work kept coming out after death or retirement, `identity.reissues_after`: the last year a genuinely new release is plausible (Rafi died 1980; his last new film songs came out in 1986). Later release dates are reissues and never date a song.

## Stage 2: Research the dossier

Run these as parallel subagents, one per topic. Each must return facts with source URLs and a confidence, and must say what it could not find.

- **Career:** timeline, eras, active years, how output changed over time.
- **Roles:** every role held, with a rough share of output per role (e.g. "composer ~90%, pianist ~10%").
- **Output structure:** how the work was released (films, singles, albums, sessions, scores, broadcasts), and what the natural *unit* and *container* are.
- **Counts:** every published count or subtotal (by year range, collaborator, solo or duet). These become `checksums`.
- **Collaborators:** the most frequent collaborators per role.
- **Attribution risks:** known disputes, soundalikes, misattributions, namesakes.
- **Vocabulary:** the tradition's own terms (raga, country subgenre, classical form) for `taxonomies`.
- **Popularity signals:** historic charts or countdowns, awards, and modern listening data that exist for this maestro.
- **Expert voices:** blogs, books and critics who write about individual songs, and for each list they publish, its intent (see Stage 4).

Fetch the pages you cite; a search snippet is not a source. Never fill a gap with a guess: write it as an open question.

## Stage 3: Discover and rate sources

For each candidate source record, in the dossier's source table:

- **Coverage:** which tiers, years and fields it covers.
- **Authority:** primary register, careful secondary work, fan site, aggregator.
- **Lineage:** which other source it copies, if any. Sources that copy each other share a `lineage` in the pack.
- **Access:** `api`, `crawlable` (check robots.txt), `print`, `print_owned`, `print_library`, `licensed`, `unavailable`.
- **Structure:** `structured`, `semi` or `prose` (decides deterministic parser vs LLM extraction).
- **Publishable:** whether facts taken from it may go into the eventual FOSS release.

Look specifically for a *register*: a source that claims to list everything for some tier (a song encyclopedia, a session discography, a catalogue of works). If none exists, say so; the completeness engine will rely on estimates for that tier, which needs at least two independent lineages per tier (Rafi's non-Hindi and non-film songs had no register and were built from a Wikipedia list, MusicBrainz, Discogs, saved fan-site pages and Atul's Song A Day). Note each register's year span: a register that stops before the maestro's career ends (HFGK stops at 1985) leaves the later years to other sources and to their own estimate.

Lineage rules the pilots taught:

- A site that mostly copies the register is the same lineage, not a second sighting. Test it on a scratch copy against the register before deciding (Hindi Geetmala missed 1,254 of 4,278 HFGK songs it seemed to copy, and as a full source queued ~1,000 near-match reviews). Run such a source with `unit_mode: enrich`: it adds details and a gap list, never units or lineage.
- Release databases (Discogs) treat every compilation as its own container. For a singer they are `unit_mode: enrich` (catalogue numbers, issue years). For a composer, release-level credits over-attribute works: leave the sweep out (Jean Wiener).
- MusicBrainz release-group dates are often a reissue's; `reissues_after` keeps them from dating songs, and a later film reusing an old recording is the old song, not a new one.

Access rules (never get around them):

- Check robots.txt. A site that blocks crawlers (mohdrafi.com, Songs of Yore for AI agents, countrydiscography.blogspot.com) or serves a bot challenge (Atul's Song A Day, DAHR's CloudFront WAF) is `access: unavailable`: the user saves pages by hand into `/mnt/project-files/maestro/manual-imports/<slug>/` and they are loaded with `maestro-import`.
- Public APIs are `access: api` with the API's own `api_rps`; personal keys go in `.env` and the pack names only the variable (`api_auth`). Known keys: `DISCOGS_TOKEN` (optional), `LASTFM_API_KEY`, `YOUTUBE_API_KEY` (label channel uploads only, never search).
- Do not propose Spotify: it needs a Premium-owned developer app and its terms bar ML use (tried and dropped, Nov 2026).
- A register whose owner has not given permission (HFGK) may be crawled for development; note the permission as an open question before going live.

Reuse before you build: check `src/maestro/extractors/` and other packs first. Every Hindi film maestro shares the core `hamraaz`, `hindigeetmala`, `hindigeetmala_binaca`, `binaca`, `filmfare`, `wikipedia_list`, `songsofyore` and `atul` extractors, the film register (`maestros/hindi-films`, whose `check-canon` validates the canon's films) and the shared name tables in `maestros/hindi-films/lexicon/`.

## Stage 4: Ask the user

Ask only what is the user's to decide or what research could not settle. Every question offers options with a recommended default and one line on what each changes. Typical questions:

- Which roles are in scope (e.g. a singer's acting work; a composer playing other people's music)?
- Tier boundaries: unreleased work, broadcasts, posthumous releases, other languages.
- Which registers can the user access (owned books, library access)?
- Which audio can be used lawfully?
- Budget: crawl volume, model spend, review hours.
- Posthumous and reissue years: confirm `reissues_after`.
- Which fields quote lyrics (`legal.lyric_fields`): titles that are the song's opening words (Hindi film songs) must keep only the first line publishable.
- Expert lists: show the user each list's intent as you read it from its title and introduction (`gem_list`: claims its songs are good and overlooked; `best_of`; `themed`) and record it in the pack's `popularity_signals.lists` TSV. Only `gem_list` lists with at least 90% low-popularity songs ever make gems. Ask for the user's trusted experts and 10-15 calibration picks (they can tag gems in the explorer).

Project-wide decisions already made (do not ask again):

- Personal use now with eventual FOSS release, so `legal.purpose: personal_then_foss`, `legal.lyrics: first_line_only`; audio from whatever can be legally sourced, derived features only; review time is ample for now.
- Music contributions only (acting is out of scope).
- A rare gem is a good unit that never became popular: low popularity is the hard first filter, quality comes only from `gem_list` expert lists or the user's own gem tags. Metadata and crude audio features do not find quality (tested on Rafi); use them as leads only.
- A song reused in a later film (or reissued) is not a new work and is not counted again.
- Canon stores live in Postgres on the user's PC; canon runs go through Remote Control there, with a backup before anything writes to a live store. Give the user times in IST.

## Stage 5: Propose the pack

Write `maestros/<slug>/maestro.yaml`. Rules:

- Nothing maestro-specific goes in `src/`. If the pack cannot express this maestro (a new unit or container type), stop and propose a schema change to the user.
- Add a `_why` block (reason, confidence, evidence URLs) to every section whose value is not obvious.
- Unconfirmed external IDs are `null`, never guessed.
- Run `uv run maestro-validate maestros/<slug>` and fix every problem.
- Summarise the proposal for the user: the canon model, tiers, top sources, and the low-confidence fields they should check.

The user approves by saying so; then set `status: approved`.

## Stage 6: Dry run

Only after approval. Take one slice (one decade, or 5 to 10 containers): build the spine from structured sources, queue and run its crawl jobs, resolve, then call `coverage_report` and `find_gaps`. Report what broke (missing sources, failing extractors, unexpected unit types, checksum mismatches), revise the pack, and repeat until the slice passes.

Use a fresh database (`MAESTRO_DSN_<SLUG>`, created `ENCODING 'UTF8' TEMPLATE template0`). Add each further source on a scratch copy first and compare tiers before and after; `maestro-run --dry-run` shows what the next round would do. Before asking the user to review merges, export them `--grouped`: one question per cluster, never chained pairwise "same" answers (A=B and A=C silently makes B=C). Add each name the user corrects to a lexicon (the shared Hindi film one unless it holds for this maestro only).

## Quality bar before asking for approval

- Identity is unambiguous and namesakes are listed.
- Every tier has a register, or the dossier says why none exists.
- Every source has access terms and lineage recorded.
- Every checksum has a source URL.
