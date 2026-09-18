#!/usr/bin/env python
"""Raise the project version in ``src/code_ai/version.json``.

Run it at the end of a piece of work, before committing:

    python scripts/bump_version.py patch -m "Answer instead of hanging with the tools off"
    python scripts/bump_version.py minor -m "Add the documents tool group"
    python scripts/bump_version.py major -m "..." --allow-major
    python scripts/bump_version.py --show

``patch`` is the default kind for a reason: reach for Z first, for Y only when
something genuinely new landed, and never for X unless the user asked. The
build counter moves on every bump and never resets.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from code_ai.version import bump, read_version  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bump_version")
    parser.add_argument(
        "kind",
        nargs="?",
        default="patch",
        choices=("major", "minor", "patch"),
        help="Which number to raise. Defaults to patch.",
    )
    parser.add_argument(
        "-m",
        "--note",
        default="",
        help="One line saying what this build is for.",
    )
    parser.add_argument(
        "--allow-major",
        action="store_true",
        help="Required for a major bump: X only moves when the user asks for it.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Print the current version and exit without changing anything.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.show:
        current = read_version()
        print(f"v{current.full}  ({current.updated or 'no date'})")
        if current.note:
            print(current.note)
        return 0
    if not args.note.strip():
        print("A bump needs a note: -m \"what this build is for\"", file=sys.stderr)
        return 2
    previous = read_version()
    try:
        updated = bump(args.kind, args.note, allow_major=args.allow_major)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"v{previous.full} -> v{updated.full}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
