#!/usr/bin/env python3
"""Validate release identity, extract notes, and checksum built artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from hoi4.release_tools import (
    changelog_section,
    validate_release_identity,
    write_sha256s,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check")
    check.add_argument("--tag", required=True)
    check.add_argument("--root", type=Path, default=Path.cwd())

    notes = subparsers.add_parser("notes")
    notes.add_argument("--tag", required=True)
    notes.add_argument("--root", type=Path, default=Path.cwd())
    notes.add_argument("--output", type=Path)

    checksums = subparsers.add_parser("checksums")
    checksums.add_argument("--dist", type=Path, default=Path("dist"))

    args = parser.parse_args()
    if args.command == "check":
        version = validate_release_identity(args.root, args.tag)
        print(f"release identity valid: {version}")
        return 0
    if args.command == "notes":
        version = validate_release_identity(args.root, args.tag)
        rendered = changelog_section(args.root, version) + "\n"
        if args.output is None:
            print(rendered, end="")
        else:
            args.output.write_text(rendered, encoding="utf-8")
        return 0
    output = write_sha256s(args.dist)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
