// Port of bisdecode.cohort.summarize: one row of case-level metrics from a
// decoded .spa trend. Matches Python (pandas 3.x / numpy 2.x) exactly, which
// needs numpy's pairwise sum, numpy's quantile interpolation and the two
// rounding rules (numpy float vs Python float). Missing values are null.
import { toNumeric, type Table } from "./bisdecode.ts";

export const TARGET_LO = 40, TARGET_HI = 60, SQI_RELIABLE = 50;

export type CohortValue = number | string | null;
export type CohortRow = Record<string, CohortValue> & { case: string };

// ---------- numpy / Python numerics ----------

/** numpy's pairwise summation (np.add.reduce on a contiguous float64 array). */
export function npSum(a: ArrayLike<number>, lo = 0, n = a.length): number {
  if (n < 8) {
    let r = -0.0;                       // numpy starts the short loop at -0.0
    for (let i = 0; i < n; i++) r += a[lo + i];
    return r;
  }
  if (n <= 128) {
    const r = [a[lo], a[lo + 1], a[lo + 2], a[lo + 3], a[lo + 4], a[lo + 5], a[lo + 6], a[lo + 7]];
    let i = 8;
    for (; i < n - (n % 8); i += 8) for (let j = 0; j < 8; j++) r[j] += a[lo + i + j];
    let res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
    for (; i < n; i++) res += a[lo + i];
    return res;
  }
  let n2 = Math.floor(n / 2);
  n2 -= n2 % 8;
  return npSum(a, lo, n2) + npSum(a, lo + n2, n - n2);
}

