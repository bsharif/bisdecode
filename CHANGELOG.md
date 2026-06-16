# Changelog

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
