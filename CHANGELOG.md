# Changelog

## 0.1.2
Fixes from a full-code review, checked against 100 real cases.

**Decoding (changes output values — re-decode existing data):**
- **Spectra:** the last bin (`bin59`) was NaN in every row. Real `.f_a` rows end
  with `|`, which was glued onto the last value. Interior blank bins now keep
  their position instead of shifting later bins down.
- **Hex bitfields:** `ARTF2` and `BISBIT00` were read as decimal (`00000080` → 80)
  and values with `a`–`f` became NaN. They are now decoded as hex to their integer
  value (`00000080` → 128, `000000a9` → 169).
- Mostly-blank numeric columns (e.g. the `C?POSIMP` / `GNDIMP` impedances) are now
  numeric instead of text. The empty column from the trailing `|` (`col54`) is dropped.
- Header-only `.spa` / `.f_a` files decode to empty tables instead of crashing.

**Anonymisation:**
- A case with no Case ID got the date-encoded base (`L<MMDDhhmm>`) as its name;
  it now gets an opaque `case<hex>` pseudonym. A digits-only folder token is no
  longer taken as a Case ID.
- Dates with no clock time (`MM/DD/YYYY`) inside text are now shifted too.
- Device serial numbers are redacted (`[redacted]`) in the anonymised
  `messages.csv` and `meta.json`.
- `.o_a` EEG segment times are now shifted instead of deleted, so EEG stays
  alignable with the trend.
- The key file is written atomically. `decode_case(anon=True)` works without
  `key` / `used_names` / `rng`. `--shift-days` below `--shift-min-days` is an error.
  `-o ./decoded` no longer bypasses the guard that protects the identifiable copy.

**Cohort:**
- Suppression metrics are NaN (not 0) when SR was never measured, and use
  seconds with a valid SR as the denominator. A missing SR/EMG/SQI column no
  longer crashes the case.
- `pct_low_sqi_lt50` now uses seconds with an SQI reading as its denominator
  (it used all rows, so sensor-off time diluted it).

**CLI:** `decode --all` and `cohort` return exit code 1 when any case fails.

## 0.1.1
- **Fix (anonymisation):** timestamps embedded in text fields (e.g. IMPEDANCE
  messages) were not date-shifted under pandas ≥3, where string columns use a
  dedicated dtype rather than `object` — the `== object` check skipped them and
  could leak the original date. Now matches object *and* string dtypes.
  (Outputs generated with pandas 2.x were unaffected.)

## 0.1.0
- Initial release. Decode BIS Advance/Vista data-download files (`.spa` trend
  incl. BIS index, `.f_a` spectrogram, `.r2a` raw EEG, events, messages) to tidy
  CSV/NumPy. Per-case date-shift anonymiser and cohort summariser. `bisdecode`
  CLI (`decode`, `cohort`).
