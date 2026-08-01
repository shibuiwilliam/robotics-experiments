"""``python -m bench.runner {scenario|experiment} ...`` — the canonical run entry point.

Runs a scenario (or an experiment's scenarios) across arms × seeds, scores the oracle, ingests the
records into the DuckDB scoreboard, and prints a concise ladder summary.
"""

from __future__ import annotations

import argparse

from bench.runner.run import RunRecord, run_experiment, run_scenario
from scoreboard.metrics import MetricsStore


def _summarize(records: list[RunRecord]) -> None:
    store = MetricsStore()  # persistent scoreboard (data/scoreboard.duckdb)
    store.ingest([r.to_dict() for r in records])
    scenarios = sorted({r.scenario for r in records})
    for sc in scenarios:
        print(f"\n== {sc} ==")
        print(
            f"{'arm':>4} {'n':>3} {'oracle':>7} {'success':>8} {'unappr':>7} {'trace':>6} {'api':>4}"
        )
        for s in store.arm_summaries(sc):
            print(
                f"{s.arm:>4} {s.n:>3} {s.oracle_pass_rate:>7.2f} {s.success_rate:>8.2f} "
                f"{s.unapproved_irreversible:>7} {s.mean_trace_completeness:>6.2f} {s.total_api_calls:>4}"
            )
    store.close()
    unappr = sum(r.unapproved_irreversible for r in records)
    api = sum(r.api_calls for r in records)
    passed = sum(r.oracle_passed for r in records)
    print(
        f"\n{len(records)} runs · oracle-pass {passed}/{len(records)} · "
        f"unapproved-irreversible {unappr} · API calls {api}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="bench.runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scenario", help="run one scenario")
    s.add_argument("--name", required=True)
    s.add_argument("--arm", default=None)

    e = sub.add_parser("experiment", help="run an experiment's scenarios")
    e.add_argument("--name", required=True)

    args = parser.parse_args()
    if args.cmd == "scenario":
        records = run_scenario(args.name, arm=args.arm)
    else:
        records = run_experiment(args.name)
        if not records:
            print(f"no scenarios mapped to experiment {args.name!r}")
            return 1
    _summarize(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
