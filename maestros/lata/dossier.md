# Lata Mangeshkar: profiler dossier

*Profiled 19 Nov 2026. Pack version 1 (draft). First pass: identity, registers and sources checked online; the career, counts and Tier B sections still need the full Stage 2 research once the scope questions are answered.*

## Identity

| Field | Value | Source |
| --- | --- | --- |
| Wikidata | Q156347 (only human match for the name) | `maestro-identify "Lata Mangeshkar"`, https://www.wikidata.org/wiki/Q156347 |
| MusicBrainz | aeb71bd8-447d-4415-8ea1-2b7d664f67e1 | Wikidata P434 |
| VIAF / ISNI / BnF | 15578255 / 0000000080955165 / 139215454 | Wikidata |
| Discogs | 342906 | Wikidata P1953 |
| HFGK V2.0 | 17375 (singer, 1,734 films, 1945-1985, 19 undated); 4276 (actor in 4 films, producer of 1) | https://hamraaz.org/filmssite/public/search.php?q=Lata+Mangeshkar |
| Hindi Geetmala | /singer/lata_mangeshkar.php ("5700+ songs", 571 pages) | https://www.hindigeetmala.net/singer/lata_mangeshkar.php |
| Born / died | 28 Sep 1929 / 6 Feb 2022 | Wikidata |
| Name variants | Lataji, Latha Mangeshkar, Lata Dinanath Mangeshkar, लता मंगेशकर, লতা মঙ্গেশকর | Wikidata aliases |

Namesakes ruled out: none on Wikidata. HFGK has two odd singer records, "Another Lata Mangeshkar" (11013, one duet with Shamshad Begum in Farmaish, 1953) and "Lata Mangeshkar + Rafi" (16433, one song in Ganga Jamuna, 1961); see open questions.

## Career and roles

Singer from 1942 (Marathi films) to 2019 ("Saugandh Mujhe Is Mitti Ki"); Hindi film songs on HFGK from 1945. Also an actor in the 1940s, a producer, and a composer of Marathi film music under the name Anandghan (1960s). Singer is by far the main role; the composer work is a few films.

## Output structure

Same shape as Rafi: the unit is a song, the container a film. HFGK lists every song of every Hindi film from 1931 to 1985, so films are checkable to 1985. From 1986 to 2019 no register exists in the project yet.

## Published counts (checksum candidates)

| Scope | Count | Source |
| --- | --- | --- |
| Hindi film songs to 1991 (figure given against the Guinness claim) | 5,025 | https://en.wikipedia.org/wiki/Lata_Mangeshkar |
| Bengali songs | 185 | same |
| Songs for Laxmikant-Pyarelal, over 35 years | over 700 | same |
| Guinness claims (disputed, not checksums) | 25,000 (1948-74), 30,000 (1948-87) | same |

## Collaborators

| Role | Most frequent (to be confirmed by the canon) |
| --- | --- |
| Composers | Laxmikant-Pyarelal, Shankar-Jaikishan, S. D. Burman, R. D. Burman, Madan Mohan, C. Ramchandra, Naushad, Salil Chowdhury, Kalyanji-Anandji, Hridaynath Mangeshkar |
| Co-singers | Mohammed Rafi, Kishore Kumar, Mukesh, Manna Dey, Hemant Kumar, Talat Mahmood, Asha Bhosle |

## Attribution risks

- Soundalikes often mislabelled as Lata: Suman Kalyanpur above all, also sisters Asha Bhosle, Usha and Meena Mangeshkar, and later Kavita Krishnamurthy and Anuradha Paudwal.
- Song counts: the Guinness figures were disputed (Rafi's camp claimed more); use only sourced, checkable counts.
- HFGK credit records above (combined "+ Rafi" credit, "Another Lata Mangeshkar").

## Sources

| Source | Coverage | Authority | Lineage | Access | Structure | Publishable |
| --- | --- | --- | --- | --- | --- | --- |
| HFGK Online V2.0 | Tier A 1945-1985, full song lists and credits | primary register (online edition of the print Geet Kosh) | hfgk | crawlable (permission before going live) | structured | facts yes |
| Hindi Geetmala | 5,700+ songs, all years, ragas, actors | careful secondary, copies HFGK to 1985 | hfgk | crawlable | semi | facts yes |
| Hindi film register (`maestros/hindi-films`) | every Hindi film; songs to 1985 | derived | wikidata / wiki / hfgk | built | structured | yes |
| MusicBrainz | releases and recordings, all years and languages | crowd database | musicbrainz | api | structured | yes (CC0) |
| Discogs | releases, catalogue numbers | crowd database | discogs | api | structured | facts yes |
| Wikipedia list of songs recorded by Lata Mangeshkar | 186 rows | editors' selection | wiki | crawlable | semi | yes |
| Atul's Song A Day, "List of songs yearwise" | every song posted on the blog | careful secondary | atul | saved page (bot check); the file saved for Rafi works | semi | facts yes |
| Songs of Yore and other blogs | expert posts | expert | songsofyore | saved pages only | prose | mentions only |
| Last.fm, YouTube (label uploads) | listening and views | data | own | api (keys on the PC) | structured | derived scores |
| aathavanitli-gani.com (Marathi songs) | Marathi film and non-film songs | to check | ? | not reachable from the cloud; check robots.txt from the PC | ? | ? |

## Registers

- **Tier A (Hindi film songs):** HFGK to 1985 (ground truth); print Geet Kosh vols. 1-5 to 1980 as a check. 1986-2019: none yet. Choices are Hindi Geetmala as the spine for those years, or the film register once it has post-1985 song data.
- **Tier B (other languages, non-film):** no single register. To build from MusicBrainz, Discogs, Wikipedia, and pages the user saves (Marathi and Bengali sites first), as for Rafi.
- **Tier C:** report only.

## Popularity signals

| Dimension | Signals available |
| --- | --- |
| Contemporary | Binaca Geetmala (full lists 1953-83 on Hindi Geetmala), Filmfare Best Female Playback (won 1959, 1963, 1966, 1970; 15 more nominations to 1971) |
| Enduring | Discogs reissues, Wikipedia articles, YouTube label uploads, Last.fm |
| Esteem | Songs of Yore and other expert posts (saved by the user), Atul's Song A Day |

## Open questions

See `open_questions` in `maestro.yaml`.
