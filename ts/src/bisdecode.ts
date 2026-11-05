// In-browser port of bisdecode's parsers (src/bisdecode/decode.py).
//
// Every function takes the raw file bytes and returns columns as typed arrays.
// The behaviour mirrors the Python decoder (pandas 3.x) exactly on the parity
// fixtures; see ../../parity/ and SEMANTICS.md for the edge-case rules.

export type ColumnKind = "time" | "float" | "int" | "string";

/** time: epoch ms (naive clock time read as UTC), NaN = NaT.
 *  float: NaN = missing / sentinel.  int: integer value, NaN = <NA>.
 *  string: text, null = missing. */
export interface Column {
  name: string;
  kind: ColumnKind;
  values: Float64Array | (string | null)[];
}

export interface Table {
  nrows: number;
  columns: Column[];
}

export interface Eeg {
  /** Interleaved samples: [ch0, ch1, ch0, ch1, ...] as raw ADC counts. */
  data: Int16Array;
  nChannels: number;
  nSamples: number;
}

export const SENTINELS = new Set([-3276.8, -3276.0, -327.7, 3276.8]);
export const HEX_PARAMS = new Set(["ARTF2", "BISBIT00"]);
export const SPECTRA_BINS = 60;

// ---------- text helpers (Python semantics) ----------

const decoder = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

/** Python `open(..., "r").read()` with UTF-8: strict, BOM kept. */
export function decodeText(bytes: Uint8Array): string {
  return decoder.decode(bytes);
}

// Python str.splitlines() boundaries.
const LINE_BREAK = /\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/;

export function splitlines(s: string): string[] {
  if (s === "") return [];
  const out = s.split(LINE_BREAK);
  // splitlines() gives no empty element after a final line break
  if (out.length > 1 && out[out.length - 1] === "") out.pop();
  return out;
}

// Python whitespace (str.isspace): JS \s minus U+FEFF, plus \x1c-\x1f and \x85.
const WS = "\\t\\n\\v\\f\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const STRIP_RE = new RegExp(`^[${WS}]+|[${WS}]+$`, "g");
/** Python str.strip(). */
const IS_WS = new RegExp(`[${WS}]`);
export function pyStrip(s: string): string {
  // fast path: most cells have no surrounding whitespace
  if (s === "" || (!IS_WS.test(s[0]) && !IS_WS.test(s[s.length - 1]))) return s;
  return s.replace(STRIP_RE, "");
}

// pd.to_numeric(errors="coerce") on one string. Returns undefined if it does
// not parse; "nan" parses to NaN (pandas accepts it).
const NUM_RE = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;
const SPECIAL_RE = /^([+-]?)(inf|infinity|nan)$/i;
export function toNumeric(raw: string | null): number | undefined {
  if (raw === null) return NaN;                 // None -> NaN
  const s = pyStrip(raw);
  if (NUM_RE.test(s)) return Number(s);
  const m = SPECIAL_RE.exec(s);
  if (m) {
    if (m[2].toLowerCase() === "nan") return NaN;
    return m[1] === "-" ? -Infinity : Infinity;
  }
  return undefined;
}

// Python int(v, 16): sign, optional 0x prefix, single '_' between digits.
const HEX_RE = /^([+-]?)(?:0[xX]_?)?([0-9a-fA-F]+(?:_[0-9a-fA-F]+)*)$/;
export function hexToInt(v: string): number {
  if (!v) return NaN;
  const m = HEX_RE.exec(pyStrip(v));
  if (!m) return NaN;
  const n = parseInt(m[2].replace(/_/g, ""), 16);
  return m[1] === "-" ? -n : n;
}

// pd.to_datetime(format="%m/%d/%Y %H:%M:%S", errors="coerce") -> epoch ms.
// 1-2 digit fields, exactly 4-digit year, seconds 0-61 (60/61 roll over).
const TS_RE = /^(\d{1,2})\/(\d{1,2})\/(\d{4}) (\d{1,2}):(\d{1,2}):(\d{1,2})$/;
const DIM = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

