"""Pytest fixtures: generate a tiny *synthetic* BIS case (no real patient data).

The synthetic case mirrors the real nine-file DH layout closely enough to
exercise every parser and the anonymiser, without shipping any clinical data.
"""
import os
import struct

import numpy as np
import pytest

CRLF = "\r\n"
START = "01/02/2030 08:00:00"

# .spa per-channel parameter names (one channel: Ch 1)
PARAMS = ["PIC_ID", "SR12", "SEF08", "MEDFRQ08", "BISBIT00", "DB13U01",
          "DB11U04", "B34U05", "TOTPOW08", "EMGLOW01", "SQI10", "IMPEDNCE",
          "ARTF2", "BURST", "ST"]
GLOBALS = ["SpSmooth", "BiSmooth", "LoFilter", "NotFiltr", "HiFilter"]

# four per-second rows; (BIS, SR, SQI, EMG) — last row is the NaN sentinel
ROWS = [
    {"BIS": "45.0", "SR12": "0.0", "SQI10": "95.0", "EMGLOW01": "30.0"},
    {"BIS": "38.0", "SR12": "0.0", "SQI10": "96.0", "EMGLOW01": "28.0"},
    {"BIS": "30.0", "SR12": "12.0", "SQI10": "90.0", "EMGLOW01": "25.0"},
    {"BIS": "-3276.0", "SR12": "-3276.8", "SQI10": "-327.7", "EMGLOW01": "-3276.0"},
]


def _times(n):
    h, m, s = 8, 0, 0
    out = []
    for i in range(n):
        out.append(f"01/02/2030 0{h}:{m:02d}:{(s + i):02d}")
    return out


def _spa(path):
    name_row = ["Time"] + GLOBALS + PARAMS
    ch_row = ["S_HDR3"] + [""] * len(GLOBALS) + ["Ch 1"] + [""] * (len(PARAMS) - 1)
    lines = ["|".join(ch_row), "|".join(name_row)]
    for t, r in zip(_times(len(ROWS)), ROWS):
        vals = [t] + ["3", "0", "3", "3", "2"]            # Time + globals
        vals += ["27", r["SR12"], "14.0", "4.0", "0110", r["BIS"],
                 "-3276.0", "-3276.0", "60.0", r["EMGLOW01"], r["SQI10"],
                 "11.0", "00000080", "-3276", "7.0"]
        lines.append("|".join(vals))
    _write(path, lines)


def _fa(path):
    lines = ["S_HDR3|BIS R2", "Time|Spectra"]
    for t in _times(len(ROWS)):
        bins = ",".join(str(1000 + i) for i in range(60))
        lines.append(f"{t}|{bins}")
    _write(path, lines)


def _ea(path):
    _write(path, [f"01/02/2030 08:00:01  >  ALARM     SQI LOW (ID 10010)",
                  f"01/02/2030 08:00:02  >  CLEAR     SQI LOW (ID 10010)"])


def _ma(path):
    _write(path, [
        f"{START} > Case ID: SYNTH",
        f"{START} > BISx serial number: TEST123",
        # embedded timestamp inside the message body (anonymiser must shift it):
        f"01/02/2030 08:00:02 > IMPEDNCE|01/02/2030 08:00:02|g   7000",
    ])


def _oa(path):
    _write(path, ["# Time/Offset file", "Time\tOffset\tSize",
                  f"{START} \t 0 \t 1024"])


def _write(path, lines):
    with open(path, "w", newline="") as f:
        f.write(CRLF.join(lines) + CRLF)


@pytest.fixture
def synth_case(tmp_path):
    """Create M-SYNTH-01020800/DH01020800/L01020800.* and return the case dir."""
    base = "L01020800"
    case = tmp_path / "M-SYNTH-01020800"
    dh = case / "DH01020800"
    dh.mkdir(parents=True)
    p = lambda ext: str(dh / f"{base}.{ext}")

    _spa(p("spa")); _fa(p("f_a")); _ea(p("e_a")); _ma(p("m_a")); _oa(p("o_a"))
    with open(p("t_a"), "w", newline="") as f:
        f.write(START + CRLF)
    # raw EEG: 512 int16 -> (256, 2)
    with open(p("r2a"), "wb") as f:
        f.write(struct.pack("<512h", *[(i % 200 - 100) for i in range(512)]))

    return str(case)
