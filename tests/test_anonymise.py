import os
import random

import pandas as pd

import bisdecode
from bisdecode.decode import decode_case


def _decode_both(synth_case, tmp_path):
    plain = str(tmp_path / "plain")
    anon = str(tmp_path / "anon")
    bisdecode.decode_case(synth_case, plain)
    key = {}
    decode_case(synth_case, anon, anon=True, key=key, rng=random.Random(42),
                used_names=set(), shift_days=1825, shift_min_days=180)
    return plain, anon, key


def test_output_named_by_case_id(synth_case, tmp_path):
    _, anon, _ = _decode_both(synth_case, tmp_path)
    assert os.path.isdir(os.path.join(anon, "SYNTH"))      # Case ID, not L-base
    assert not os.path.exists(os.path.join(anon, "L01020800"))


def test_no_original_date_anywhere(synth_case, tmp_path):
    _, anon, _ = _decode_both(synth_case, tmp_path)
    leaked = []
    for root, _d, files in os.walk(anon):
        for fn in files:
            if fn.endswith((".csv", ".json")):
                txt = open(os.path.join(root, fn), encoding="utf-8").read()
                if "01/02/2030" in txt or "2030-01-02" in txt:
                    leaked.append(fn)
    assert leaked == [], f"original date leaked in: {leaked}"


def test_embedded_timestamp_shifted(synth_case, tmp_path):
    _, anon, _ = _decode_both(synth_case, tmp_path)
    msgs = open(os.path.join(anon, "SYNTH", "SYNTH_messages.csv")).read()
    assert "IMPEDNCE" in msgs                               # row still present
    assert "01/02/2030" not in msgs                         # embedded ts shifted


def test_intervals_and_values_preserved(synth_case, tmp_path):
    plain, anon, _ = _decode_both(synth_case, tmp_path)
    p = pd.read_csv(os.path.join(plain, "L01020800", "L01020800_trend.csv"),
                    parse_dates=["timestamp"])
    a = pd.read_csv(os.path.join(anon, "SYNTH", "SYNTH_trend.csv"),
                    parse_dates=["timestamp"])
    assert p["timestamp"].diff().dropna().equals(a["timestamp"].diff().dropna())
    assert p["Ch1:DB13U01"].fillna(-1).equals(a["Ch1:DB13U01"].fillna(-1))


def test_reversible_with_key(synth_case, tmp_path):
    plain, anon, key = _decode_both(synth_case, tmp_path)
    off = pd.Timedelta(seconds=key["L01020800"]["offset_seconds"])
    p = pd.read_csv(os.path.join(plain, "L01020800", "L01020800_trend.csv"),
                    parse_dates=["timestamp"])
    a = pd.read_csv(os.path.join(anon, "SYNTH", "SYNTH_trend.csv"),
                    parse_dates=["timestamp"])
    assert (a["timestamp"] - off).equals(p["timestamp"])


def test_min_shift_enforced(synth_case, tmp_path):
    _, _, key = _decode_both(synth_case, tmp_path)
    off_days = abs(key["L01020800"]["offset_seconds"]) / 86400
    assert off_days >= 180
