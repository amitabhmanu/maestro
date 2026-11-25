# web/: data for the public explorer site (Netlify)

`netlify.toml` builds the site with `scripts/netlify-build.sh`: the explorer page from
`src/maestro/explorer/` plus `web/canon/` (copied as `canon/`).

To publish canons, write snapshots here and commit them:

    uv run maestro-explore --maestro maestros/rafi=<dsn> ... --out data/explorer
    uv run maestro-lyrics --pack maestros/rafi --snapshot data/explorer/canon/rafi.json   # must pass
    cp -r data/explorer/canon web/

Gem tags: the site shows `web/canon/gem-tags.json` (the ledger's active tags) to every visitor.
Refresh it after merging the explorer's tags into the ledger:

    uv run maestro-gem-tags site --ledger /mnt/project-files/maestro/gem-tags/gem-tags-ledger.json --out web/canon/gem-tags.json

Only publishable snapshots (no `--include-private`) may go here: this folder is public.
On Netlify there is no claude.ai runtime: a visitor's own tag changes are kept in their browser,
on top of the published tags, and Ask Claude is hidden.
