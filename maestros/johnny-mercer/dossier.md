# Johnny Mercer: profiler dossier

*Profiled 27 Nov 2026. Pack version 1 (draft), for MVP1 of the atlas (the Hollywood maestro).*

## Identity

| Field | Value | Source |
| --- | --- | --- |
| Wikidata | Q363698 | `maestro-identify` |
| MusicBrainz | b342d50e-401c-4c77-b7e4-2b3e7beaf00a (95507df9 merged into it) | musicbrainz.org |
| VIAF / ISNI / BnF | 79167222 / 0000000120183877 / 138974071 | Wikidata |
| Discogs | 164574 | discogs.com |
| Born / died | 18 Nov 1909, Savannah, Georgia / 25 Jun 1976, Bel Air, Los Angeles | Wikipedia, Georgia Encyclopedia |
| Name variants | John Herndon Mercer, John H. Mercer, J. Mercer | Wikidata, copyright entries |

Namesakes ruled out:

- Q20127929: Johnny Mercer, British Conservative politician (born 1981), ruled out by dates and occupation.

MusicBrainz also has credit-only artists that must be read as him plus others: "Harold Arlen & Johnny Mercer" (3f207c3c), "Johnny Mercer and The Pied Pipers", "The Pied Pipers with Johnny Mercer".

## Career and roles