function rintEven(y: number): number {
  const r = Math.round(y);
  return Math.abs(y - Math.trunc(y)) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

/** round() on a numpy float64: rint(x * 10**n) / 10**n. */
export function npRound(x: number, n: number): number {
  if (!Number.isFinite(x)) return x;
  const f = 10 ** n;
  return rintEven(x * f) / f;
}

/** round() on a Python float: exact decimal value, ties to even. */
export function pyRound(x: number, n: number): number {
  if (!Number.isFinite(x)) return x;
  // A real tie is a dyadic number with at most n+1 decimals, so its long
  // expansion is "...5000…". (0.05 is not a tie: it is 0.05000000000000000277…)
  const exact = x.toFixed(Math.min(100, n + 40));
  const dot = exact.indexOf(".");
  const tail = exact.slice(dot + 1 + n);
  if (/^50*$/.test(tail)) {
    const down = exact.slice(0, n ? dot + 1 + n : dot);   // truncated toward zero
    const last = Number(down[down.length - 1]);
    const up = Number(down) + 10 ** -n * Math.sign(x);
    return Number((last % 2 === 0 ? Number(down) : up).toFixed(n));
  }
  return Number(x.toFixed(n));
}

const nanToNull = (x: number): number | null => (Number.isNaN(x) ? null : x);

function sorted(v: number[]): Float64Array {
  return Float64Array.from(v).sort();
}

/** pandas/numpy median (NaN ignored). */
function median(v: number[]): number {
  const s = sorted(v.filter((x) => !Number.isNaN(x)));
  const n = s.length;
  if (!n) return NaN;
  const h = n >> 1;
  return n % 2 ? s[h] : (s[h - 1] + s[h]) / 2;
}

/** pandas Series.quantile(q), numpy "linear" method with numpy's lerp. */
function quantile(v: number[], q: number): number {
  const s = sorted(v);
  if (!s.length) return NaN;
  const idx = (s.length - 1) * q, lo = Math.floor(idx), t = idx - lo;
  const a = s[lo], b = s[Math.min(lo + 1, s.length - 1)], d = b - a;
  return t >= 0.5 ? b - d * (1 - t) : a + d * t;
}

const mean = (v: number[]) => npSum(v) / v.length;
const minOf = (v: ArrayLike<number>) => { let m = Infinity; for (let i = 0; i < v.length; i++) if (v[i] < m) m = v[i]; return m; };
const maxOf = (v: ArrayLike<number>) => { let m = -Infinity; for (let i = 0; i < v.length; i++) if (v[i] > m) m = v[i]; return m; };

/** pandas Series.std() (ddof=1). */
function std(v: number[]): number {
  const n = v.length;
  if (n < 2) return NaN;
  const avg = npSum(v) / n;
  return Math.sqrt(npSum(v.map((x) => (avg - x) ** 2)) / (n - 1));
}

const pct = (n: number, d: number) => (d ? npRound((100.0 * n) / d, 1) : NaN);
const count = (v: number[], f: (x: number) => boolean) => v.reduce((c, x) => c + (f(x) ? 1 : 0), 0);

// ---------- summarize ----------

/**
 * Case-level metrics for one channel of a parsed .spa table.
 * `minSqi`: use only seconds with SQI >= minSqi for BIS/SR/EMG metrics.
 */
export function summarize(spa: Table, base: string, channel = "Ch1", minSqi: number | null = null): CohortRow {
  const n = spa.nrows;
  const get = (param: string): Float64Array => {
    const c = spa.columns.find((x) => x.name === `${channel}:${param}`);
    if (!c) return new Float64Array(n).fill(NaN);
    if (c.values instanceof Float64Array) return c.values;
    // Python reads the trend back from CSV, which turns an all-number text
    // column (e.g. "nan", "-inf", "5.") into floats. A real text column would
    // make Python fail; here it counts as missing.
    const v = c.values.map((x) => (x === null || x === "" ? NaN : toNumeric(x)));
    return v.every((x) => x !== undefined) ? Float64Array.from(v as number[]) : new Float64Array(n).fill(NaN);
  };
  const bis = get("DB13U01"), sr = get("SR12"), emg = get("EMGLOW01"), sqi = get("SQI10");
  const ts = spa.columns.find((c) => c.name === "timestamp")!.values as Float64Array;

  const valid: boolean[] = [], excluded: boolean[] = [];
  for (let i = 0; i < n; i++) {
    let v = !Number.isNaN(bis[i]);
    let ex = false;
    if (minSqi !== null) {
      const reliable = sqi[i] >= minSqi;          // NaN -> false
      ex = v && !reliable;
      v = v && reliable;
    }
    valid.push(v); excluded.push(ex);
  }
  const pick = (a: Float64Array, m: (i: number) => boolean) => {
    const out: number[] = [];
    for (let i = 0; i < n; i++) if (m(i)) out.push(a[i]);
    return out;
  };
  const b = pick(bis, (i) => valid[i]);
  const monS = b.length;
  const s = pick(sr, (i) => valid[i] && !Number.isNaN(sr[i]));
  const srS = s.length;
  const sqiS = count(Array.from(sqi), (x) => !Number.isNaN(x));
  const tsValid = Array.from(ts).filter((x) => !Number.isNaN(x));

  const inBand = count(b, (x) => x >= TARGET_LO && x <= TARGET_HI);
  const below40 = count(b, (x) => x < TARGET_LO), below30 = count(b, (x) => x < 30);
  const bMean = monS ? mean(b) : NaN;
  const bStd = monS ? std(b) : NaN;

  const row: Record<string, number | string> = {
    case: base,
    start: tsValid.length ? minOf(tsValid) : NaN,
    end: tsValid.length ? maxOf(tsValid) : NaN,
    rec_min: pyRound(n / 60, 1),
    monitored_min: pyRound(monS / 60, 1),
    min_sqi: minSqi === null ? NaN : minSqi,
    excluded_low_sqi_min: minSqi !== null ? pyRound(count(excluded.map(Number), (x) => x === 1) / 60, 1) : NaN,
    bis_median: monS ? npRound(median(b), 1) : NaN,
    bis_iqr_lo: monS ? npRound(quantile(b, 0.25), 1) : NaN,
    bis_iqr_hi: monS ? npRound(quantile(b, 0.75), 1) : NaN,
    bis_min: monS ? npRound(minOf(b), 1) : NaN,
    bis_max: monS ? npRound(maxOf(b), 1) : NaN,
    bis_sd: monS ? npRound(bStd, 1) : NaN,
    bis_cv: monS && bMean ? npRound(bStd / bMean, 3) : NaN,
    pct_in_40_60: pct(inBand, monS),
    pct_above_60: pct(count(b, (x) => x > TARGET_HI), monS),
    pct_below_40: pct(below40, monS),
    min_below_40: npRound(below40 / 60, 1),
    pct_below_30: pct(below30, monS),
    min_below_30: npRound(below30 / 60, 1),
    min_in_40_60: npRound(inBand / 60, 1),
    sr_mean: srS ? npRound(mean(s), 2) : NaN,
    pct_suppressed_sr_gt0: pct(count(s, (x) => x > 0), srS),
    min_suppressed_sr_gt0: srS ? npRound(count(s, (x) => x > 0) / 60, 1) : NaN,
    pct_deep_burst_sr_ge10: pct(count(s, (x) => x >= 10), srS),
    supp_min_equiv: srS ? npRound(npSum(s.map((x) => Math.max(x, 0))) / 100 / 60, 2) : NaN,
    emg_median: monS ? npRound(median(pick(emg, (i) => valid[i])), 1) : NaN,
    sqi_median: monS ? npRound(median(pick(sqi, (i) => valid[i])), 1) : NaN,
    pct_low_sqi_lt50: pct(count(Array.from(sqi), (x) => x < SQI_RELIABLE), sqiS),
  };
  const out = { case: base } as CohortRow;
  for (const [k, v] of Object.entries(row)) out[k] = typeof v === "number" ? nanToNull(v) : v;
  return out;
}
