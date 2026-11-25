# web/: data for the public explorer site (Netlify)

`netlify.toml` builds the site with `scripts/netlify-build.sh`: the explorer page from
`src/maestro/explorer/` plus `web/canon/` (copied as `canon/`).

To publish canons, write snapshots here and commit them:

    uv run maestro-explore --maestro maestros/rafi=<dsn> ... --out data/explorer
    uv run maestro-lyrics --pack maestros/rafi --snapshot data/explorer/canon/rafi.json   # must pass
    cp -r data/explorer/canon web/

Only publishable snapshots (no `--include-private`) may go here: this folder is public.
On Netlify there is no claude.ai runtime, so gem tags are kept in the viewer's browser
and Ask Claude is hidden.
