# Atlas lexicons

Shared lists that label every canon and register for the explorer's atlas (Places, Genres, Waves). Not a maestro pack: there is no `maestro.yaml` here.

- `lexicon/places.tsv`: world > region > country > state > city, with Wikidata ids, coordinates and old or other names (Bombay, Calcutta, Madras, बंबई) used to read places out of film banners and birthplaces. Country ids are ISO 3166 alpha-3 in lower case, matching the map shapes.
- `lexicon/genres.tsv`: three trees, music (forms and styles, including the forms of Hindi and Hollywood film song), film genres, and song themes; `maps_from` lists the source labels each node takes (explorer catalogue genres, Hindi Geetmala categories, HFGK subjects in English and Hindi).
- `lexicon/waves.tsv`: eras (which tile an industry's timeline) and movements (which overlap) of Bollywood and Hollywood, each with a definition, a rule for which films join it, and its sources. Every wave is sourced (Amitabh, 27 Nov 2026).

The map shapes are in `src/maestro/explorer/geo/world-india-view.topo.json`: Natural Earth 1:10m admin-0 countries, India point of view (public domain), simplified with mapshaper to 4% and quantised. India's official boundaries are used everywhere (Amitabh, 27 Nov 2026).

Check the lists with `uv run maestro-atlas check` (exits 1 on any problem); `maestro-atlas coverage --register web/canon/films/hindi-films.json --canon web/canon/rafi.json` reports how much of a snapshot they cover. Changes go through the user's review (gate R1 of the redesign plan).
