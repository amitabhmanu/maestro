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

Enrichment (read for films the register already has; never adds films):

| Source | What it gives | Extractor |
| --- | --- | --- |
| Wikidata, per film by QID | cast, producers, writers, cinematographer, editor, lyricists, banner, distributor, release dates (with country), box office and budget, awards and nominations (the film's, and a person's or song's "for" it), based on / remade as, soundtrack item, languages, IDs (IMDb, TMDB, Letterboxd, Indiancine.ma, Rotten Tomatoes, Bollywood Hungama, Hindi Wikipedia) | core `wikidata_film_details` |
| English Wikipedia film articles (films HFGK does not list) | track listings: songs with singers, lyricists, music directors, length, version (Reprise, Remix...), and the soundtrack article's label and release date | core `wikipedia_film_songs` |
| Wikipedia "List of film songs based on ragas" | the raga of ~600 Hindi film songs | `wikipedia_ragas` |
| HFGK film pages (on the next HFGK crawl) | the cast ("Artists") and the censor certificate (number, date, length, reels) | core `hfgk` |

Atul's Song A Day serves a bot challenge and is never crawled (import saved pages instead); IMDb is never
fetched (IDs come from Wikidata). Catalogue numbers need Discogs (a token) or MusicBrainz: not yet read.
HFGK: the site master's permission is needed before anything built on it goes live.

## Build

```
# Crawl each sweep (maestro-run plans the same three, but its resolve step is untested on a register):
uv run maestro-crawl --pack maestros/hindi-films --seed "<films_url('Q1568')>" --extractor wikidata_language_films
uv run maestro-crawl --pack maestros/hindi-films --seed https://en.wikipedia.org/wiki/Lists_of_Indian_films --extractor wikipedia_year --max-jobs 200
uv run maestro-crawl --pack maestros/hindi-films --seed https://hamraaz.org/filmssite/public/index.php --extractor hfgk --max-jobs 7000
uv run maestro-films match --pack maestros/hindi-films     # link each film's records across sources
# Enrichment: queue one job per batch of films the register has, then run the queue (no --seed):
uv run maestro-films plan --pack maestros/hindi-films --what wikidata          # ~100 requests
uv run maestro-films plan --pack maestros/hindi-films --what wikipedia_songs   # ~4,900 pages, ~2-3 hours
uv run maestro-crawl --pack maestros/hindi-films --max-jobs 7000
uv run maestro-crawl --pack maestros/hindi-films --seed https://en.wikipedia.org/wiki/List_of_film_songs_based_on_ragas --extractor wikipedia_ragas --purpose enrich
# Decide a flag the rules leave open (see to-review.csv); your decision always wins:
uv run maestro-films decide --pack maestros/hindi-films 7298 year_mismatch 1935 --note "censor date"
uv run maestro-films report --pack maestros/hindi-films --out data/films
uv run maestro-films check-canon --pack maestros/hindi-films --canon maestros/rafi --out data/films
uv run maestro-explore --maestro maestros/rafi --maestro maestros/hindi-films --out data/explorer   # Films view
```

The HFGK crawl is ~6,500 pages: about 3.5 hours at the pack's 0.5 requests a second.

## Cleanup rules

`match` breaks ties between equally good candidates by rule (the reason is kept on the `same_as` claim):
the only one with the exact title, else the only one whose title begins with this film's, else the only one
of the same year, else the only one with a Wikipedia article or IMDb ID (the others are duplicate Wikidata
items). A lone candidate whose music director disagrees is no match. Settled review items are closed.

`report` gives each flag a status: `resolved` (with the rule and the value used), `open` (in
`to-review.csv`, for you) or `info` (a gap, such as no Wikidata item). The rules:

- **Years**: inside HFGK's years, HFGK's year (from the censor certificate) when the others are a year off;
  when one source is years away and another agrees with HFGK, that source's record is probably another
  film of the same title (open). After HFGK, Wikipedia's year list (the Indian release) when Wikidata's
  date is earlier (often a festival premiere) or a year off.
- **Music directors**: HFGK's, when another source agrees or when only one other source disagrees (it
  credits every song; Wikidata often links the wrong person for older films); open when the years are far
  apart too, or when HFGK has no credit.
- **Titles**: a transliteration of the same title (same consonants), or a Hindi-script-only Wikidata label,
  is settled with HFGK's spelling first.
- Names match across more spellings: B/V (Brijlal/Vrijlal), run-together names (Sundar Das/Sunderdas
  Bhatia), one-letter differences with the same surname, a one-word name as the other's first word
  (Jaidev/Jaidev Verma), and teams written with a hyphen (Kalyanji-Anandji).

## Songs that are not new

`filmsongs.mark_reuse` marks a song **reused** when a later film lists the same song (same opening words,
music director and a singer in common, as HFGK lists re-released and renamed films) and **recreated** when
a later film's track listing remakes an older film's song (marked Recreated/Remix, or the older song's
music director credited). Neither counts in a film's `new_songs`; nor do versions inside one film
(Reprise, Female, Sad...). Remakes of whole films come from Wikidata (based on / remade as).

## Report files

- `films.csv`: one row per film: Wikidata and IMDb IDs, title (HFGK's transliteration first), Hindi title,
  year, music directors, lyricists, singers (most songs first), song count, directors, sources, flags.
- `review.csv`: one row per flag. `no_wikidata` (no Wikidata item matched), `not_in_hfgk` (dated inside
  HFGK's years, no HFGK film), `year_mismatch` (`off_by_one` or `major`), `composer_mismatch`,
  `title_spelling` (HFGK against Wikidata), `song_count_mismatch`, `duplicate_in_source`, `match_review`
  (several Wikidata films fit: decide with `maestro-review`).
- `to-review.csv`: only the flags the rules leave open, matches first, with the rule's note.
- `songs.csv`: one row per song: film, number, title (first line only), singers, lyricists, music directors,
  actors on screen, raga, singing (solo, duet, trio, group, + chorus), version, length, reused/recreated from.
- `films.csv` also has producers, cast, writers, banner, record company, release and censor dates, box office,
  awards won and nominated, based on / remade as, languages, outside IDs, new and reused song counts.
- `summary.json` / `summary.md`; `canon-check-<slug>.csv/.json` from check-canon.

`people.tsv` maps spellings of one music director (Hemanta Mukhopadhyay / Hemant Kumar) so they are not
flagged as disagreeing; add to it as the review list turns up more.
