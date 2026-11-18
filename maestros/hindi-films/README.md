# hindi-films: a register of every Hindi film

Not a maestro: a **register pack** (`identity.kind: register`). Its canon is every song of every Hindi
film, with films as containers. It is kept apart from the maestro canons (its own database,
`MAESTRO_DSN_HINDI_FILMS`) and checks them: `maestro-films check-canon` compares a maestro's films with it.

## Sources

| Source | What it gives | Extractor |
| --- | --- | --- |
| Wikidata (films with original language Hindi) | every film it knows (~8,200), year, IMDb ID (reference only, IMDb is never fetched), English Wikipedia article, music directors, directors | core `wikidata_language_films` |
| Wikipedia "List of Hindi films of YEAR" (1920-) | films per year, director; music director, lyricists and singers from the notes of older lists | `wikipedia_year` |
| HFGK Online V2.0 (hamraaz.org), 1931-1985 | every film with its full song list: singers, music directors, lyricists per song (~6,460 films, ~47,000 songs) | core `hfgk` |

Song details after 1985 (Wikipedia film pages, MusicBrainz soundtracks) and song-level checks
(Atul's Song A Day, MusicBrainz) are later phases. HFGK: the site master's permission is needed before
anything built on it goes live.

## Build

```
# Crawl each sweep (maestro-run plans the same three, but its resolve step is untested on a register):
uv run maestro-crawl --pack maestros/hindi-films --seed "<films_url('Q1568')>" --extractor wikidata_language_films
uv run maestro-crawl --pack maestros/hindi-films --seed https://en.wikipedia.org/wiki/Lists_of_Indian_films --extractor wikipedia_year --max-jobs 200
uv run maestro-crawl --pack maestros/hindi-films --seed https://hamraaz.org/filmssite/public/index.php --extractor hfgk --max-jobs 7000
uv run maestro-films match --pack maestros/hindi-films     # link each film's records across sources
uv run maestro-films report --pack maestros/hindi-films --out data/films
uv run maestro-films check-canon --pack maestros/hindi-films --canon maestros/rafi --out data/films
uv run maestro-explore --maestro maestros/rafi --maestro maestros/hindi-films --out data/explorer   # Films view
```

The HFGK crawl is ~6,500 pages: about 3.5 hours at the pack's 0.5 requests a second.

## Report files

- `films.csv`: one row per film: Wikidata and IMDb IDs, title (HFGK's transliteration first), Hindi title,
  year, music directors, lyricists, singers (most songs first), song count, directors, sources, flags.
- `review.csv`: one row per flag. `no_wikidata` (no Wikidata item matched), `not_in_hfgk` (dated inside
  HFGK's years, no HFGK film), `year_mismatch` (`off_by_one` or `major`), `composer_mismatch`,
  `title_spelling` (HFGK against Wikidata), `song_count_mismatch`, `duplicate_in_source`, `match_review`
  (several Wikidata films fit: decide with `maestro-review`).
- `summary.json` / `summary.md`; `canon-check-<slug>.csv/.json` from check-canon.

`people.tsv` maps spellings of one music director (Hemanta Mukhopadhyay / Hemant Kumar) so they are not
flagged as disagreeing; add to it as the review list turns up more.
