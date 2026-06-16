"""Unified `bisdecode` command with subcommands: decode, cohort."""
import sys

from . import __version__
from .decode import main as decode_main
from .cohort import main as cohort_main

SUBCOMMANDS = {"decode": decode_main, "cohort": cohort_main}

USAGE = f"""bisdecode {__version__} — BIS data-download decoder

usage: bisdecode <command> [options]

commands:
  decode   decode raw BIS case(s) to tidy CSV/npy (supports --anonymise)
  cohort   build a one-row-per-case summary from decoded trends

run `bisdecode <command> -h` for command options.
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0
    if argv[0] in ("-V", "--version"):
        print(f"bisdecode {__version__}")
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd not in SUBCOMMANDS:
        print(f"unknown command: {cmd}\n", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2
    return SUBCOMMANDS[cmd](rest) or 0


if __name__ == "__main__":
    raise SystemExit(main())
