# Intrinsic gem pilot: Rafi, Hindi film songs (tier A)

4359 songs scored. AUC 0.5 = no better than chance; above 0.5 = the signal ranks the first group higher. 'Matched' compares only songs of the same decade and the same solo/duet status, which removes the lists' own era and solo bias.

## gems vs other unpopular songs (46 vs 2967)

| signal | AUC | 90% CI | matched AUC |
|---|---|---|---|
| solo | 0.71 | 0.66-0.75 | 0.50 |
| composer_rare | 0.63 | 0.56-0.69 | 0.63 |
| lyricist_rare | 0.53 | 0.46-0.61 | 0.52 |
| team_rare | 0.61 | 0.54-0.67 | 0.64 |
| costar_rare | 0.63 | 0.45-0.82 | 0.64 |
| actor_rare | 0.55 | 0.48-0.64 | 0.62 |
| banner_rare | 0.46 | 0.39-0.54 | 0.48 |
| subject_rare | 0.42 | 0.37-0.47 | 0.43 |
| few_rafi_songs_in_film | 0.52 | 0.46-0.59 | 0.54 |
| early_in_composer_career | 0.64 | 0.55-0.71 | 0.59 |
| lean_year_for_rafi | 0.45 | 0.38-0.53 | 0.47 |
| atypicality | 0.55 | 0.49-0.61 | 0.57 |

## any expert-listed song vs unlisted songs (123 vs 4190)

| signal | AUC | 90% CI | matched AUC |
|---|---|---|---|
| solo | 0.64 | 0.60-0.67 | 0.50 |
| composer_rare | 0.47 | 0.43-0.50 | 0.51 |
| lyricist_rare | 0.42 | 0.38-0.46 | 0.45 |
| team_rare | 0.44 | 0.40-0.48 | 0.49 |
| costar_rare | 0.49 | 0.43-0.58 | 0.49 |
| actor_rare | 0.53 | 0.48-0.58 | 0.56 |
| banner_rare | 0.43 | 0.40-0.48 | 0.47 |
| subject_rare | 0.48 | 0.44-0.51 | 0.47 |
| few_rafi_songs_in_film | 0.47 | 0.43-0.51 | 0.49 |
| early_in_composer_career | 0.53 | 0.49-0.57 | 0.49 |
| lean_year_for_rafi | 0.42 | 0.38-0.46 | 0.48 |
| atypicality | 0.43 | 0.39-0.47 | 0.48 |

## hits vs other songs (sanity check) (231 vs 4128)

| signal | AUC | 90% CI | matched AUC |
|---|---|---|---|
| solo | 0.53 | 0.50-0.56 | 0.50 |
| composer_rare | 0.27 | 0.25-0.30 | 0.31 |
| lyricist_rare | 0.31 | 0.28-0.33 | 0.35 |
| team_rare | 0.28 | 0.25-0.30 | 0.29 |
| costar_rare | 0.47 | 0.43-0.52 | 0.48 |
| actor_rare | 0.38 | 0.34-0.41 | 0.39 |
| banner_rare | 0.35 | 0.32-0.38 | 0.32 |
| subject_rare | 0.43 | 0.41-0.45 | 0.44 |
| few_rafi_songs_in_film | 0.37 | 0.34-0.40 | 0.36 |
| early_in_composer_career | 0.48 | 0.45-0.51 | 0.52 |
| lean_year_for_rafi | 0.44 | 0.41-0.47 | 0.50 |
| atypicality | 0.23 | 0.21-0.25 | 0.25 |

## Each expert list vs unpopular unlisted songs (atypicality and composer rarity)

| list | songs | atypicality AUC | composer_rare AUC |
|---|---|---|---|
| “Rafi’s Centenary Special: 35 songs from 35 years (1946-1980)” | 32 | 0.37 | 0.41 |
| “Rafi ‘s songs for “not the top heroes”” | 24 | 0.46 | 0.43 |
| “Rafi’s songs with some unrecognised composers” | 19 | 0.70 | 0.84 |
| “Rafi’s best songs by Madan Mohan” | 16 | 0.28 | 0.32 |
| “Some Less Heard Gems of Mohammad Rafi – The Sad Romantic Solos: 1940s through 5 | 15 | 0.54 | 0.63 |
| “Some Hidden Gems of Mohammad Rafi: The Romantic Solos” | 15 | 0.33 | 0.35 |
| “Classical Songs of Mohammad Rafi, With Some Thoughts on Gara” | 14 | 0.20 | 0.37 |
| “Rafi’s best songs by Kalyanji-Anandji” | 13 | 0.39 | 0.36 |
| “RD Burman’s best songs for Rafi” | 12 | 0.20 | 0.34 |
| “Naushad’s best songs for Mohammad Rafi” | 9 | 0.18 | 0.33 |
| “SN Tripathi Part 2 (post-1950s): His best songs for Mohammad Rafi” | 9 | 0.69 | 0.52 |
