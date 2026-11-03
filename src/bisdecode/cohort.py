#!/usr/bin/env python3
"""
cohort_summary.py — Build a one-row-per-case cohort table from decoded BIS trends.

Reads every `*_trend.csv` produced by decode_bis.py and computes case-level
depth-of-anaesthesia metrics: recording/monitored time, BIS distribution, time
in/below/above the 40-60 target band, suppression burden, EMG, and signal
quality. Each trend row is 1 second, so counts convert directly to minutes.

With --min-sqi N, a second counts only if its SQI >= N (seconds with no SQI are
excluded too). Every BIS, SR and EMG metric then uses only those seconds;
`monitored_min` becomes "monitored with reliable signal", and the removed time
is reported as `excluded_low_sqi_min`. Off by default.

Usage:
    python3 cohort_summary.py decoded -o decoded/cohort_summary.csv [--channel Ch1]
        [--min-sqi 50]
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

TARGET_LO, TARGET_HI = 40, 60          # usual general-anaesthesia BIS target band
SQI_RELIABLE = 50                       # SQI >= 50 considered reliable


def pct(n, d):
    return round(100.0 * n / d, 1) if d else np.nan


def summarize(path, channel, min_sqi=None):
    df = pd.read_csv(path, parse_dates=["timestamp"])
    base = os.path.basename(path).replace("_trend.csv", "")

    def col(param):
        c = f"{channel}:{param}"
        # a missing parameter is all-NaN (not an empty Series that breaks masks)
        return df[c] if c in df.columns else pd.Series(np.nan, index=df.index)

    bis, sr, emg, sqi = col("DB13U01"), col("SR12"), col("EMGLOW01"), col("SQI10")

    rec_s = len(df)                                  # total rows = total seconds
    valid = bis.notna()                              # sensor connected / BIS present
    excluded = pd.Series(False, index=df.index)
    if min_sqi is not None:                          # drop unreliable-signal seconds
        reliable = sqi >= min_sqi                    # NaN SQI -> False (excluded)
        excluded = valid & ~reliable
        valid = valid & reliable
    mon_s = int(valid.sum())                         # monitored seconds
    b = bis[valid]
    sr_ok = valid & sr.notna()                       # SR measured while monitored
    sr_s = int(sr_ok.sum())
    s = sr[sr_ok]
    sqi_s = int(sqi.notna().sum())                   # seconds with an SQI reading

    row = {
        "case": base,
        "start": df["timestamp"].min(),
        "end": df["timestamp"].max(),
        "rec_min": round(rec_s / 60, 1),
        "monitored_min": round(mon_s / 60, 1),
        "min_sqi": np.nan if min_sqi is None else min_sqi,
        "excluded_low_sqi_min": round(int(excluded.sum()) / 60, 1) if min_sqi is not None else np.nan,
        "bis_median": round(b.median(), 1) if mon_s else np.nan,
        "bis_iqr_lo": round(b.quantile(.25), 1) if mon_s else np.nan,
        "bis_iqr_hi": round(b.quantile(.75), 1) if mon_s else np.nan,
        "bis_min": round(b.min(), 1) if mon_s else np.nan,
        "bis_max": round(b.max(), 1) if mon_s else np.nan,
        # --- BIS variability (lability has been linked to delirium) ---
        "bis_sd": round(b.std(), 1) if mon_s else np.nan,
        "bis_cv": round(b.std() / b.mean(), 3) if mon_s and b.mean() else np.nan,
        # --- time relative to the 40-60 target band (% of monitored time) ---
        "pct_in_40_60": pct(((b >= TARGET_LO) & (b <= TARGET_HI)).sum(), mon_s),
        "pct_above_60": pct((b > TARGET_HI).sum(), mon_s),
        # --- deep-hypnosis exposure (the headline delirium/POCD exposure vars) ---
        "pct_below_40": pct((b < TARGET_LO).sum(), mon_s),
        "min_below_40": round((b < TARGET_LO).sum() / 60, 1),
        "pct_below_30": pct((b < 30).sum(), mon_s),
        "min_below_30": round((b < 30).sum() / 60, 1),
        "min_in_40_60": round(((b >= TARGET_LO) & (b <= TARGET_HI)).sum() / 60, 1),
        # --- suppression burden ---
        # (NaN, not 0, when SR was never measured)
        "sr_mean": round(s.mean(), 2) if sr_s else np.nan,
        "pct_suppressed_sr_gt0": pct((s > 0).sum(), sr_s),
        "min_suppressed_sr_gt0": round((s > 0).sum() / 60, 1) if sr_s else np.nan,
        "pct_deep_burst_sr_ge10": pct((s >= 10).sum(), sr_s),
        # time-integrated suppression: equivalent minutes of full (100%) suppression
        # = AUC of SR(t) over the case. The strongest single EEG delirium predictor.
        "supp_min_equiv": round(s.clip(lower=0).sum() / 100 / 60, 2) if sr_s else np.nan,
        # EMG + signal quality
        "emg_median": round(emg[valid].median(), 1) if mon_s else np.nan,
        "sqi_median": round(sqi[valid].median(), 1) if mon_s else np.nan,
        # % of seconds WITH an SQI reading (sensor-off seconds are not "good")
        "pct_low_sqi_lt50": pct((sqi < SQI_RELIABLE).sum(), sqi_s),
    }
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bisdecode cohort", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("decoded_dir", help="directory containing <base>/<base>_trend.csv")
    ap.add_argument("-o", "--out", default="cohort_summary.csv")
    ap.add_argument("--channel", default="Ch1", help="trend channel (default Ch1)")
    ap.add_argument("--min-sqi", type=float, default=None, metavar="N",
                    help="use only seconds with SQI >= N for BIS/SR/EMG metrics "
                         "(e.g. 50); default: no SQI filter")
    args = ap.parse_args(argv)

    trends = sorted(glob.glob(os.path.join(args.decoded_dir, "*", "*_trend.csv")))
    if not trends:
        raise SystemExit(f"no *_trend.csv found under {args.decoded_dir}")

    rows, failed = [], 0
    for t in trends:
        try:
            rows.append(summarize(t, args.channel, args.min_sqi))
        except Exception as e:
            failed += 1
            print(f"[FAIL] {t}: {e}", file=sys.stderr)
    if not rows:
        raise SystemExit("no case could be summarised")
    coh = pd.DataFrame(rows).sort_values("case").reset_index(drop=True)
    coh.to_csv(args.out, index=False)

    # console: cohort-level overview
    print(f"{len(coh)} cases -> {args.out}\n")
    pd.set_option("display.width", 200, "display.max_columns", 30)
    print(coh[["case", "monitored_min", "bis_median", "pct_in_40_60",
               "pct_below_40", "pct_suppressed_sr_gt0", "sqi_median",
               "pct_low_sqi_lt50"]].to_string(index=False))
    print("\n=== cohort distribution (median [IQR]) ===")
    for c in ["monitored_min", "bis_median", "bis_sd", "pct_in_40_60",
              "pct_below_40", "min_below_40", "pct_below_30", "min_below_30",
              "supp_min_equiv", "pct_suppressed_sr_gt0", "emg_median",
              "sqi_median", "pct_low_sqi_lt50"]:
        s = coh[c].dropna()
        print(f"  {c:24s} {s.median():7.1f}  [{s.quantile(.25):.1f} - {s.quantile(.75):.1f}]")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
