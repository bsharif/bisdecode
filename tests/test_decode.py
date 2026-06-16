import json
import os

import numpy as np
import pandas as pd

import bisdecode
from bisdecode.cli import main as cli_main


def test_decode_case_outputs(synth_case, tmp_path):
    out = str(tmp_path / "decoded")
    meta = bisdecode.decode_case(synth_case, out, do_eeg=True)
    cd = os.path.join(out, "L01020800")

    for suffix in ("trend.csv", "spectra.csv", "events.csv", "messages.csv",
                   "meta.json", "eeg.npy"):
        assert os.path.exists(os.path.join(cd, f"L01020800_{suffix}"))

    assert meta["channels"] == ["Ch1"]
    assert meta["trend_rows"] == 4
    assert meta["eeg"]["shape"] == [256, 2]
    assert meta["eeg"]["duration_s"] == 2.0


def test_trend_bis_and_sentinels(synth_case, tmp_path):
    out = str(tmp_path / "decoded")
    bisdecode.decode_case(synth_case, out)
    t = pd.read_csv(os.path.join(out, "L01020800", "L01020800_trend.csv"))
    assert "Ch1:DB13U01" in t.columns                 # BIS index column
    bis = t["Ch1:DB13U01"]
    assert list(bis.dropna()) == [45.0, 38.0, 30.0]   # values preserved
    assert pd.isna(bis.iloc[3])                        # -3276.0 sentinel -> NaN


def test_eeg_shape_and_dtype(synth_case, tmp_path):
    out = str(tmp_path / "decoded")
    bisdecode.decode_case(synth_case, out)
    eeg = np.load(os.path.join(out, "L01020800", "L01020800_eeg.npy"))
    assert eeg.shape == (256, 2)
    assert eeg.dtype == np.int16


def test_spectra_60_bins(synth_case, tmp_path):
    out = str(tmp_path / "decoded")
    bisdecode.decode_case(synth_case, out)
    s = pd.read_csv(os.path.join(out, "L01020800", "L01020800_spectra.csv"))
    assert sum(c.startswith("bin") for c in s.columns) == 60


def test_events_parsed(synth_case, tmp_path):
    out = str(tmp_path / "decoded")
    bisdecode.decode_case(synth_case, out)
    e = pd.read_csv(os.path.join(out, "L01020800", "L01020800_events.csv"))
    assert len(e) == 2
    assert set(e["kind"]) == {"ALARM", "CLEAR"}


def test_cli_decode_and_cohort(synth_case, tmp_path):
    out = str(tmp_path / "decoded")
    assert cli_main(["decode", synth_case, "-o", out]) == 0
    summary = str(tmp_path / "cohort.csv")
    assert cli_main(["cohort", out, "-o", summary]) == 0
    c = pd.read_csv(summary)
    assert len(c) == 1
    assert c.loc[0, "bis_median"] == 38.0             # median of [45,38,30]
    assert c.loc[0, "pct_suppressed_sr_gt0"] > 0      # row C had SR=12
