"""CLI entry point: python -m eval.runner [--sweep] [--config CFG]."""

from __future__ import annotations

import argparse
import sys

from eval.runner.sweep import run_dose_response_sweep, save_sweep_report


def main() -> None:
    parser = argparse.ArgumentParser(description="PSL-Bench evaluation runner")
    parser.add_argument("--sweep", action="store_true", help="Run dose-response sweep")
    parser.add_argument("--config", type=str, default=None, help="Config YAML (placeholder)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--n-steps", type=int, default=200, help="Sim steps")
    parser.add_argument("--output", type=str, default="experiments/runs/latest", help="Output dir")
    args = parser.parse_args()

    if args.sweep or args.config:
        print(f"Running dose-response sweep (seed={args.seed}, steps={args.n_steps})")
        report = run_dose_response_sweep(seed=args.seed, n_steps=args.n_steps)
        path = save_sweep_report(report, args.output)
        print(f"Results saved to {path}")
        print(f"Wall time: {report.wall_time_s:.2f}s")
        print()
        for r in report.results:
            print(
                f"  Dose {r.dose_index}: "
                f"RMSE={r.joint_pos_rmse:.2e}, "
                f"info_loss={r.information_loss:.2e}, "
                f"comm_div={r.commutativity_divergence:.2e}"
            )
    else:
        print("No --sweep or --config specified. Use --help for usage.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
