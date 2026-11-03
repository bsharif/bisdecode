#!/usr/bin/env python3
"""
decode_bis.py — Decoder for Aspect/Covidien/Medtronic BIS data-download files.

A BIS Advance / Vista "data download" produces, per case, a `DH<date>` folder
holding nine files that share a base name `L<date>`:

    .t_a   case start timestamp (text, 1 line)
    .o_a   time/offset index into the raw-EEG file .r2a (text)
    .e_a   event / alarm log (text)            -> timestamp, kind, message, id
    .m_a   monitor messages + impedance log (text)
    .h_a   binary header (2048 B, module/revision metadata)
    .spa   PER-SECOND PROCESSED TREND (text, pipe-delimited)  <-- BIS, SR, SEF,
           MEDFREQ, total power, EMG, SQI, impedance, artifact, burst, per channel
    .f_a   PER-SECOND DENSITY SPECTRAL ARRAY (text) — 60 power bins/row (spectrogram)
    .ara   binary auxiliary array (artifact/asymmetry; not fully reverse-engineered)
    .r2a   RAW EEG waveform (binary, int16 LE, 2 channels interleaved @ 128 Hz)

Verified field map in .spa (per channel block), cross-checked against the BIS PDF:
    SR12     = Suppression Ratio (%)        SEF08   = Spectral Edge Freq (Hz)
    MEDFRQ08 = Median Frequency (Hz)        DB13U01 = BIS INDEX (the headline number)
    TOTPOW08 = Total Power (dB)             EMGLOW01= EMG (dB)
    SQI10    = Signal Quality Index (%)     IMPEDNCE= impedance
    ARTF2    = artifact bitmask (hex)       BURST   = burst count    ST = ?
    BISBIT00 = BIS status bitfield (hex)
Hex bitfields (ARTF2, BISBIT00) are decoded to their integer value (0x80 -> 128).

Sentinel "no data" values (~ -3276.8, -3276.0, -327.7, -3276) are mapped to NaN.

Usage:
    python3 decode_bis.py <case-or-DH-folder> [-o OUTDIR] [--no-eeg]
    python3 decode_bis.py --all raw-data -o decoded             # batch all cases
    python3 decode_bis.py --all raw-data --anonymise \
        -o decoded_anon --key-file anonymisation_key.csv       # date-shifted export

Anonymise: per-case random date-shift (±--shift-days, default 5y) applied to all
timestamps so dates/clock-times move but within-case intervals are preserved.
Outputs are named by Case ID; the (secret, gitignored) key file maps
source_base -> offset_seconds for re-linking — reverse with: real = shifted - offset.

Outputs (per case, under OUTDIR/<base>/):
    <base>_trend.csv      tidy per-second trend, all channels (wide)
    <base>_spectra.csv    per-second DSA spectrogram (time + 60 bins)
    <base>_events.csv     alarms / info events
    <base>_messages.csv   monitor messages + impedance
    <base>_meta.json      start time, channels, sample rate, r2a layout, revisions
    <base>_eeg.npy        raw EEG, shape (n_samples, n_channels), int16  (unless --no-eeg)
"""
import argparse
import csv
import glob
import json
import os
import random
import re
import sys

import numpy as np
import pandas as pd

# Sentinel values used by the monitor to mean "no valid measurement".
SENTINELS = {-3276.8, -3276.0, -327.7, 3276.8}
HEX_PARAMS = {"ARTF2", "BISBIT00"}   # hex bitfields in .spa -> decoded to int
SPECTRA_BINS = 60
# device serial numbers identify the site: redacted in anonymised output
_SERIAL_RE = re.compile(r"(serial number\s*:\s*)\S.*$", re.IGNORECASE)
EEG_SAMPLE_RATE = 128          # Hz, per channel (BIS R2 raw EEG)
EEG_BYTES_PER_SEC = 512        # observed: 2 ch * 128 Hz * 2 bytes


# ---------- anonymisation (per-case date-shift) ----------
def derive_case_id(dh, msgs_df):
    """Non-temporal case pseudonym: the monitor's 'Case ID' (e.g. AV9R)."""
    for m in msgs_df.get("message", []):
        if isinstance(m, str) and m.strip().startswith("Case ID:"):
            cid = m.split(":", 1)[1].strip()
            if cid:
                return cid
    for cand in (os.path.basename(os.path.dirname(dh)), os.path.basename(dh)):
        parts = cand.split("-")
        if len(parts) >= 2 and parts[0] == "M" and not parts[1].isdigit():
            return parts[1]
    return None


