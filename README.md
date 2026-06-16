# bisdecode

Decode **Aspect / Covidien / Medtronic BIS Advance / Vista** data-download files
into analysis-ready tables. The per-case `DH` export is a vendor-undocumented,
nine-file bundle; `bisdecode` reverse-engineers it into tidy CSV + NumPy.

> ⚠️ **Unofficial / reverse-engineered.** Not affiliated with or endorsed by the
> device manufacturer. Field mappings were validated against `BIS R2 4.1` /
> `BISx 1.15.2.22` exports cross-checked with the official BIS PDF reports.
> Verify against your own device revision before relying on it. Raw EEG is stored
> as ADC counts; µV scaling needs the (not-exported) BIS R2 gain constant.

## Install

```bash
pip install bisdecode
```

## CLI

```bash
# decode one case (folder containing a DH*/ subfolder, or the DH folder itself)
bisdecode decode path/to/M-XXXX-... -o decoded

# batch every case under a root
bisdecode decode raw-data --all -o decoded

# anonymised export: per-case date-shift, output named by Case ID, key kept secret
bisdecode decode raw-data --all --anonymise -o decoded_anon --key-file KEY.csv

# one-row-per-case cohort summary from decoded trends
bisdecode cohort decoded -o cohort_summary.csv
```

Per case you get: `*_trend.csv` (per-second BIS / SR / SEF / median freq / total
power / EMG / SQI / impedance / artifact / burst, per channel), `*_spectra.csv`
(60-bin density spectral array), `*_events.csv`, `*_messages.csv`, `*_meta.json`,
and `*_eeg.npy` (raw EEG, int16, 2ch @ 128 Hz).

## Python API

```python
import bisdecode
meta = bisdecode.decode_case("path/to/M-XXXX-...", "decoded")
trend = bisdecode.parse_spa("…/L…spa")          # -> pandas DataFrame
eeg   = bisdecode.parse_eeg("…/L….r2a")          # -> ndarray (n, 2) int16
row   = bisdecode.summarize("decoded/X/X_trend.csv", channel="Ch1")
```

## Anonymisation

`--anonymise` applies an independent random per-case offset (magnitude in
`[--shift-min-days, --shift-days]`, random sign) to **every** timestamp —
including those embedded in message text — so dates and clock-times move while
all within-case intervals are preserved. Outputs are named by the monitor Case
ID. The key file maps `source_base → offset_seconds`; keep it secret (it reverses
the shift: `real = shifted − offset`). Re-runs reuse the key, so offsets are stable.

## License

MIT. See `LICENSE`.
