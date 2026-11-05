// Parity: the TS port and the Rust/WASM port must give exactly what the Python
// decoder gives (all columns, kinds, NaN positions, hex ints, EEG bytes).
//
//   npm test              synthetic, edge-case and header-only fixtures
//   SKIP_WASM=1 npm test  TS port only
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as tsImpl from "../src/bisdecode.ts";
import type { Table } from "../src/bisdecode.ts";
import { summarize } from "../src/cohort.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, "..", "..");
const PY = process.env.PYTHON ?? join(REPO, ".venv", "bin", "python");

interface Impl {
  parseSpa(b: Uint8Array): Table;
  parseSpectra(b: Uint8Array): Table;
  parseEvents(b: Uint8Array): Table;
  parseOffset(b: Uint8Array): Table;
  parseStart(b: Uint8Array): string;
  parseEeg(b: Uint8Array, n?: number): { data: Int16Array; nChannels: number; nSamples: number };
}

const impls: [string, Impl][] = [["ts", tsImpl]];
if (!process.env.SKIP_WASM) {
  const pkg = join(REPO, "rust", "pkg-node", "bisdecode_wasm.js");
  assert.ok(existsSync(pkg), `WASM build missing: run 'npm run build:wasm' (or SKIP_WASM=1)`);
  impls.push(["wasm", createRequire(import.meta.url)(pkg) as Impl]);
}

type J = number | string | null;
const enc = (x: number): J =>
  Number.isNaN(x) ? null : x === Infinity ? "inf" : x === -Infinity ? "-inf" : x;

function toJson(t: Table) {
  return {
    nrows: t.nrows,
    columns: t.columns.map((c) => ({
      name: c.name,
      kind: c.kind,
      values: c.values instanceof Float64Array ? Array.from(c.values, enc) : [...c.values],
    })),
  };
}

function sameTable(got: Table, want: any, label: string) {
  const g = toJson(got);
  if (want.nrows === 0) {
    // pandas leaves an empty column as object dtype; compare names only
    assert.equal(g.nrows, 0, label);
    assert.deepEqual(g.columns.map((c) => c.name), want.columns.map((c: any) => c.name), label);
    return;
  }
  assert.deepEqual(g.columns.map((c) => c.name), want.columns.map((c: any) => c.name), `${label}: names`);
  for (let i = 0; i < want.columns.length; i++) {
    assert.deepEqual(g.columns[i], want.columns[i], `${label}: column ${want.columns[i].name}`);
  }
  assert.equal(g.nrows, want.nrows, label);
}

const work = mkdtempSync(join(tmpdir(), "bisparity-"));
const args = [join(REPO, "parity", "make_fixtures.py"), work];
const goldens: string[] = JSON.parse(execFileSync(PY, args, { encoding: "utf8", maxBuffer: 1 << 20 }).trim().split("\n").pop()!);

process.on("exit", () => rmSync(work, { recursive: true, force: true }));

for (const name of goldens) {
  const g = JSON.parse(readFileSync(join(work, name), "utf8"));
  const label = g.dir.slice(work.length + 1).split("/")[0];
  const file = (ext: string) => readFileSync(join(g.dir, `${g.base}.${ext}`));

  test(`cohort: ${label}`, () => {
    const spa = tsImpl.parseSpa(file("spa"));
    for (const c of g.cohort) {
      const got = Object.fromEntries(Object.entries(summarize(spa, g.base, c.channel, c.minSqi))
        .map(([k, v]) => [k, typeof v === "number" ? enc(v) : v]));
      assert.deepEqual(got, c.row, `${c.channel} minSqi=${c.minSqi}`);
    }
  });

  for (const [implName, impl] of impls) {
    test(`${implName}: ${label}`, () => {
      sameTable(impl.parseSpa(file("spa")), g.spa, "spa");
      if (g.spectra) sameTable(impl.parseSpectra(file("f_a")), g.spectra, "spectra");
      if (g.events) sameTable(impl.parseEvents(file("e_a")), g.events, "events");
      if (g.offset) sameTable(impl.parseOffset(file("o_a")), g.offset, "offset");
      if ("start" in g) assert.equal(impl.parseStart(file("t_a")), g.start, "start");
      if (g.eeg) {
        const e = impl.parseEeg(file("r2a"));
        assert.equal(e.nSamples, g.eeg.nSamples, "eeg samples");
        assert.equal(e.nChannels, g.eeg.nChannels, "eeg channels");
        const bytes = Buffer.from(e.data.buffer, e.data.byteOffset, e.data.byteLength);
        assert.equal(createHash("sha256").update(bytes).digest("hex"), g.eeg.sha256, "eeg bytes");
      }
    });
  }
}
