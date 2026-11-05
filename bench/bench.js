import * as T from "./bisdecode.min.js";
import init, * as W from "./wasm/bisdecode_wasm.js";

const WARM = 3, RUNS = 15;
const STEPS = [
  ["spa", "parseSpa"], ["f_a", "parseSpectra"], ["e_a", "parseEvents"],
  ["o_a", "parseOffset"], ["t_a", "parseStart"], ["r2a", "parseEeg"],
];
const $ = (id) => document.getElementById(id);
const median = (a) => { const s = [...a].sort((x, y) => x - y); return s[s.length >> 1]; };

let wasmInitMs = null;
async function ensureWasm() {
  if (wasmInitMs !== null) return;
  const t0 = performance.now();
  await init();
  wasmInitMs = performance.now() - t0;
}

function time(fn, bytes) {
  const first = (() => { const t = performance.now(); fn(bytes); return performance.now() - t; })();
  for (let i = 1; i < WARM; i++) fn(bytes);
  const ms = [];
  for (let i = 0; i < RUNS; i++) { const t = performance.now(); fn(bytes); ms.push(performance.now() - t); }
  return { first, median: median(ms) };
}

export async function runBench(files) {
  await ensureWasm();
  const byExt = {};
  for (const f of files) {
    const m = /\.([a-z0-9_]+)$/i.exec(f.name);
    if (m) byExt[m[1].toLowerCase()] = new Uint8Array(await f.arrayBuffer());
  }
  if (!byExt.spa) throw new Error("no .spa file in that folder");
  const rows = [];
  for (const [ext, fn] of STEPS) {
    const b = byExt[ext];
    if (!b) continue;
    rows.push({ ext, bytes: b.byteLength, ts: time(T[fn], b), wasm: time(W[fn], b) });
  }
  // whole case, cold-ish: one pass of every parser
  const whole = (impl) => { const t = performance.now(); for (const [ext, fn] of STEPS) if (byExt[ext]) impl[fn](byExt[ext]); return performance.now() - t; };
  const wholeTs = median(Array.from({ length: 9 }, () => whole(T)));
  const wholeWasm = median(Array.from({ length: 9 }, () => whole(W)));
  const res = { ua: navigator.userAgent, wasmInitMs, rows, wholeTs, wholeWasm };
  render(res);
  return res;
}

function render(r) {
  const f = (x) => x.toFixed(x < 10 ? 2 : 1);
  const cls = (a, b) => (a <= b ? "win" : "");
  let h = `<table><thead><tr><th>file</th><th>size</th><th>TS ms</th><th>WASM ms</th><th>TS first</th><th>WASM first</th></tr></thead><tbody>`;
  for (const x of r.rows) {
    h += `<tr><td>.${x.ext}</td><td>${(x.bytes / 1e6).toFixed(2)} MB</td>` +
      `<td class="${cls(x.ts.median, x.wasm.median)}">${f(x.ts.median)}</td>` +
      `<td class="${cls(x.wasm.median, x.ts.median)}">${f(x.wasm.median)}</td>` +
      `<td>${f(x.ts.first)}</td><td>${f(x.wasm.first)}</td></tr>`;
  }
  h += `</tbody><tfoot><tr><td>whole case (median of 9)</td><td></td>` +
    `<td class="${cls(r.wholeTs, r.wholeWasm)}">${f(r.wholeTs)}</td><td class="${cls(r.wholeWasm, r.wholeTs)}">${f(r.wholeWasm)}</td><td></td><td></td></tr></tfoot></table>` +
    `<p>WASM fetch + compile + instantiate: ${f(r.wasmInitMs)} ms · ${r.ua}</p>`;
  $("out").innerHTML = h;
}

$("pick").addEventListener("change", async (e) => {
  $("status").textContent = "running…";
  try { window.__results = await runBench([...e.target.files]); $("status").textContent = "done"; }
  catch (err) { $("status").textContent = String(err); window.__error = String(err); }
});
window.runBench = runBench;