function daysFromCivil(y: number, m: number, d: number): number {
  y -= m <= 2 ? 1 : 0;
  const era = Math.floor(y / 400);
  const yoe = y - era * 400;
  const doy = Math.floor((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5) + d - 1;
  const doe = yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy;
  return era * 146097 + doe - 719468;
}

export function parseTime(s: string | null): number {
  if (s === null) return NaN;
  const m = TS_RE.exec(s);
  if (!m) return NaN;
  const [mo, d, y, h, mi, se] = m.slice(1).map(Number);
  if (y < 1 || mo < 1 || mo > 12 || d < 1 || h > 23 || mi > 59 || se > 61) return NaN;
  const leap = (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
  if (d > (mo === 2 && leap ? 29 : DIM[mo - 1])) return NaN;
  return (daysFromCivil(y, mo, d) * 86400 + h * 3600 + mi * 60 + se) * 1000;
}

function isSentinel(x: number): boolean {
  return SENTINELS.has(x) || x <= -3276.0;
}

// ---------- parsers ----------

/** .spa per-second processed trend -> wide table '<chan>:<param>'. */
export function parseSpa(bytes: Uint8Array): Table {
  const lines = splitlines(decodeText(bytes));
  if (lines.length < 2) throw new Error(".spa needs two header rows");
  const chRow = lines[0].split("|");
  const nameRow = lines[1].split("|");

  const names: string[] = [];
  let curCh: string | null = null;
  nameRow.forEach((name, i) => {
    const ch = i < chRow.length ? pyStrip(chRow[i]) : "";
    if (ch.startsWith("Ch")) curCh = ch.replace(/ /g, "");
    const nm = pyStrip(name);
    if (i === 0) names.push("timestamp");
    else if (curCh && nm !== "") names.push(`${curCh}:${nm}`);
    else names.push(nm || `col${i}`);
  });

  const ncol = names.length;
  const cells: string[][] = [];
  for (let k = 2; k < lines.length; k++) {
    const ln = lines[k];
    if (pyStrip(ln) === "") continue;
    const f = ln.split("|", ncol);
    while (f.length < ncol) f.push("");          // ragged rows -> "" (fillna)
    cells.push(f);
  }
  const nrows = cells.length;
  const columns: Column[] = [];

  for (let j = 0; j < ncol; j++) {
    const name = names[j];
    if (j === 0) {
      const v = new Float64Array(nrows);
      for (let r = 0; r < nrows; r++) v[r] = parseTime(cells[r][0]);
      columns.push({ name, kind: "time", values: v });
      continue;
    }
    const txt = new Array<string>(nrows);
    let allBlank = true;
    for (let r = 0; r < nrows; r++) {
      txt[r] = pyStrip(cells[r][j]);
      if (txt[r] !== "") allBlank = false;
    }
    if (name.startsWith("col") && allBlank) continue;   // trailing '|' field
    const param = name.split(":").pop()!;
    if (HEX_PARAMS.has(param)) {
      const v = new Float64Array(nrows);
      for (let r = 0; r < nrows; r++) v[r] = hexToInt(txt[r]);
      columns.push({ name, kind: "int", values: v });
      continue;
    }
    const v = new Float64Array(nrows);
    let numeric = true;
    for (let r = 0; r < nrows; r++) {
      if (txt[r] === "") { v[r] = NaN; continue; }
      const x = toNumeric(txt[r]);
      if (x === undefined || Number.isNaN(x)) { numeric = false; break; }
      v[r] = isSentinel(x) ? NaN : x;
    }
    if (numeric) columns.push({ name, kind: "float", values: v });
    else columns.push({ name, kind: "string", values: txt });
  }
  return { nrows, columns };
}

/** .f_a density spectral array -> timestamp + bin00..binNN. */
export function parseSpectra(bytes: Uint8Array): Table {
  const lines = splitlines(decodeText(bytes));
  const rows: string[][] = [];
  for (let k = 2; k < lines.length; k++) {
    const ln = lines[k];
    if (pyStrip(ln) === "" || ln.indexOf("|") === -1) continue;
    const fields = ln.split("|");
    const bins = fields[1].split(",");
    if (pyStrip(bins[bins.length - 1]) === "") bins.pop();   // trailing comma only
    rows.push([fields[0], ...bins]);
  }
  let nBins = SPECTRA_BINS;
  for (const r of rows) nBins = Math.max(nBins, r.length - 1);
  const nrows = rows.length;
  const ts = new Float64Array(nrows);
  const bins = Array.from({ length: nBins }, () => new Float64Array(nrows));
  for (let r = 0; r < nrows; r++) {
    const row = rows[r];
    ts[r] = parseTime(row[0]);
    for (let b = 0; b < nBins; b++) {
      const x = b + 1 < row.length ? toNumeric(row[b + 1]) : NaN;
      bins[b][r] = x === undefined ? NaN : x;
    }
  }
  const columns: Column[] = [{ name: "timestamp", kind: "time", values: ts }];
  bins.forEach((v, b) => columns.push({ name: `bin${String(b).padStart(2, "0")}`, kind: "float", values: v }));
  return { nrows, columns };
}

// Digits are ASCII [0-9] here; Python's \d also takes other Unicode digits,
// which a BIS export never contains. \w stays Unicode-aware like Python's.
const S = `[${WS}]`;
const TS = "([0-9]{2}/[0-9]{2}/[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2})";
const EVENT_RE = new RegExp(
  `^${TS}${S}+>${S}+([\\p{L}\\p{N}_]+)${S}+(.*?)(?:${S}+\\(ID ([0-9]+)\\))?${S}*$`, "u");

/** .e_a events -> timestamp, kind, msg, id (id kept as text, like Python). */
export function parseEvents(bytes: Uint8Array): Table {
  const ts: number[] = [], kind: string[] = [], msg: string[] = [];
  const id: (string | null)[] = [];
  for (const ln of splitlines(decodeText(bytes))) {
    const m = EVENT_RE.exec(pyStrip(ln));
    if (!m) continue;
    ts.push(parseTime(m[1])); kind.push(m[2]); msg.push(m[3]); id.push(m[4] ?? null);
  }
  return {
    nrows: ts.length,
    columns: [
      { name: "timestamp", kind: "time", values: Float64Array.from(ts) },
      { name: "kind", kind: "string", values: kind },
      { name: "msg", kind: "string", values: msg },
      { name: "id", kind: "string", values: id },
    ],
  };
}

const OFFSET_RE = new RegExp(`^${TS}${S}*\t${S}*([0-9]+)${S}*\t${S}*([0-9]+)`);

/** .o_a raw-EEG offset index -> timestamp (text, as in Python), offset, size. */
export function parseOffset(bytes: Uint8Array): Table {
  const ts: string[] = [], off: number[] = [], size: number[] = [];
  for (const ln of splitlines(decodeText(bytes))) {
    const m = OFFSET_RE.exec(ln.replace(/\r+$/, ""));
    if (!m) continue;
    ts.push(m[1]); off.push(Number(m[2])); size.push(Number(m[3]));
  }
  return {
    nrows: ts.length,
    columns: [
      { name: "timestamp", kind: "string", values: ts },
      { name: "offset", kind: "int", values: Float64Array.from(off) },
      { name: "size", kind: "int", values: Float64Array.from(size) },
    ],
  };
}

/** .t_a case start time (raw text, stripped). */
export function parseStart(bytes: Uint8Array): string {
  return pyStrip(decodeText(bytes));
}

/** .r2a raw EEG: int16 little-endian, nChannels interleaved. */
export function parseEeg(bytes: Uint8Array, nChannels = 2): Eeg {
  const nWords = bytes.byteLength >> 1;
  const n = Math.floor(nWords / nChannels) * nChannels;
  const data = new Int16Array(n);
  const dv = new DataView(bytes.buffer, bytes.byteOffset, n * 2);
  for (let i = 0; i < n; i++) data[i] = dv.getInt16(i * 2, true);
  return { data, nChannels, nSamples: n / nChannels };
}