def load_key(path):
    """Load the date-shift key: source_base -> {case_id, anon_name, offset}."""
    key = {}
    if path and os.path.exists(path):
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                key[row["source_base"]] = {
                    "case_id": row["case_id"], "anon_name": row["anon_name"],
                    "offset_seconds": int(row["offset_seconds"])}
    return key


def save_key(path, key):
    """Write the key atomically: a crash mid-write must not corrupt the secret."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_base", "case_id", "anon_name", "offset_seconds"])
        for base, v in sorted(key.items()):
            w.writerow([base, v["case_id"], v["anon_name"], v["offset_seconds"]])
    os.replace(tmp, path)


_EMBED_TS_RE = re.compile(r"\d{2}/\d{2}/\d{4}(?: \d{2}:\d{2}:\d{2})?")


def shift_embedded(value, shift):
    """Rewrite 'MM/DD/YYYY[ HH:MM:SS]' substrings inside a string by `shift`.

    A bare date (no clock time) is shifted as midnight and written back as a date.
    """
    if not isinstance(value, str):
        return value
    def repl(m):
        txt = m.group(0)
        fmt = "%m/%d/%Y %H:%M:%S" if " " in txt else "%m/%d/%Y"
        ts = pd.to_datetime(txt, format=fmt, errors="coerce")
        return txt if pd.isna(ts) else (ts + shift).strftime(fmt)
    return _EMBED_TS_RE.sub(repl, value)


def redact_serials(value):
    """'BISx serial number: ABC123' -> 'BISx serial number: [redacted]'."""
    return _SERIAL_RE.sub(r"\1[redacted]", value) if isinstance(value, str) else value


def assign_shift(key, base, case_id, rng, shift_days, used_names, shift_min_days=180):
    """Return (anon_name, offset_seconds), reusing the key if the case is known.

    The offset magnitude is drawn in [shift_min_days, shift_days] with a random
    sign (plus a random within-day component), so every case is displaced by at
    least shift_min_days — avoiding near-zero "shifts" that barely anonymise.
    """
    if base in key:
        used_names.add(key[base]["anon_name"])
        return key[base]["anon_name"], key[base]["offset_seconds"]
    # no Case ID: an opaque pseudonym, never the date-encoded base (L<MMDDhhmm>)
    name = case_id or f"case{rng.randrange(16**6):06x}"
    n, stem = name, name
    while name in used_names:                       # de-collide pseudonyms
        n = (n if isinstance(n, int) else 1) + 1
        name = f"{stem}_{n}"
    used_names.add(name)
    lo = min(shift_min_days, shift_days)
    mag = rng.randint(lo, shift_days) * 86400 + rng.randint(0, 86399)
    offset = mag if rng.random() < 0.5 else -mag
    key[base] = {"case_id": case_id or "", "anon_name": name, "offset_seconds": offset}
    return name, offset


def find_base(dh_dir):
    """Return the shared base name (e.g. 'L05220753') inside a DH folder."""
    spa = glob.glob(os.path.join(dh_dir, "*.spa"))
    if not spa:
        raise FileNotFoundError(f"no .spa file in {dh_dir}")
    return os.path.splitext(os.path.basename(spa[0]))[0]


def locate_dh(path):
    """Accept either a case folder (M-...) or a DH folder; return the DH folder."""
    if glob.glob(os.path.join(path, "*.spa")):
        return path
    dh = glob.glob(os.path.join(path, "DH*"))
    if dh and glob.glob(os.path.join(dh[0], "*.spa")):
        return dh[0]
    raise FileNotFoundError(f"could not find a DH/*.spa folder under {path}")


def _hex_to_int(s):
    """Decode a hex-bitfield column ('00000080' -> 128); blank/invalid -> <NA>."""
    def conv(v):
        try:
            return int(v, 16) if v else None
        except ValueError:
            return None
    return pd.array([conv(v) for v in s], dtype="Int64")


def _nan_sentinels(s):
    """Map known sentinel values to NaN on a numeric Series."""
    return s.mask(s.isin(SENTINELS) | (s <= -3276.0))


def parse_spa(path):
    """Parse the per-second processed-trend file into a wide DataFrame.

    Header-driven: row 1 carries 'Ch N' labels (forward-filled across the block),
    row 2 carries parameter names. Columns become '<chan>:<param>'.
    """
    with open(path, "r", newline="") as f:
        lines = f.read().splitlines()
    ch_row = lines[0].split("|")
    name_row = lines[1].split("|")

    # forward-fill channel labels across each channel's block
    cols, cur_ch = [], None
    for i, name in enumerate(name_row):
        ch = ch_row[i].strip() if i < len(ch_row) else ""
        if ch.startswith("Ch"):
            cur_ch = ch.replace(" ", "")          # 'Ch 1' -> 'Ch1'
        nm = name.strip()
        if i == 0:
            cols.append("timestamp")
        elif cur_ch and nm not in ("",):
            cols.append(f"{cur_ch}:{nm}")
        else:
            cols.append(nm or f"col{i}")

    rows = [ln.split("|")[: len(cols)] for ln in lines[2:] if ln.strip()]
    df = pd.DataFrame(rows, columns=cols).fillna("")
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="%m/%d/%Y %H:%M:%S",
                                     errors="coerce")
    for c in list(df.columns):
        if c == "timestamp":
            continue
        txt = df[c].astype(str).str.strip()
        blank = txt == ""
        if c.startswith("col") and blank.all():
            df = df.drop(columns=c)              # empty field after a trailing '|'
            continue
        if c.split(":")[-1] in HEX_PARAMS:       # hex bitfields: '00000080' -> 128
            df[c] = _hex_to_int(txt)
            continue
        num = pd.to_numeric(txt, errors="coerce")
        # numeric if every non-blank cell parses (mostly-blank impedance columns too)
        if num[~blank].notna().all():
            df[c] = _nan_sentinels(num.astype(float))
        else:
            df[c] = txt
    return df


def parse_spectra(path):
    """Parse the .f_a density-spectral-array file: time + 60 power bins per row."""
    with open(path, "r", newline="") as f:
        lines = f.read().splitlines()
    data = []
    for ln in lines[2:]:
        if not ln.strip() or "|" not in ln:
            continue
        # row is 'ts|b0,...,b59|' — the trailing '|' must not glue onto the last bin
        fields = ln.split("|")
        bins = fields[1].split(",")
        if bins[-1].strip() == "":
            bins = bins[:-1]                     # drop a trailing comma only
        data.append([fields[0]] + bins)
    n_bins = max([len(r) - 1 for r in data] + [SPECTRA_BINS])
    cols = ["timestamp"] + [f"bin{i:02d}" for i in range(n_bins)]
    df = pd.DataFrame([r + [None] * (n_bins + 1 - len(r)) for r in data], columns=cols)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="%m/%d/%Y %H:%M:%S",
                                     errors="coerce")
    for c in cols[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


EVENT_RE = re.compile(
    r"^(?P<ts>\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})\s+>\s+"
    r"(?P<kind>\w+)\s+(?P<msg>.*?)(?:\s+\(ID (?P<id>\d+)\))?\s*$")


def parse_events(path):
    rows = []
    with open(path, "r", newline="") as f:
        for ln in f.read().splitlines():
            m = EVENT_RE.match(ln.strip())
            if m:
                rows.append(m.groupdict())
    df = pd.DataFrame(rows, columns=["ts", "kind", "msg", "id"])
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], format="%m/%d/%Y %H:%M:%S", errors="coerce")
    return df.rename(columns={"ts": "timestamp"})


def parse_messages(path):
    """Parse .m_a: free-text monitor messages incl. revision info and impedance log."""
    rows = []
    with open(path, "r", newline="") as f:
        for ln in f.read().splitlines():
            ln = ln.rstrip("\r")
            m = re.match(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}) > (.*)$", ln)
            if m:
                rows.append({"timestamp": m.group(1), "message": m.group(2)})
    df = pd.DataFrame(rows, columns=["timestamp", "message"])
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], format="%m/%d/%Y %H:%M:%S",
                                         errors="coerce")
    return df


def parse_revisions(msg_df):
    """Pull module/revision/serial metadata out of the early .m_a messages."""
    rev = {}
    for msg in msg_df["message"].head(40):
        if ":" in msg and "IMPEDNCE" not in msg:
            k, _, v = msg.partition(":")
            rev[k.strip()] = v.strip()
    return rev


def parse_offset(path):
    """Parse .o_a — the raw-EEG (.r2a) time/offset index."""
    with open(path, "r", newline="") as f:
        lines = [ln.rstrip("\r") for ln in f.read().splitlines()]
    rows = []
    for ln in lines:
        m = re.match(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})\s*\t\s*(\d+)\s*\t\s*(\d+)", ln)
        if m:
            rows.append({"timestamp": m.group(1), "offset": int(m.group(2)),
                         "size": int(m.group(3))})
    return rows


def parse_start(path):
    with open(path, "r", newline="") as f:
        return f.read().strip()


def parse_eeg(path, n_channels=2):
    """Read raw EEG: int16 little-endian, n_channels interleaved.

    Returns array shape (n_samples_per_channel, n_channels).
    """
    raw = np.fromfile(path, dtype="<i2")
    n = (raw.size // n_channels) * n_channels
    return raw[:n].reshape(-1, n_channels)


def decode_case(path, outdir, do_eeg=True, anon=False, key=None, rng=None,
                shift_days=1825, used_names=None, shift_min_days=180):
    dh = locate_dh(path)
    base = find_base(dh)
    p = lambda ext: os.path.join(dh, f"{base}.{ext}")

    # parse messages early — needed for the case pseudonym
    msgs = parse_messages(p("m_a"))

    if anon:
        key = {} if key is None else key
        if used_names is None:
            used_names = {v["anon_name"] for v in key.values()}
        rng = rng or random.SystemRandom()
        case_id = derive_case_id(dh, msgs)
        name, offset_s = assign_shift(key, base, case_id, rng, shift_days,
                                      used_names, shift_min_days)
        shift = pd.Timedelta(seconds=offset_s)
        out_name = name
        meta = {"base": name, "anonymised": True}      # NB: no real base / source path
    else:
        shift = pd.Timedelta(0)
        out_name = base
        meta = {"base": base, "source": dh}

    case_out = os.path.join(outdir, out_name)
    os.makedirs(case_out, exist_ok=True)
    op = lambda suffix: os.path.join(case_out, f"{out_name}_{suffix}")

    def shifted(df, col="timestamp"):
        if not anon:
            return df
        if col in df.columns:
            df[col] = df[col] + shift
        # also rewrite timestamps embedded INSIDE text fields (e.g. the
        # IMPEDNCE messages: "IMPEDNCE|05/22/2026 08:10:15|g NOISE").
        # Match object AND string dtypes — pandas >=3 stores strings as a
        # dedicated dtype, not object, so an `== object` check would miss them
        # and silently leak the embedded date.
        for c in df.columns:
            if c != col and (df[c].dtype == object or
                             str(df[c].dtype) in ("string", "str")):
                df[c] = df[c].map(lambda v: shift_embedded(v, shift))
        return df

    meta["case_start"] = shift_start(p("t_a"), shift) if os.path.exists(p("t_a")) else None

    # processed trend (the main research table)
    trend = shifted(parse_spa(p("spa")))
    trend.to_csv(op("trend.csv"), index=False)
    meta["trend_rows"] = len(trend)
    meta["trend_columns"] = list(trend.columns)
    meta["channels"] = sorted({c.split(":")[0] for c in trend.columns if ":" in c})

    # spectrogram
    spec = shifted(parse_spectra(p("f_a")))
    spec.to_csv(op("spectra.csv"), index=False)
    meta["spectra_bins"] = spec.shape[1] - 1

    # events + messages
    shifted(parse_events(p("e_a"))).to_csv(op("events.csv"), index=False)
    msgs = shifted(msgs)
    if anon:
        msgs["message"] = msgs["message"].map(redact_serials)
    msgs.to_csv(op("messages.csv"), index=False)
    meta["revisions"] = parse_revisions(msgs)

    # raw EEG (no absolute time inside); shift the segment times to keep alignment
    oidx = parse_offset(p("o_a"))
    if anon:
        for r in oidx:
            r["timestamp"] = shift_embedded(r["timestamp"], shift)
    meta["eeg"] = {"sample_rate_hz": EEG_SAMPLE_RATE, "n_channels": 2,
                   "dtype": "int16_le", "offset_index": oidx}
    if do_eeg and os.path.exists(p("r2a")):
        eeg = parse_eeg(p("r2a"))
        np.save(op("eeg.npy"), eeg)
        meta["eeg"]["shape"] = list(eeg.shape)
        meta["eeg"]["duration_s"] = round(eeg.shape[0] / EEG_SAMPLE_RATE, 1)

    with open(op("meta.json"), "w") as f:
        json.dump(meta, f, indent=2, default=str)

    # quick console summary of the BIS index if present
    bis_cols = [c for c in trend.columns if c.endswith("DB13U01")]
    summ = ""
    if bis_cols:
        b = trend[bis_cols[0]].dropna()
        if len(b):
            summ = f"  BIS({bis_cols[0]}): median={b.median():.0f} range={b.min():.0f}-{b.max():.0f}"
    print(f"[ok] {out_name}: {len(trend)} trend rows, "
          f"{meta.get('eeg',{}).get('duration_s','?')}s EEG{summ}")
    return meta


def shift_start(t_a_path, shift):
    """Read .t_a start time and apply the anonymisation shift (NaT-safe)."""
    raw = parse_start(t_a_path)
    if shift == pd.Timedelta(0):
        return raw
    ts = pd.to_datetime(raw, format="%m/%d/%Y %H:%M:%S", errors="coerce")
    return None if pd.isna(ts) else (ts + shift).strftime("%m/%d/%Y %H:%M:%S")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bisdecode decode", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="case folder, DH folder, or (with --all) the raw-data root")
    ap.add_argument("-o", "--outdir", default="decoded", help="output directory")
    ap.add_argument("--all", action="store_true", help="batch-process every case under PATH")
    ap.add_argument("--no-eeg", action="store_true", help="skip raw EEG (.r2a) extraction")
    ap.add_argument("--anonymise", action="store_true",
                    help="date-shift all timestamps per-case and name outputs by Case ID")
    ap.add_argument("--key-file", default="anonymisation_key.csv",
                    help="path to the (secret) date-shift key; reused if it exists")
    ap.add_argument("--shift-days", type=int, default=1825,
                    help="max +/- shift magnitude in days (default 1825 = +/-5y)")
    ap.add_argument("--shift-min-days", type=int, default=180,
                    help="minimum shift magnitude in days (default 180)")
    ap.add_argument("--seed", type=int, default=None,
                    help="seed offsets for reproducible runs (default: system entropy)")
    args = ap.parse_args(argv)
    if args.anonymise and args.shift_days < args.shift_min_days:
        ap.error("--shift-days must be >= --shift-min-days")

    key, rng, used = None, None, None
    if args.anonymise:
        key = load_key(args.key_file)
        used = {v["anon_name"] for v in key.values()}
        rng = random.Random(args.seed) if args.seed is not None else random.SystemRandom()
        if os.path.normpath(args.outdir) == "decoded":   # don't clobber the identifiable copy
            args.outdir = "decoded_anon"
        print(f"ANONYMISE: date-shifting per case; key -> {args.key_file} "
              f"(KEEP SECRET); output -> {args.outdir}/")

    kw = dict(do_eeg=not args.no_eeg, anon=args.anonymise, key=key, rng=rng,
              shift_days=args.shift_days, used_names=used,
              shift_min_days=args.shift_min_days)

    if args.all:
        cases = sorted(d for d in glob.glob(os.path.join(args.path, "*"))
                       if os.path.isdir(d) and glob.glob(os.path.join(d, "DH*")))
        print(f"found {len(cases)} cases")
        ok = 0
        for c in cases:
            try:
                decode_case(c, args.outdir, **kw)
                ok += 1
                if args.anonymise:
                    save_key(args.key_file, key)        # persist after each case
            except Exception as e:
                print(f"[FAIL] {os.path.basename(c)}: {e}", file=sys.stderr)
        print(f"done: {ok}/{len(cases)} cases decoded into {args.outdir}/")
        return 0 if cases and ok == len(cases) else 1
    decode_case(args.path, args.outdir, **kw)
    if args.anonymise:
        save_key(args.key_file, key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
