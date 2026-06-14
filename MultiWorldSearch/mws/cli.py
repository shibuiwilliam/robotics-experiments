"""MWS unified CLI entrypoint."""

from __future__ import annotations

import click

from mws.core.logging import setup_logging

# Load .env file at CLI startup so GOOGLE_API_KEY and MWS_* vars are available
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional


@click.group()
@click.option("--seed", default=0, type=int, help="Global random seed")
@click.option("--cloud-mode", default="mock", type=click.Choice(["mock", "live"]))
@click.option("--json-log", is_flag=True, help="Output structured JSON logs")
@click.pass_context
def main(ctx: click.Context, seed: int, cloud_mode: str, json_log: bool) -> None:
    """Multi-World Search (MWS) — collective memory for embodied AI swarms."""
    import os

    os.environ.setdefault("MWS_CLOUD_MODE", cloud_mode)
    os.environ.setdefault("MWS_SEED", str(seed))
    setup_logging(json_output=json_log)
    ctx.ensure_object(dict)
    ctx.obj["seed"] = seed
    ctx.obj["cloud_mode"] = cloud_mode


@main.command("version")
def version_cmd() -> None:
    """Print MWS version."""
    from mws import __version__

    click.echo(f"mws {__version__}")


# --- sim ---
@main.group()
def sim() -> None:
    """Simulation commands."""


@sim.command("run")
@click.option("--config", default="configs/worlds/warehouse.yaml", help="World config")
@click.option("--seed", default=None, type=int)
@click.option("--steps", default=100, type=int)
@click.pass_context
def sim_run(ctx: click.Context, config: str, seed: int | None, steps: int) -> None:
    """Run a MuJoCo simulation world and generate observation atoms."""
    from mws.scenarios.sim_runner import run_simulation

    effective_seed = seed if seed is not None else ctx.obj["seed"]
    run_simulation(config_path=config, seed=effective_seed, steps=steps)


# --- index ---
@main.group()
def index() -> None:
    """Index management commands."""


@index.command("build")
@click.option("--config", default="configs/index/default.yaml", help="Index config")
@click.option(
    "--batch-api",
    is_flag=True,
    help="Embed via the async Gemini Batch API (live mode only; 50% cost, E3)",
)
@click.option("--resume-job", default=None, help="Resume polling an existing batch job name")
@click.option("--poll-interval", default=15.0, type=float, help="Batch job poll interval (s)")
@click.option(
    "--timeout",
    default=600.0,
    type=float,
    help="Max seconds to wait for the batch job (resume later with --resume-job)",
)
@click.pass_context
def index_build(
    ctx: click.Context,
    config: str,
    batch_api: bool,
    resume_job: str | None,
    poll_interval: float,
    timeout: float,
) -> None:
    """Build/rebuild multi-index from stored atoms."""
    import json

    if batch_api or resume_job:
        from mws.scenarios.index_builder import build_index_batch_api

        summary = build_index_batch_api(
            config_path=config,
            seed=ctx.obj["seed"],
            resume_job_name=resume_job,
            poll_interval=poll_interval,
            timeout=timeout,
        )
    else:
        from mws.scenarios.index_builder import build_index

        summary = build_index(config_path=config, seed=ctx.obj["seed"])
    click.echo(json.dumps(summary, indent=2, default=str))


# --- scenario ---
@main.group()
def scenario() -> None:
    """Scenario execution commands."""


@scenario.command("run")
@click.option("--name", required=True, help="Scenario name (e.g. maintenance_handoff)")
@click.option("--seed", default=None, type=int)
@click.option("--config", default=None, help="Scenario config override")
@click.option(
    "--no-acceptance",
    is_flag=True,
    help="Skip acceptance assertion (do not fail on unmet criteria).",
)
@click.pass_context
def scenario_run(
    ctx: click.Context,
    name: str,
    seed: int | None,
    config: str | None,
    no_acceptance: bool,
) -> None:
    """Run a verification scenario end-to-end.

    Honors the resolved cloud mode (from --cloud-mode or .env). A LIVE run
    (real Gemini spend) is refused unless MWS_CONFIRM_LIVE_SPEND=1 (G1); the
    resolved mode/backend/seed are printed first (G2/G5); and the scenario's
    acceptance criteria are asserted against the produced metrics, exiting
    non-zero on any miss (G4) unless --no-acceptance.
    """
    from mws.core.config import get_settings
    from mws.core.runguard import format_mode_banner, live_spend_refusal
    from mws.eval.acceptance import check_acceptance
    from mws.scenarios.runner import run_scenario

    effective_seed = seed if seed is not None else ctx.obj["seed"]
    settings = get_settings()
    click.echo(format_mode_banner(settings, effective_seed))
    refusal = live_spend_refusal(settings, n_scenarios=1)
    if refusal:
        raise click.ClickException(refusal)

    result = run_scenario(name=name, seed=effective_seed, config_path=config)

    # G4: assert acceptance criteria against the produced metrics. The mock
    # pytest suite gates the in-memory path; this gates the live/ES path too.
    if not no_acceptance:
        checks = check_acceptance(name, (result or {}).get("metrics", {}))
        if checks:
            for c in checks:
                mark = "PASS" if c.passed else "FAIL"
                click.echo(f"  [acceptance] {mark}: {c.label}")
            failed = [c for c in checks if not c.passed]
            if failed:
                raise click.ClickException(
                    f"{name}: {len(failed)}/{len(checks)} acceptance criteria FAILED "
                    f"({', '.join(c.label for c in failed)})"
                )
            click.echo(f"  [acceptance] {name}: all {len(checks)} criteria PASS")


