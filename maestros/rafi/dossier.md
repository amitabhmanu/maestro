# Mohammed Rafi: profiler dossier

*Profiled 12 Nov 2026 (first pass, web research only). Pack version 1, status draft.*

## Identity

| Field | Value | Source |
| --- | --- | --- |
| Wikidata, MusicBrainz, VIAF, ISNI | Not yet resolved: run `uv run maestro-identify "Mohammed Rafi"` where wikidata.org is reachable | |
| Born / died | 24 Dec 1924, Kotla Sultan Singh, Punjab / 31 Jul 1980, Bombay | [Wikipedia](https://en.wikipedia.org/wiki/Mohammed_Rafi) |
| Active | 1944–1980 (first recording 1941, Punjabi film) | [Wikipedia](https://en.wikipedia.org/wiki/Mohammed_Rafi), [Dawn review of *Geet Gata Chal*](https://www.dawn.com/news/amp/1890805) |
| Name variants | Mohd. Rafi, Md. Rafi, Mohammad Rafi, محمد رفیع, मोहम्मद रफ़ी | |

Namesakes ruled out: none checked yet.

## Career and roles

Playback singer for Hindi films from the mid-1940s to 1980, with songs in many other Indian languages (Punjabi, Konkani, Bengali, Marathi, Tamil, Telugu and others) and a few in English, Persian, Arabic and Dutch. Singing is effectively 100% of the canon; a few screen appearances are out of scope.

## Output structure

Unit: a song. Container: a film. Songs belong to films, and the Hindi Film Geet Kosh lists every song of every Hindi film 1931–1980, so the film is a checkable container. Non-film songs (ghazals, bhajans, naats) have no container and form part of Tier B.

## Published counts (checksum candidates)

| Scope | Count | Source |
| --- | --- | --- |
| Hindi film songs | 4,425 | Shahid Rafi and Sujata Dev (2015), via [Wikipedia](https://en.wikipedia.org/wiki/Mohammed_Rafi) |
| Non-Hindi film songs | 310 | same |
| Non-film songs | 328 | same |
| All songs found by researchers | 7,405 | Manorama Online (2015), via Wikipedia |
| Hindi film songs to 1980 (Hamraz register) | ~4,525 | [mohdrafi.com](https://mohdrafi.com/meri-awaaz-suno/setting-the-records-straight.html/comment-page-1) |
| 1944–1950 | 341 (132 solo, 169 duet, 40 with 3+ singers) | [Atul's Song A Day](https://atulsongaday.me/2014/08/20/zahe-kismat-teri-mehfil-se-jo-paighaam-aaya-hai/) |
| Composer Laxmikant–Pyarelal | 388 | [Wikipedia](https://en.wikipedia.org/wiki/Mohammed_Rafi) |
| Composer Shankar–Jaikishan | 341 | same |
| Composer O. P. Nayyar | 197 | same |
| Composer Kalyanji–Anandji | ~170 | same |
| Composer Naushad | 149 | same |

The 25,000–28,000 figures Rafi himself gave (BBC 1977; Guinness 1984) are not credible and are not checksums. The 7,405 figure likely counts something broader than released Hindi film songs; reconcile in the dry run.

## Collaborators

| Role | Most frequent |
| --- | --- |
| Composers | Laxmikant–Pyarelal, Shankar–Jaikishan, O. P. Nayyar, Kalyanji–Anandji, Naushad, S. D. Burman, Ravi, Madan Mohan |
| Co-singers | Lata Mangeshkar, Asha Bhosle, Geeta Dutt, Shamshad Begum |

## Attribution risks

- Soundalikes credited as Rafi online: Anwar, Shabbir Kumar, Mahendra Kapoor, and tribute covers by Sonu Nigam.
- Duets credited only to the female singer; chorus parts uncredited.
- Inflated totals in circulation (above).

## Sources

| Source | Coverage | Authority | Lineage | Access | Structure | Publishable |
| --- | --- | --- | --- | --- | --- | --- |
| Hindi Film Geet Kosh (5 vols.) | All Hindi film songs 1931–1980 | Register | hfgk | Print, order from Kanpur | Structured (print) | Facts yes |
| Geet Gata Chal (Bhatti) | Hindi and other songs | Independent listing | bhatti | Print | Structured (print) | Facts yes |
| hindigeetmala | Songs, films, credits | Secondary | hfgk (likely) | Crawlable (check robots.txt) | Semi | Facts yes |
| Atul's Song A Day | Per-song essays, picturisation | Careful secondary | atul | Crawlable | Prose | Facts yes, text no |
| Songs of Yore | Essays, esteem | Careful secondary | songsofyore | Crawlable | Prose | Facts yes, text no |
| Wikipedia song lists | Partial lists | Aggregator | wiki | Crawlable | Semi | Yes (CC BY-SA) |
| Wikidata, MusicBrainz | Partial, IDs | Aggregator | wiki / musicbrainz | API | Structured | Yes (CC0) |

## Registers

- Tier A: Hindi Film Geet Kosh (ground truth); Geet Gata Chal as an independent check.
- Tier B: Geet Gata Chal partly; no complete register known for non-film songs.
- Tier C: none; report only.

## Popularity signals

| Dimension | Signals available |
| --- | --- |
| Contemporary | Binaca Geetmala annual lists; Filmfare (6 wins); National Award; Padma Shri 1967 |
| Enduring | YouTube, Last.fm, JioSaavn, Saregama Carvaan playlists |
| Penetration | Covers, reality-show performances |
| Esteem | Songs of Yore, Atul's Song A Day, books |

## Open questions

- Confirm Wikidata, MusicBrainz and VIAF IDs (blocked in the cloud session; run `maestro-identify` locally).
- Is a copy of HFGK available to digitise, and which volumes?
- What does the 7,405 count include?
