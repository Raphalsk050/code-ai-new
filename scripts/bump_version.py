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
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from code_ai.version import bump, read_version  # noqa: E402

EXTENSION = Path(__file__).resolve().parents[1] / "vscode-extension"
# The extension's own version, and the lock file's two copies of it (the root
# and the "" package), which are the first "version" keys in each file.
_EXTENSION_VERSIONS = (("package.json", 1), ("package-lock.json", 2))


def sync_extension_version(release: str, root: Path = EXTENSION) -> None:
    """Ship the VS Code extension under the product's X.Y.Z, not a number of its own."""
    for name, count in _EXTENSION_VERSIONS:
        path = root / name
        if not path.is_file():
            continue
        # newline="": keep the file's own line endings.
        with open(path, encoding="utf-8", newline="") as handle:
            text = handle.read()
        updated = re.sub(r'("version":\s*")[^"]*(")', rf"\g<1>{release}\g<2>", text, count=count)
        if updated != text:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(updated)


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
    sync_extension_version(updated.release)
    print(f"v{previous.full} -> v{updated.full}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
