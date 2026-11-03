"""bisdecode — decode Aspect/Covidien/Medtronic BIS data-download files.

Reverse-engineered reader for the BIS Advance / Vista per-case `DH` export
(`.spa` processed trend, `.f_a` spectrogram, `.r2a` raw EEG, events, messages).
Includes a per-case date-shift anonymiser and a cohort-summary builder.

Public API:
    decode_case(path, outdir, ...)   -> per-case decode to tidy CSV / npy
    parse_spa / parse_spectra / parse_eeg / parse_events / parse_messages
    summarize(trend_csv, channel)    -> one-row cohort metrics
"""
from .decode import (
    decode_case, parse_spa, parse_spectra, parse_eeg, parse_events,
    parse_messages, parse_offset, EEG_SAMPLE_RATE, SENTINELS,
)
from .cohort import summarize

__version__ = "0.1.3"
__all__ = [
    "decode_case", "parse_spa", "parse_spectra", "parse_eeg", "parse_events",
    "parse_messages", "parse_offset", "summarize", "EEG_SAMPLE_RATE", "SENTINELS",
    "__version__",
]
