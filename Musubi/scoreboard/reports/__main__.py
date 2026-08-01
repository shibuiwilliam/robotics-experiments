"""``python -m scoreboard.reports build --experiment E0``."""

from __future__ import annotations

import argparse

from scoreboard.reports.report import build_report


def main() -> int:
    parser = argparse.ArgumentParser(prog="scoreboard.reports")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build an experiment report")
    b.add_argument("--experiment", required=True)
    args = parser.parse_args()
    out = build_report(args.experiment)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
