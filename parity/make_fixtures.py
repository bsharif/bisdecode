"""Write parity fixtures + the Python decoder's output ("golden.json") for each.

    python parity/make_fixtures.py OUTDIR

Fixtures: the synthetic case from tests/conftest.py, plus edge cases that pin
the pandas behaviour the browser ports must copy. Synthetic data only.
"""
import argparse
import calendar
import hashlib
import importlib.util
import json
import math
import os
import struct
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from bisdecode import decode as D  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "bis_conftest", os.path.join(HERE, "..", "tests", "conftest.py"))
conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conftest)

EXTS = ["spa", "f_a", "e_a", "o_a", "t_a", "r2a"]


# ---------- encode pandas output as plain JSON ----------

def _num(x):
    if x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x)):
        return None
    x = float(x)
    if math.isinf(x):
        return "inf" if x > 0 else "-inf"
    return x


def _missing(v):
    return v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v))


def _ms(t):
    if pd.isna(t):
        return None
    return calendar.timegm(t.timetuple()) * 1000 + t.microsecond // 1000


def table(df):
    cols = []
    for c in df.columns:
        s = df[c]
        dt = str(s.dtype)
        if dt.startswith("datetime64"):
            cols.append({"name": c, "kind": "time", "values": [_ms(t) for t in s]})
        elif dt == "Int64":
            cols.append({"name": c, "kind": "int", "values": [_num(v) for v in s]})
        elif pd.api.types.is_numeric_dtype(s):
            cols.append({"name": c, "kind": "float", "values": [_num(v) for v in s]})
        else:
            cols.append({"name": c, "kind": "string",
                         "values": [None if _missing(v) else str(v) for v in s]})
    return {"nrows": len(df), "columns": cols}


def offset_table(rows):
    return {"nrows": len(rows), "columns": [
        {"name": "timestamp", "kind": "string", "values": [r["timestamp"] for r in rows]},
        {"name": "offset", "kind": "int", "values": [float(r["offset"]) for r in rows]},
        {"name": "size", "kind": "int", "values": [float(r["size"]) for r in rows]},
    ]}


def golden(dh, base):
    p = lambda ext: os.path.join(dh, f"{base}.{ext}")
    g = {"base": base, "dir": dh}
    g["spa"] = table(D.parse_spa(p("spa")))
    if os.path.exists(p("f_a")):
        g["spectra"] = table(D.parse_spectra(p("f_a")))
    if os.path.exists(p("e_a")):
        g["events"] = table(D.parse_events(p("e_a")))
    if os.path.exists(p("o_a")):
        g["offset"] = offset_table(D.parse_offset(p("o_a")))
    if os.path.exists(p("t_a")):
        g["start"] = D.parse_start(p("t_a"))
    if os.path.exists(p("r2a")):
        eeg = D.parse_eeg(p("r2a"))
        g["eeg"] = {"nSamples": int(eeg.shape[0]), "nChannels": int(eeg.shape[1]),
                    "sha256": hashlib.sha256(
                        np.ascontiguousarray(eeg, dtype="<i2").tobytes()).hexdigest()}
    return g


# ---------- fixtures ----------

def _w(path, text, nl="\r\n"):
    with open(path, "w", newline="") as f:
        f.write(nl.join(text) + nl)


def synth(root):
    case = os.path.join(root, "synth")
    dh = os.path.join(case, "DH01020800")
    os.makedirs(dh)
    p = lambda ext: os.path.join(dh, f"L01020800.{ext}")
    conftest._spa(p("spa")); conftest._fa(p("f_a")); conftest._ea(p("e_a"))
    conftest._ma(p("m_a")); conftest._oa(p("o_a"))
    with open(p("t_a"), "w", newline="") as f:
        f.write(conftest.START + "\r\n")
    with open(p("r2a"), "wb") as f:
        f.write(struct.pack("<512h", *[(i % 200 - 100) for i in range(512)]))
    return dh, "L01020800"


