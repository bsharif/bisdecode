#!/bin/sh
# Assemble bench/dist: page + minified TS bundle + WASM (web target). No data.
set -e
cd "$(dirname "$0")"
(cd ../ts && npm run -s build && npm run -s build:wasm >/dev/null)
rm -rf dist && mkdir -p dist/wasm
cp index.html bench.js dist/
cp ../ts/dist/bisdecode.min.js dist/
cp ../rust/pkg-web/bisdecode_wasm.js ../rust/pkg-web/bisdecode_wasm_bg.wasm dist/wasm/
echo "bench/dist ready"
