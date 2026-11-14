# Audio gem pilot for Rafi: findings (14 Nov 2026)

**Short answer: these audio features don't pick out songs experts admire, and public-domain audio barely includes the gems.**

## What was run

On Amitabh's PC: Rafi recordings published in 1965 or earlier (public domain in India), from the Internet Archive. 278 matched canon songs, and 211 were analysed. Each file was analysed from a temporary copy and deleted, and only the features were kept. There were 20 features: pitch range, top note, melodic leaps and interval variety, pitch-class spread, ornamentation (pitch wobble within notes), note rate, tempo, onset rate, beat regularity, spectral contrast, flatness, chroma polyphony, dynamic range and section changes. Each was z-scored against Rafi's own norm. They were combined into an "atypicality" score and a "craft" score, plus craft relative to the same composer's other songs.

## Results (AUC: 0.5 = chance)

- **Coverage:** only 3 of the 37 gems from up to 1965 have a recording on the archive, compared with 49 of 97 hits. Uploaders keep what is already loved, so the gem test itself could not be run.
- **Expert-listed songs vs ordinary songs (38 vs 84):** every feature falls between 0.34 and 0.58. Craft scores 0.43 and craft-vs-composer 0.38, both slightly *below* chance.
- **Hits vs ordinary songs (49 vs 84):** also chance level. The features don't even capture what made a song a hit. Only atypicality differs (0.33): hits are more typical.
- **Gems (3):** some AUCs near 0.8. With three songs that is noise.

## Why

1. Quality is a judgement about how the elements work together. Range, ornament counts and spectral richness measure quantity, and many beautiful songs are simple.
2. The features are crude. They were taken from noisy 78 transfers without separating the voice from the orchestra, so some of them measure the transfer as much as the music.
3. The data is biased: public-domain audio skews towards known songs. The gaps also show in the canon's own popularity data. For example, "Jane Kahan Mera Jigar Gaya Ji" counted as "ordinary".

## Conclusion

Opinion-free scoring doesn't work on either metadata or audio at this level of effort. Intrinsic features can still help as a **filter or a lead generator** once a human ear or an expert has judged quality. They should not be used to rank songs by quality. The 25 highest-craft ordinary songs in `audio-report` are worth a listen as leads.

A better audio pilot would need vocal separation (e.g. Demucs), a proper raga and taal model, and above all a source that covers obscure songs. That probably means Amitabh's own collection, which only he can provide.
