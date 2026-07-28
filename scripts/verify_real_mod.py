#!/usr/bin/env python3
"""Validate a real mod and enforce source-stability budgets without saving."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Sequence

from hoi4.config import find_config
from hoi4.release_gate import DEFAULT_MIN_PROBES, DiffBudget, format_report, run_release_gate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mod_root",
        nargs="?",
        type=Path,
        help="Mod directory. If omitted, discover it from .hoi4.json.",
    )
    parser.add_argument("--hoi4-install", type=Path, help="Optional vanilla HOI4 directory")
    parser.add_argument(
        "--config-start",
        type=Path,
        default=Path.cwd(),
        help="Starting directory for .hoi4.json discovery",
    )
    parser.add_argument("--max-changed-lines", type=int, default=20)
    parser.add_argument("--max-files", type=int, default=1)
    parser.add_argument("--min-probes", type=int, default=DEFAULT_MIN_PROBES)
    parser.add_argument(
        "--require-probe",
        action="append",
        default=[],
        help="Require the named probe to pass; may be repeated",
    )
    parser.add_argument("--strict-loading", action="store_true")
    parser.add_argument("--validate-icons", action="store_true")
    parser.add_argument("--strict-localization", action="store_true")
    parser.add_argument("--error-log", type=Path)
    parser.add_argument("--log-since", type=datetime.fromisoformat)
    parser.add_argument("--require-fresh-game-log", action="store_true")
    parser.add_argument("--allow-load-diagnostics", action="store_true")
    parser.add_argument("--allow-validation-errors", action="store_true")
    parser.add_argument("--allow-empty-diff", action="store_true")
    parser.add_argument("--max-diagnostics", type=int, default=20)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    mod_root = args.mod_root
    hoi4_install = args.hoi4_install
    if mod_root is None:
        config = find_config(args.config_start)
        if config is None or config.mod_path is None:
            _parser().error("no mod_root supplied and no usable .hoi4.json was found")
        mod_root = config.mod_path
        if hoi4_install is None:
            hoi4_install = config.hoi4_install

    report = run_release_gate(
        mod_root,
        hoi4_install=hoi4_install,
        budget=DiffBudget(
            max_changed_lines=args.max_changed_lines,
            max_files=args.max_files,
            require_diff=not args.allow_empty_diff,
        ),
        validate_icons=args.validate_icons,
        strict_localization=args.strict_localization,
        fail_on_load_diagnostics=not args.allow_load_diagnostics,
        fail_on_validation_errors=not args.allow_validation_errors,
        min_probes=args.min_probes,
        required_probes=args.require_probe,
        strict_loading=args.strict_loading,
        error_log=args.error_log,
        error_log_since=args.log_since,
        require_fresh_game_log=args.require_fresh_game_log,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_report(report, max_diagnostics=args.max_diagnostics))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
