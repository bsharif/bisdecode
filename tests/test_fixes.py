"""Regression tests for the 0.1.2 review fixes."""
import json
import os
import random
import shutil

import pandas as pd
import pytest

import bisdecode
import bisdecode.cli
from bisdecode.cohort import summarize
from bisdecode.decode import decode_case, main as decode_main, parse_spa, parse_spectra

BASE = "L01020800"


def _anon(case, tmp_path, **kw):
    out = str(tmp_path / "anon")
    key = {}
    meta = decode_case(case, out, anon=True, key=key, rng=random.Random(7),
                       used_names=set(), **kw)
    return out, key, meta


def _dh(case):
    return os.path.join(case, "DH01020800")


# ---------- decoding ----------

def test_spectra_last_bin_survives_trailing_pipe(synth_case):
    s = parse_spectra(os.path.join(_dh(synth_case), f"{BASE}.f_a"))
    bins = s.filter(like="bin")
    assert bins.shape[1] == 60
    assert bins.notna().all().all()                  # bin59 was NaN before 0.1.2
    assert s["bin59"].iloc[0] == 1059


def test_spectra_interior_blank_bin_keeps_position(tmp_path):
    f = tmp_path / "x.f_a"
    f.write_text("h\nh\n01/02/2030 08:00:00|1,,3|\n")
    s = parse_spectra(str(f))
    assert s["bin00"].iloc[0] == 1 and pd.isna(s["bin01"].iloc[0])
    assert s["bin02"].iloc[0] == 3


def test_hex_bitfields_decoded_as_hex(synth_case):
    t = parse_spa(os.path.join(_dh(synth_case), f"{BASE}.spa"))
    assert list(t["Ch1:ARTF2"]) == [0x80, 0, 0xA9, 0]
    assert list(t["Ch1:BISBIT00"]) == [0x110, 0xE, 0x110, 0x110]


def test_mostly_blank_column_is_numeric_and_trailing_column_dropped(synth_case):
    t = parse_spa(os.path.join(_dh(synth_case), f"{BASE}.spa"))
    imp = t["Ch1:C1POSIMP"]
    assert pd.api.types.is_float_dtype(imp)
    assert imp.iloc[2] == 1200 and imp.isna().sum() == 3
    assert not [c for c in t.columns if c.startswith("col")]


def test_header_only_files_decode_to_empty_tables(tmp_path):
    spa = tmp_path / "x.spa"
    spa.write_text("S_HDR3|Ch 1|\nTime|DB13U01|\n")
    fa = tmp_path / "x.f_a"
    fa.write_text("S_HDR3|BIS R2\nTime|Spectra\n")
    assert len(parse_spa(str(spa))) == 0
    s = parse_spectra(str(fa))
    assert len(s) == 0 and s.shape[1] == 61


# ---------- anonymisation ----------

def test_bare_date_in_message_is_shifted(synth_case, tmp_path):
    out, _, _ = _anon(synth_case, tmp_path)
    msgs = pd.read_csv(os.path.join(out, "SYNTH", "SYNTH_messages.csv"))
    expiry = [m for m in msgs["message"] if m.startswith("Sensor expiry")]
    assert expiry and "01/02/2030" not in expiry[0]


def test_serials_redacted_from_anon_output(synth_case, tmp_path):
    out, _, meta = _anon(synth_case, tmp_path)
    for suffix in ("meta.json", "messages.csv"):
        txt = open(os.path.join(out, "SYNTH", f"SYNTH_{suffix}")).read()
        assert "TEST123" not in txt, suffix
    assert meta["revisions"]["BISx serial number"] == "[redacted]"
    # the identifiable decode still keeps them
    plain = str(tmp_path / "plain")
    meta = decode_case(synth_case, plain)
    assert meta["revisions"]["BISx serial number"] == "TEST123"


def test_eeg_segment_times_shifted_not_dropped(synth_case, tmp_path):
    out, key, meta = _anon(synth_case, tmp_path)
    seg = meta["eeg"]["offset_index"][0]
    shifted = pd.Timestamp("2030-01-02 08:00:00") + pd.Timedelta(
        seconds=key[BASE]["offset_seconds"])
    assert seg["timestamp"] == shifted.strftime("%m/%d/%Y %H:%M:%S")


def test_no_case_id_gives_opaque_name(synth_case, tmp_path):
    # move the DH folder out of its M-<id>-<date> parent and drop the Case ID line
    dh = str(tmp_path / "loose" / "DH01020800")
    shutil.copytree(_dh(synth_case), dh)
    ma = os.path.join(dh, f"{BASE}.m_a")
    lines = [ln for ln in open(ma, newline="").read().split("\r\n") if "Case ID" not in ln]
    open(ma, "w", newline="").write("\r\n".join(lines))

    out, key, _ = _anon(dh, tmp_path)
    (name,) = os.listdir(out)
    assert name.startswith("case") and "0102" not in name
    assert key[BASE]["case_id"] == ""
    for root, _d, files in os.walk(out):
        assert not [f for f in files if BASE in f]