@scenario.command("list")
def scenario_list() -> None:
    """List available scenarios."""
    from mws.scenarios.registry import list_scenarios

    for name in list_scenarios():
        click.echo(name)


# --- eval ---
@main.group()
def eval_cmd() -> None:
    """Evaluation commands."""


@eval_cmd.command("run")
@click.option("--run-id", required=True, help="Run ID to evaluate")
@click.pass_context
def eval_run(ctx: click.Context, run_id: str) -> None:
    """Compute metrics and generate a report for a completed run."""
    from mws.eval.report import generate_report

    generate_report(run_id=run_id)


@eval_cmd.command("reconcile")
@click.option("--log", "log_path", required=True, help="Run log file (stderr capture)")
@click.option("--runs", required=True, help="Comma-separated run IDs (or full run dir paths)")
def eval_reconcile(log_path: str, runs: str) -> None:
    """Audit a LIVE run: actual cloud calls in the log vs metrics counts.

    Exits non-zero if any real call escaped the trackers (delta != 0).
    """
    import json
    import sys
    from pathlib import Path

    from mws.core.config import get_settings
    from mws.eval.reconcile import reconcile

    run_root = get_settings().run_dir
    run_dirs = []
    for token in (t.strip() for t in runs.split(",") if t.strip()):
        p = Path(token)
        run_dirs.append(p if p.exists() else run_root / token)
    result = reconcile(Path(log_path), run_dirs)
    click.echo(json.dumps(result, indent=2))
    if not result["reconciled"]:
        sys.exit(1)


@eval_cmd.command("e1-ab")
def eval_e1_ab() -> None:
    """Run the E1 A/B: task-instruction prefixes (v2) vs raw embedding (v1).

    Live-only by definition (prefixes exist on the Gemini teacher). Costs
    ~52 embedding requests. Prints JSON and records a runs/ manifest.
    """
    import json
    import uuid

    from mws.core.config import get_settings
    from mws.eval.e1_ab import run_e1_ab
    from mws.eval.report import create_manifest, save_report

    settings = get_settings()
    result = run_e1_ab(settings)
    run_id = f"e1_ab-{settings.seed}-{uuid.uuid4().hex[:8]}"
    manifest = create_manifest(run_id=run_id, scenario="e1_ab", seed=settings.seed)
    save_report(run_id=run_id, manifest=manifest, metrics=result)
    result["run_id"] = run_id
    click.echo(json.dumps(result, indent=2, default=str))


@eval_cmd.command("tune-fusion")
def eval_tune_fusion() -> None:
    """Pre-registered RRF weight sweep on scenario-owned golden pairs (mock).

    Renders the pre-registered verdict (adopt / current confirmed) and records
    the sweep table to runs/. Adoption additionally requires the golden test
    suite and a live confirmation — never applied automatically here.
    """
    import json
    import uuid

    from mws.core.config import get_settings
    from mws.eval.report import create_manifest, save_report
    from mws.eval.tune_fusion import run_fusion_tuning

    settings = get_settings()
    result = run_fusion_tuning()
    run_id = f"tune_fusion-{settings.seed}-{uuid.uuid4().hex[:8]}"
    manifest = create_manifest(run_id=run_id, scenario="tune_fusion", seed=settings.seed)
    save_report(run_id=run_id, manifest=manifest, metrics=result)
    result["run_id"] = run_id
    click.echo(json.dumps(result, indent=2, default=str))


@eval_cmd.command("multi-seed")
@click.option("--scenarios", default="", help="Comma-separated scenario names (default: all)")
@click.option("--seeds", default="0,1,2,3", help="Comma-separated seeds")
def eval_multi_seed(scenarios: str, seeds: str) -> None:
    """Run scenarios across multiple seeds and report mean ± 95% CI."""
    import json

    from mws.eval.multi_seed import run_multi_seed_suite
    from mws.scenarios.registry import list_scenarios

    names = [s.strip() for s in scenarios.split(",") if s.strip()] or list(list_scenarios())
    seed_list = [int(s) for s in seeds.split(",") if s.strip()]
    result = run_multi_seed_suite(names, seeds=seed_list)
    click.echo(json.dumps(result, indent=2, default=str))


# --- agent ---
@main.group()
def agent() -> None:
    """Agent commands."""


@agent.command("serve")
@click.option("--config", default="configs/agents/ops_agent.yaml", help="Agent config")
@click.pass_context
def agent_serve(ctx: click.Context, config: str) -> None:
    """Start an ADK agent with MWS search tools."""
    from mws.agents.serve import serve_agent

    serve_agent(config_path=config, cloud_mode=ctx.obj["cloud_mode"])


if __name__ == "__main__":
    main()
