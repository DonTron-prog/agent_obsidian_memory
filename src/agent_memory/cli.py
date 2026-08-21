"""Command-line entry point."""

import argparse
from collections.abc import Sequence

from agent_memory import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory",
        description="Manage local-first agent memory.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
