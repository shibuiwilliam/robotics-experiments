"""``python -m scoreboard.dashboard build [--scenario e0_smoke]``."""

from __future__ import annotations

import argparse

from scoreboard.dashboard.dashboard import build_dashboard


def main() -> int:
    parser = argparse.ArgumentParser(prog="scoreboard.dashboard")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the observability dashboard")
    b.add_argument("--scenario", default="e0_smoke")
    args = parser.parse_args()
    out = build_dashboard(args.scenario)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
