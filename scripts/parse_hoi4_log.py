#!/usr/bin/env python3
"""Report HOI4 engine errors attributable to one mod directory."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from hoi4.game_log import format_game_log_report, parse_hoi4_error_log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mod_root", type=Path)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--since", type=datetime.fromisoformat)
    parser.add_argument("--start-offset", type=int, default=0)
    parser.add_argument(
        "--incremental", action="store_true",
        help="Retain the trailing record; omit this flag after writing stops to flush it.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    report = parse_hoi4_error_log(
        args.log,
        args.mod_root,
        since=args.since,
        start_offset=args.start_offset,
        incremental=args.incremental,
    )
    if args.as_json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(format_game_log_report(report))
        print(f"next offset: {report.next_offset}")
    return 0 if report.is_fresh and not report.entries else 1


if __name__ == "__main__":
    raise SystemExit(main())
