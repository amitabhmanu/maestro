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

Fetch the pages you cite; a search snippet is not a source. Never fill a gap with a guess: write it as an open question.

## Stage 3: Discover and rate sources

For each candidate source record, in the dossier's source table:

- **Coverage:** which tiers, years and fields it covers.
- **Authority:** primary register, careful secondary work, fan site, aggregator.
- **Lineage:** which other source it copies, if any. Sources that copy each other share a `lineage` in the pack.
- **Access:** `api`, `crawlable` (check robots.txt), `print`, `print_owned`, `print_library`, `licensed`, `unavailable`.
- **Structure:** `structured`, `semi` or `prose` (decides deterministic parser vs LLM extraction).
- **Publishable:** whether facts taken from it may go into the eventual FOSS release.

Look specifically for a *register*: a source that claims to list everything for some tier (a song encyclopedia, a session discography, a catalogue of works). If none exists, say so; the completeness engine will rely on estimates for that tier.

## Stage 4: Ask the user

Ask only what is the user's to decide or what research could not settle. Every question offers options with a recommended default and one line on what each changes. Typical questions:

- Which roles are in scope (e.g. a singer's acting work; a composer playing other people's music)?
- Tier boundaries: unreleased work, broadcasts, posthumous releases, other languages.
- Which registers can the user access (owned books, library access)?
- Which audio can be used lawfully?
- Budget: crawl volume, model spend, review hours.

Project-wide decisions already made (do not ask again): personal use now with eventual FOSS release, so `legal.purpose: personal_then_foss`; audio from whatever can be legally sourced, derived features only; review time is ample for now.

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

## Quality bar before asking for approval

- Identity is unambiguous and namesakes are listed.
- Every tier has a register, or the dossier says why none exists.
- Every source has access terms and lineage recorded.
- Every checksum has a source URL.
