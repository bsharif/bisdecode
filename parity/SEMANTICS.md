# Parser semantics the browser ports copy from Python

The Python decoder (`src/bisdecode/decode.py`, run on pandas 3.1) is the
reference. Both ports (`ts/`, `rust/`) follow these rules; the parity test
(`ts/test/parity.test.ts`) checks them against Python on every run.

| Topic | Rule (what pandas / Python does) |
|---|---|
| Text decoding | UTF-8, strict (bad bytes are an error), a BOM is kept as text |
| Line splitting | `str.splitlines()`: `\r\n`, `\r`, `\n`, `\v`, `\f`, `\x1c`–`\x1e`, `\x85`, ` `, ` `; no empty last line |
| Strip | `str.strip()`: Python whitespace, which is not the same as JS `trim()` (Python keeps `U+FEFF`, strips `\x1c`–`\x1f`) |
| Timestamps | `%m/%d/%Y %H:%M:%S`, not stripped first: 1–2 digit fields, exactly 4-digit year, seconds 0–61 (60 and 61 roll over to the next minute), real calendar dates only. Output is epoch ms, reading the clock time as UTC; NaN for NaT |
| `.spa` numbers | A column is numeric only if every non-blank cell parses as a number and none is the text `nan`; otherwise it stays text. `inf`, `infinity`, `5.`, `.5`, `1e3` parse; `1_000`, `0x10`, `1e` do not |
| Sentinels | `-3276.8`, `-3276.0`, `-327.7`, `3276.8`, and any value `<= -3276.0` become NaN (numeric `.spa` columns only, not spectra) |
| Hex fields | `ARTF2`, `BISBIT00` use Python `int(v, 16)`: optional sign, optional `0x`, single `_` between digits; blank or invalid gives NaN (`<NA>`) |
| `.spa` rows | Rows are cut to the header width; short rows are padded with blanks; whitespace-only lines are skipped; an all-blank unnamed column (`colN`) is dropped |
| Spectra | Only the second `\|` field holds bins; one trailing empty bin is dropped; at least 60 bins, more if a row is wider; bad or missing bins give NaN |
| Events | Same match as `EVENT_RE`; the message is the shortest text before an optional `(ID n)`; `id` stays text, null when absent |
| Offsets | `.o_a` timestamps stay as text; offset and size are integers |
| Raw EEG | int16 little-endian; a final odd byte or half sample pair is dropped |

Known differences (cannot occur in a BIS export): the ports accept only ASCII
digits where Python's `\d` also takes other Unicode digits; hex values above
2^53 lose precision; empty pandas tables are `object` dtype, so the parity test
compares only column names when a table has no rows.