- 1927-29: New York, actor and would-be singer. First published lyric, "Out of Breath (and Scared to Death of You)", in The Garrick Gaieties, 1930.
- 1932-34: wins Paul Whiteman's contest, joins Whiteman as singer and writer; "Lazybones" (with Hoagy Carmichael, 1933) is his first hit. First film song 1934.
- 1935-41: Hollywood. RKO (1935), then Warner Bros. with Richard Whiting (1937) and Harry Warren (from 1938): "Hooray for Hollywood", "Too Marvelous for Words", "Jeepers Creepers".
- 1942: co-founds Capitol Records with Buddy DeSylva and Glenn Wallichs, its first president. A hit singer 1942-49 ("Strip Polka", "Ac-Cent-Tchu-Ate-the-Positive", "Candy" with Jo Stafford, "Personality", "On the Atchison, Topeka and the Santa Fe", "Baby, It's Cold Outside" with Margaret Whiting).
- 1940s-50s: film songs with Harold Arlen ("Blues in the Night", "That Old Black Magic", "One for My Baby"), Broadway (St. Louis Woman 1946, Li'l Abner 1956, Saratoga 1959).
- 1961-63: Henry Mancini years ("Moon River", "Days of Wine and Roses", "Charade").
- 1969: co-founds the Songwriters Hall of Fame. Last Oscar nomination 1971. The Good Companions, London, 1974.

Share of output by role (MusicBrainz work credits, 413 works): lyrics only about 85-90%, words and music 5-8%, music to others' words about 5%. He also recorded 312 sides as a singer (DAHR, 1929-1967), many of others' songs.

## Output structure

The unit is the song (work). Each Mercer song was recorded by many singers, so recordings are versions, not units. About half his songs were written for films, so the film is the container; stage songs and stand-alone popular songs have no container. No source lists a film's songs completely, so `container_has_full_listing` is false and units are matched across containers by title. The Hollywood film register (`maestros/hollywood-films`) supplies the films and their partial song lists.

Proposed tiers: A film songs, B stage and stand-alone songs, C unpublished lyrics and posthumous completions (report only), D his own recordings as a singer of others' songs (report only).

## Published counts (checksum candidates)

| Scope | Count | Source |
| --- | --- | --- |
| Songs (lyrics), Complete Lyrics | more than 1,200 | Penguin Random House |
| Songs, published and unpublished | over 1,400 | Johnny Mercer Foundation, GSU |
| Songs | more than 1,500 | PBS (Robert Kimball interview) |
| Songs | more than 1,000 | Georgia Encyclopedia, Britannica |
| Films | at least 73 (1934-1973) | Encyclopedia.com |
| Films | 100 | Johnny Mercer Foundation |
| Stage productions | 13 musicals and revues / 23 productions | Encyclopedia.com / JMF |
| Oscar nominations, Best Original Song | 18 (19 counting the Darling Lili score), 4 wins | Wikipedia, JMF / Georgia Encyclopedia |
| Recordings as performer | 312 (1929-1967) | DAHR |
| MusicBrainz works | 413 (337 with a writing credit, 228 with an ISWC) | MusicBrainz API |
| Wikidata songs | 58 (lyricist 48, composer 14) | Wikidata |
| Wikipedia song tables | 54 (list article), 73 (main article) | Wikipedia |

The four Oscar wins: "On the Atchison, Topeka and the Santa Fe" (1946, Warren), "In the Cool, Cool, Cool of the Evening" (1951, Carmichael), "Moon River" (1961) and "Days of Wine and Roses" (1962, both Mancini).

## Collaborators

| Role | Most frequent (MusicBrainz works with him as lyricist) |
| --- | --- |
| Composers | Harold Arlen 33, Harry Warren 26, André Previn 18, Henry Mancini 13, Richard Whiting 13, Gene de Paul 11, Hoagy Carmichael 9, Bernie Hanighen 7, Jerome Kern 6, Rube Bloom 5 |
| Posthumous | Barry Manilow 16 |
| Duet partners (singer) | Jo Stafford, Margaret Whiting, The Pied Pipers |

## Attribution risks

- **English lyrics to foreign songs:** "Autumn Leaves" (Kosma, Prévert), "The Glow-Worm", "Summer Wind", "Song of India". He is lyricist of the English version only.
- **Lyrics added to existing instrumentals:** "Laura" (film 1944, lyric 1945), "Midnight Sun", "Satin Doll" (1953). Sources date these differently.
- **Unverified claims:** "Sophisticated Lady" (Encyclopedia.com) is not borne out elsewhere.
- **Shared credits:** "I Wanna Be Around" (with Sadie Vimmerstedt, who sent the idea), "Moon Dreams" (with Chummy MacGregor).
- **Posthumous completions:** Barry Manilow set unfinished lyrics ("When October Goes", 1984; about 16 works in MusicBrainz).
- **Songs he sang but did not write:** "Zip-a-Dee-Doo-Dah", "Candy", "Personality", "Baby, It's Cold Outside". Charts credit them to him as artist; they must not become his works.
- **Namesake:** Q20127929 (politician, born 1981).

## Sources

| Source | Coverage | Authority | Lineage | Access | Structure | Publishable |
| --- | --- | --- | --- | --- | --- | --- |
| MusicBrainz works | ~337 writing credits | medium | musicbrainz | api | structured | yes |
| Wikidata | 58 songs, films | medium | wiki | api | structured | yes |
| Wikipedia (list article, main article tables, award tables) | ~110 songs, all Oscar nominations | medium | wiki | crawlable | semi | yes |
| Hollywood film register | his films and their partial song lists | medium | wiki | local | structured | yes |
| Discogs | 19,000+ releases crediting him | medium | discogs | api | structured | yes (enrich only) |
| DAHR | 312 recordings as performer | high | label ledgers | saved pages (bot protection) | structured | yes |
| Catalog of Copyright Entries | every registered song | high | US Copyright Office | archive.org (not reachable from the cloud) | OCR | yes (public domain) |
| SecondHandSongs | ~167 works with cover counts | high | secondhandsongs | reference only (robots.txt forbids ClaudeBot) | structured | no |
| JazzStandards.com | ranks of ~31 Mercer songs in its top 1000 | medium | jazzstandards | crawlable | prose | yes |
| Last.fm, YouTube | listeners and views per song title | low | streaming | api (keys on the PC) | structured | yes |
| Complete Lyrics (Knopf 2009) | all lyrics | high | Kimball et al. | print | book | index only |

## Registers

- **Tiers A, B, C:** The Complete Lyrics of Johnny Mercer (print) is the only full register. The Georgia State song database ("over 1,400") is retired. The Catalog of Copyright Entries is a public-domain substitute for published songs, if its volumes can be read on the PC.
- **Tier D:** DAHR for 78s to 1967; no register for his later LPs.

## Popularity signals

| Dimension | Signals available |
| --- | --- |
| Contemporary | Oscar nominations and wins, Your Hit Parade, Billboard (his own records 1942-49) |
| Enduring | Wikipedia articles, Last.fm and YouTube per title, JazzStandards rank, reissues |
| Penetration | Cover counts (SecondHandSongs, reference only; MusicBrainz recordings per work), film and TV use |
| Esteem | AFI 100 Years...100 Songs (Moon River #4, Days of Wine and Roses #39), Grammy Hall of Fame, Songwriters Hall of Fame; gem lists by Frank Gagliano and Joe Lang (`expert_lists.tsv`) |

Unlike a singer's canon, a songwriter's popularity is per song across all performers: Last.fm and YouTube must be searched by title and filtered by writer, not read from his artist page.

## Open questions

- Is his singing in scope? Proposed: his recordings of others' songs are tier D, measured but not a target.
- Barry Manilow's posthumous settings: tier C, or out of the canon?
- English lyrics to foreign songs and lyrics added to instrumentals count as his works (lyricist credit only). Agree?
- Can Amitabh get The Complete Lyrics for its index?
- Stage shows have no container type; tier B songs without a container for MVP1.
- Film count: 73 or 100? Settle from the Hollywood film register once its 1930s-70s crawl is done.
