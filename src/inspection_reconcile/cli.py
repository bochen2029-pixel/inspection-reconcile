"""Command-line entry point (SPEC §11). Subcommands are added step by step (SPEC §16.3)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from inspection_reconcile import __version__


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="inspection-reconcile",
        description="Check inspection documentation readiness against an explicit requirement pack.",
    )
    parser.add_argument("--version", action="version", version=f"inspection-reconcile {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_console()
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
