# Spike: in-browser decoding — Rust/WASM or TypeScript?

Branch `spike/browser-decode`, 5 Nov 2026. Not for merge or release.

**Recommendation: TypeScript.** Both ports give exactly the same output as
the Python decoder. WASM is about 1.5× faster, but on a full-size synthetic case that
saves about 40 ms. In exchange it is 19× bigger to download, has twice the
code, and adds a Rust toolchain to the build. Part 2 uses the TS port.

## What was built

| | `ts/src/bisdecode.ts` | `rust/src/core.rs` + `wasm.rs` |
|---|---|---|
| Parsers | `parseSpa`, `parseSpectra`, `parseEvents`, `parseOffset`, `parseStart`, `parseEeg` | same, same JS shapes |
| Output | `{nrows, columns:[{name, kind, values}]}`: `Float64Array` for time/float/int, `string[]` for text; `Int16Array` for EEG | same |
| Code (non-blank, non-comment lines) | **231** | **~480** (core ~410, bindings 67) |
| Dependencies | none | `wasm-bindgen`, `js-sys` (no `regex` crate, to keep the WASM small) |

## Parity

`cd ts && npm test` makes the fixtures with Python, runs both ports and
compares every column, kind, NaN position, hex integer and the SHA-256 of
the EEG samples. Fixtures: the `conftest.py` synthetic case, an edge-case case
(three channel blocks, ragged rows, text columns, `nan`/`inf`, odd hex, bad
dates, second 60, CR-only lines, wider spectra rows, odd EEG byte count) and
a header-only case. The rules are in `parity/SEMANTICS.md`.

Result: **6/6 pass** (3 fixtures × 2 ports). A deliberate sentinel bug makes
the edge test fail. Synthetic data only; Ben checks a real case himself.

## Speed (Chrome 156 headless, M1, median of 15 runs, 3 page loads)

Synthetic case of real size (~2.3 h) from `parity/make_bench_case.py`.

| File | Size | TS ms | WASM ms |
|---|---|---|---|
| `.spa` | 2.27 MB | 57 | 35 |
| `.f_a` | 3.11 MB | 53 | 39 |
| `.r2a` | 4.17 MB | 2.3 | 1.4 |
| **Whole case** | 9.6 MB | **~115** | **~76** |
| WASM fetch + compile + instantiate | | — | 1–8 |

The `.r2a` read, the expected strength of Rust, takes 2 ms in either port: it
is one typed-array copy. The time is in the text parsing and in making JS
strings and arrays, which WASM must also do at the boundary. Not measured on
the M4 yet: build `bench/` (`bench/build.sh`) and open `bench/dist/index.html`
in Chrome there; pick a `DH…` folder. On a phone, expect 3–5× slower; Part 2
should parse in a Web Worker so the page does not freeze.

## Download size (what the web app ships)

| | raw | gzip | brotli |
|---|---|---|---|
| TS, minified | 4.8 KB | **2.2 KB** | 2.0 KB |
| WASM + JS glue (minified) | 71.8 + 6.3 KB | **42.6 KB** | 38.1 KB |

## Could a PyO3 build put PyPI on the same Rust core?

Yes, technically: `core.rs` has no JS types, so a `pyo3` + `maturin` crate
could wrap it, and `decode.py` would build DataFrames from its columns. The
cost:

- **Releases:** `bisdecode` stops being one pure-Python wheel. It needs a
  wheel per platform (manylinux x86_64/aarch64, musllinux, macOS
  x86_64/arm64, Windows; `abi3` keeps it to one per platform), built by
  `maturin-action` in a matrix: about 6 jobs instead of 1 on every release.
  Users on any other platform need a Rust toolchain to install from sdist.
- **CI:** each pytest job (3.9–3.12) must compile the extension first;
  `publish.yml` becomes a build matrix plus an upload job.
- **Reference:** the Python decoder is the version checked against the BIS
  PDFs. Moving its core to Rust changes the thing every result is checked
  against, for no gain: Python users decode in batch, and speed is not a
  problem there.

So the "one core" benefit is real but expensive. The cheaper way to keep the
two in step is what this spike does: Python stays the reference, and the
parity test fails if the TS port drifts. It needs Python and Node, but no Rust.

## Decision

Use the TS port in Part 2. Reasons, in order: the same exact output; a speed
difference that a user cannot see; 2 KB instead of 43 KB; half the code, in
the language the web app already uses; no Rust step in the bis-data Netlify
build; and no change to how `bisdecode` is built or released.

Follow-ups if this goes further: run the parity test in `bisdecode` CI (add a
Node step); decide whether the TS port is published to npm or copied into
bis-data; drop `rust/` from the branch.