def edge(root):
    """Edge cases: several channels, ragged rows, text columns, odd hex, bad
    timestamps, CR-only line ends, wider/narrower spectra rows, odd EEG size."""
    dh = os.path.join(root, "edge", "DH09091200")
    os.makedirs(dh)
    p = lambda ext: os.path.join(dh, f"L09091200.{ext}")
    ch = ["S_HDR3", "", "", "Ch 1", "", "", "", "", "Ch 12", "", ""]
    nm = ["Time", "SpSmooth", "", "DB13U01", "ARTF2", "BISBIT00", "NOTE", "MIX",
          "DB13U01", "SR12", " SQI10 ", ""]
    rows = [
        "01/02/2030 08:00:00|3||45.5|0x80|+ff|abc|1|3276.8|-3277|nan|",
        "1/2/2030 8:0:1|3||-3276.8|zz|1_0|def|nan| 7 |inf|-inf|",
        "01/02/2030 08:00:60|3||-327.7||000e||2|-3276.00|1e3|5.|",
        "02/30/2030 08:00:00|3||12||-1|g|3",                     # ragged (short)
        " 01/02/2030 08:00:02|3||13|00000001|0110|h|4|5|6|7|8|9",   # long row
        "   ",
        "garbage",
    ]
    with open(p("spa"), "w", newline="") as f:
        f.write("\r".join(["|".join(ch), "|".join(nm)] + rows) + "\r")
    _w(p("f_a"), [
        "S_HDR3|BIS R2", "Time|Spectra",
        "01/02/2030 08:00:00|" + ",".join(str(i) for i in range(62)) + ",|",
        "01/02/2030 08:00:01|1,,x, 4 ,nan,-inf|",
        "no pipe here",
        "",
        "01/02/2030 08:00:02|1.5e-3,+.5,0001,5e400|extra|",
        "13/02/2030 08:00:03|7|",
    ], nl="\n")
    _w(p("e_a"), [
        "01/02/2030 08:00:01  >  ALARM     SQI LOW (ID 10010)",
        "  01/02/2030 08:00:02 > INFO no id here   ",
        "01/02/2030 08:00:03 > NOTE (ID 5)",
        "nonsense line",
        "01/02/2030 08:00:04 > X_1 msg with (ID 7) inside (ID 8)",
    ])
    _w(p("o_a"), ["# Time/Offset file", "Time\tOffset\tSize",
                  "01/02/2030 08:00:00 \t 0 \t 1024",
                  "01/02/2030 09:00:00\t1024\t2048 trailing",
                  "bad\t1\t2"])
    with open(p("t_a"), "w", newline="") as f:
        f.write("  01/02/2030 08:00:00 \n\n")
    with open(p("r2a"), "wb") as f:
        f.write(struct.pack("<3h", -32768, 32767, 5) + b"\x01")
    return dh, "L09091200"


def header_only(root):
    dh = os.path.join(root, "empty", "DH01010000")
    os.makedirs(dh)
    p = lambda ext: os.path.join(dh, f"L01010000.{ext}")
    _w(p("spa"), ["S_HDR3|Ch 1|", "Time|DB13U01|"], nl="\n")
    _w(p("f_a"), ["S_HDR3|BIS R2", "Time|Spectra"], nl="\n")
    _w(p("e_a"), ["nothing"])
    _w(p("o_a"), ["# Time/Offset file"])
    open(p("t_a"), "w").close()
    open(p("r2a"), "wb").close()
    return dh, "L01010000"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    a = ap.parse_args(argv)
    os.makedirs(a.outdir, exist_ok=True)
    cases = [synth(a.outdir), edge(a.outdir), header_only(a.outdir)]
    out = []
    for dh, base in cases:
        g = golden(dh, base)
        name = f"golden_{len(out):03d}.json"
        with open(os.path.join(a.outdir, name), "w") as f:
            json.dump(g, f)
        out.append(name)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
