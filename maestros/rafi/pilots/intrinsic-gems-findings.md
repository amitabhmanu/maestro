# Intrinsic gem pilot for Rafi: findings (14 Nov 2026)

**Short answer: metadata alone does not find gems.** No opinion-free metadata signal ranks the 46 expert-named gems (Hindi film songs) clearly above Rafi's other unpopular songs once the gem lists' own biases are removed. The combined "unusual for Rafi" score lands at AUC 0.57 matched (0.5 is chance).

## What was tested

All 4,359 Hindi film songs in the canon snapshot, each scored against Rafi's own norm on 11 signals: solo or not, how rarely he sang for that composer, lyricist, composer-lyricist team, co-singer, on-screen actor, banner and film genre, how few of his songs the film had, how early the song came in its composer's career, and how lean a year it was for him. Yardsticks: the 48 gems from PR #27 (46 are Hindi film songs), the other 123 songs named in any expert post, and the 231 Binaca and Filmfare hits.

## What the numbers say

- **Solo** looks strong (AUC 0.71) only because two of the three gem lists are solo lists. Compared within the same decade and solo status, it is 0.50, exactly chance.
- **Rare composer** (0.63) is circular: one gem list is literally "songs with unrecognised composers". Without that list's songs, it is 0.48.
- **Early in the composer's career** is the only signal left standing (0.66 on the 28 gems outside that list, 0.59 matched), and it is weak and probably reflects the 1940s-50s list.
- **The sanity check works the other way round:** hits score very low on every rarity signal (atypicality AUC 0.23). So these signals measure obscurity. They tell us where unpopular songs are, which the popularity filter already does, not which of those songs are good.
- **Expert best-of and classical lists score below ordinary songs** on atypicality (0.18 to 0.28). Experts praise songs by major composers, which metadata calls "typical".

## Why

Every field the canon holds (credits, film, year, cast) describes a song's context, not its music. Quality lives in the music: the melody, the raga and how it is handled, the singing, the arrangement. A raga or taal field would be a step closer, but the canon has none. Wikipedia raga articles list a handful of Rafi songs each (the Yaman page names him 9 times), mostly famous ones, so that route gives thin, popularity-biased coverage.

## What would work

The only way to measure the music itself is from audio: vocal range and tessitura, melodic intervals and ornamentation, rhythmic complexity, how many instruments and how they move, compared within Rafi's own songs and within each composer's work. Recordings published in 1965 or earlier are in the public domain in India (60 years from the year after publication), and many such 78s are on the Internet Archive. The compositions and lyrics in them can still be protected (60 years after the composer's or lyricist's death), and US status differs, so the pilot would stream each file, extract features from ~300 of them and keep only the features, never the audio. That needs Amitabh's go-ahead.

Tables: [intrinsic-gems-report.md](intrinsic-gems-report.md). Script: `maestros/rafi/pilots/intrinsic_gems.py`.
