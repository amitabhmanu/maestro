#!/usr/bin/env bash
# Assemble the static explorer site for Netlify in ./site:
#   index.html + catalogue.json from src/maestro/explorer,
#   canon/ (index.json and snapshots written by `maestro-explore --out`) from web/canon, if present.
# Without snapshots the page still opens on the maestro picker (every maestro shown as not ready).
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf site
mkdir -p site
cp src/maestro/explorer/index.html src/maestro/explorer/catalogue.json site/
if [ -f web/canon/index.json ]; then
  cp -r web/canon site/canon
  echo "netlify-build: copied $(find site/canon -name '*.json' | wc -l) canon files"
else
  echo "netlify-build: no web/canon/index.json; publishing the picker without canon snapshots"
fi
