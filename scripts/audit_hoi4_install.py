#!/usr/bin/env python3
"""Audit the SDK against an installed HOI4 version and a real mod corpus."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from hoi4.compatibility_audit import (
    EMPIRE_REQUIRED_PROBES,
    format_compatibility_report,
    run_compatibility_audit,
)
from hoi4.release_gate import DiffBudget, format_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mod_root", type=Path)
    parser.add_argument("--hoi4-install", type=Path, required=True)
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path.home() / ".local" / "state" / "hoi4-agent-sdk" / "reports",
    )
    parser.add_argument("--require-probe", action="append", dest="required_probes")
    parser.add_argument("--fingerprint-manifest", type=Path)
    parser.add_argument("--error-log", type=Path)
    parser.add_argument("--log-since", type=datetime.fromisoformat)
    parser.add_argument(
        "--allow-stale-log",
        action="store_true",
        help="Do not require --error-log to postdate every audited mod file",
    )
    parser.add_argument("--max-changed-lines", type=int, default=20)
    parser.add_argument("--max-files", type=int, default=1)
    args = parser.parse_args()

    required = args.required_probes or list(EMPIRE_REQUIRED_PROBES)
    report = run_compatibility_audit(
        args.mod_root,
        hoi4_install=args.hoi4_install,
        required_probes=required,
        fingerprint_manifest=args.fingerprint_manifest,
        budget=DiffBudget(
            max_changed_lines=args.max_changed_lines,
            max_files=args.max_files,
        ),
        error_log=args.error_log,
        error_log_since=args.log_since,
        require_fresh_game_log=(
            args.error_log is not None and not args.allow_stale_log
        ),
    )
    summary = format_compatibility_report(report)
    detail = f"{summary}\n\n{format_report(report.gate)}\n"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args.report_dir.mkdir(parents=True, exist_ok=True)
    text_path = args.report_dir / f"audit-{timestamp}.txt"
    json_path = args.report_dir / f"audit-{timestamp}.json"
    text_path.write_text(detail, encoding="utf-8")
    json_path.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(detail, end="")
    print(f"reports: {text_path} {json_path}")
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