def test_decode_case_anon_without_key_or_names(synth_case, tmp_path):
    meta = decode_case(synth_case, str(tmp_path / "a"), anon=True)
    assert meta["anonymised"] is True


def test_shift_days_below_minimum_rejected(synth_case, tmp_path):
    with pytest.raises(SystemExit):
        decode_main([synth_case, "--anonymise", "--shift-days", "30",
                     "-o", str(tmp_path / "a"),
                     "--key-file", str(tmp_path / "k.csv")])


def test_key_file_written(synth_case, tmp_path):
    keyf = tmp_path / "k.csv"
    assert decode_main([synth_case, "--anonymise", "-o", str(tmp_path / "a"),
                        "--key-file", str(keyf), "--seed", "1"]) == 0
    assert keyf.exists() and not (tmp_path / "k.csv.tmp").exists()


def test_batch_exit_code_reports_failures(synth_case, tmp_path):
    root = tmp_path / "raw"
    shutil.copytree(synth_case, root / "M-SYNTH-01020800")
    bad = root / "M-BAD-01020800" / "DH01020800"
    bad.mkdir(parents=True)                          # DH folder without a .spa
    assert decode_main(["--all", str(root), "-o", str(tmp_path / "d")]) == 1


# ---------- cohort ----------

def _trend(tmp_path, **cols):
    d = tmp_path / "c"
    d.mkdir(exist_ok=True)
    n = len(next(iter(cols.values())))
    df = pd.DataFrame({"timestamp": pd.date_range("2030-01-02", periods=n, freq="s"),
                       **{f"Ch1:{k}": v for k, v in cols.items()}})
    f = d / "X_trend.csv"
    df.to_csv(f, index=False)
    return str(f)


def test_cohort_missing_sr_is_nan_not_zero(tmp_path):
    row = summarize(_trend(tmp_path, DB13U01=[45.0, 40.0, 35.0]), "Ch1")
    assert row["bis_median"] == 40.0
    for k in ("sr_mean", "pct_suppressed_sr_gt0", "min_suppressed_sr_gt0",
              "supp_min_equiv", "pct_low_sqi_lt50"):
        assert pd.isna(row[k]), k


def test_cohort_low_sqi_uses_seconds_with_sqi(tmp_path):
    nan = float("nan")
    row = summarize(_trend(tmp_path, DB13U01=[nan, nan, 45.0, 45.0],
                           SR12=[nan, nan, 0.0, 0.0],
                           SQI10=[nan, nan, 30.0, 90.0]), "Ch1")
    assert row["pct_low_sqi_lt50"] == 50.0           # 1 of 2 SQI seconds, not 1 of 4


def test_cohort_min_sqi_off_by_default(tmp_path):
    row = summarize(_trend(tmp_path, DB13U01=[45.0, 20.0], SR12=[0.0, 50.0],
                           SQI10=[90.0, 30.0]), "Ch1")
    assert row["monitored_min"] == round(2 / 60, 1)
    assert pd.isna(row["min_sqi"]) and pd.isna(row["excluded_low_sqi_min"])


def test_cohort_min_sqi_filters_unreliable_seconds(tmp_path):
    nan = float("nan")
    # 120 reliable seconds at BIS 45, 60 low-SQI seconds at BIS 20 with
    # suppression, 60 seconds with BIS but no SQI reading
    bis = [45.0] * 120 + [20.0] * 60 + [45.0] * 60
    sr = [0.0] * 120 + [50.0] * 60 + [0.0] * 60
    sqi = [90.0] * 120 + [30.0] * 60 + [nan] * 60
    f = _trend(tmp_path, DB13U01=bis, SR12=sr, SQI10=sqi)

    raw = summarize(f, "Ch1")
    assert raw["min_below_40"] == 1.0 and raw["supp_min_equiv"] == 0.5

    row = summarize(f, "Ch1", min_sqi=50)
    assert row["min_sqi"] == 50
    assert row["monitored_min"] == 2.0               # only the 120 reliable seconds
    assert row["excluded_low_sqi_min"] == 2.0        # 60 low + 60 without SQI
    assert row["bis_median"] == 45.0
    assert row["min_below_40"] == 0.0
    assert row["supp_min_equiv"] == 0.0
    assert row["pct_low_sqi_lt50"] == 33.3           # quality flag ignores the filter


def test_cli_cohort_min_sqi(synth_case, tmp_path):
    out = str(tmp_path / "decoded")
    decode_case(synth_case, out)
    summary = str(tmp_path / "c.csv")
    assert bisdecode.cli.main(["cohort", out, "-o", summary, "--min-sqi", "50"]) == 0
    c = pd.read_csv(summary)
    assert c.loc[0, "min_sqi"] == 50 and c.loc[0, "bis_median"] == 38.0
