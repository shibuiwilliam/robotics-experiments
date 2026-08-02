"""Musubi Console implementation.

Each command handler returns a JSON-serializable dict (for ``--json``) and is rendered to a compact
human view otherwise. Heavy imports (bench/sim → mujoco) are deferred into the handlers that need
them so ``status`` / ``scenarios`` / ``ontology`` stay fast.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from config import load_registry

_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- rendering
def _emit(data: Any, as_json: bool, human: str | None = None) -> int:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True, default=str))
    else:
        print(human if human is not None else json.dumps(data, indent=2, default=str))
    return 0


def _kv(title: str, rows: list[tuple[str, Any]]) -> str:
    width = max((len(k) for k, _ in rows), default=0)
    lines = [f"# {title}"]
    lines += [f"  {k.ljust(width)}  {v}" for k, v in rows]
    return "\n".join(lines)


# --------------------------------------------------------------------------- status / doctor
def cmd_status(args: argparse.Namespace) -> int:
    reg = load_registry()
    from clients.guard import llm_imports_outside_clients
    from ontology import artifacts

    artifacts_present = {
        p.name: p.exists()
        for p in (
            artifacts.JSON_SCHEMA_PATH,
            artifacts.CONTEXT_PATH,
            artifacts.SHACL_PATH,
            artifacts.WORLD_OK_PATH,
        )
    }
    from bench.scenarios import list_scenarios

    offenders = llm_imports_outside_clients()
    scenarios = list_scenarios()
    keys = {
        "GOOGLE_API_KEY": bool(os.environ.get("GOOGLE_API_KEY")),
        "ANTHROPIC_API_KEY": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }
    offline_ready = all(artifacts_present.values()) and not offenders
    data = {
        "python": sys.version.split()[0],
        "vcr_mode": reg.vcr_mode(),
        "llm_provider": reg.llm_provider(),
        "agent_model": reg.agent_model(),
        "keys_present": keys,
        "ontology_artifacts": artifacts_present,
        "scenarios": scenarios,
        "invariants": {"no_llm_import_outside_clients": not offenders, "offenders": offenders},
        "offline_ready": offline_ready,
    }
    human = _kv(
        "Musubi status",
        [
            ("python", data["python"]),
            ("vcr_mode", data["vcr_mode"]),
            ("llm_provider", data["llm_provider"]),
            ("agent_model", data["agent_model"]),
            ("keys", f"GOOGLE={keys['GOOGLE_API_KEY']} ANTHROPIC={keys['ANTHROPIC_API_KEY']}"),
            ("ontology_artifacts", "OK" if all(artifacts_present.values()) else artifacts_present),
            ("scenarios", ", ".join(scenarios)),
            ("choke-point clean", "✓" if not offenders else f"✗ {offenders}"),
            ("offline_ready", "✓" if offline_ready else "✗"),
        ],
    )
    return _emit(data, args.json, human)


# --------------------------------------------------------------------------- providers
def cmd_providers(args: argparse.Namespace) -> int:
    reg = load_registry()
    active = reg.llm_provider()
    providers = {
        "gemini": {
            "model": reg.model_id("agent"),
            "key": "GOOGLE_API_KEY",
            "key_present": bool(os.environ.get("GOOGLE_API_KEY")),
        },
        "claude": {
            "model": reg.get("models.claude.id"),
            "key": "ANTHROPIC_API_KEY",
            "key_present": bool(os.environ.get("ANTHROPIC_API_KEY")),
        },
    }
    data = {
        "active": active,
        "providers": providers,
        "note": "Offline uses FakeGeminiClient regardless of provider; live needs the key + MUSUBI_VCR_MODE=record.",
    }
    human = _kv(
        "LLM providers (agent engine)",
        [
            (
                f"{'* ' if name == active else '  '}{name}",
                f"{p['model']}  key={p['key']}={p['key_present']}",
            )
            for name, p in providers.items()
        ],
    )
    return _emit(data, args.json, human)


# --------------------------------------------------------------------------- ontology
def cmd_ontology(args: argparse.Namespace) -> int:
    from ontology import artifacts

    if args.ontology_cmd == "concepts":
        defs = sorted(artifacts.json_schema().get("$defs", {}))
        return _emit(
            {"concepts": defs, "count": len(defs)},
            args.json,
            "# ontology concepts\n  " + ", ".join(defs),
        )
    # validate
    doc = json.loads(Path(args.file).read_text(encoding="utf-8"))
    conforms, report = artifacts.validate_world(doc)
    data = {"file": args.file, "conforms": conforms, "report": report.strip()}
    return _emit(
        data,
        args.json,
        f"# SHACL world_ok validation\n  file: {args.file}\n  conforms: {conforms}\n{report.strip()}",
    )


# --------------------------------------------------------------------------- scenarios
def cmd_scenarios(args: argparse.Namespace) -> int:
    from bench.scenarios import list_scenarios, load_scenario

    if args.scenarios_cmd == "ls":
        rows = []
        for name in list_scenarios():
            sc = load_scenario(name)
            rows.append(
                {
                    "scenario": name,
                    "experiment": sc.experiment,
                    "driver": sc.driver_name(),
                    "arms": sc.arms,
                }
            )
        human = "# scenarios\n" + "\n".join(
            f"  {r['scenario']:<16} E={r['experiment']:<4} driver={r['driver']:<18} arms={r['arms']}"
            for r in rows
        )
        return _emit(rows, args.json, human)
    # show
    sc = load_scenario(args.id)
    data = {
        "scenario": sc.name,
        "title": sc.title,
        "experiment": sc.experiment,
        "driver": sc.driver_name(),
        "arms": sc.arms,
        "seeds": sc.seeds,
        "perception": sc.perception,
        "invisible_hand": sc.invisible_hand,
        "norms": sc.norms,
        "sweep": sc.sweep,
        "oracle": {
            "success": sc.oracle.success,
            "must": sc.oracle.must,
            "acceptable_world": sc.oracle.acceptable_world,
            "endpoints": sc.oracle.endpoints,
        },
    }
    human = _kv(
        f"scenario {sc.name}",
        [
            ("title", sc.title),
            ("experiment", sc.experiment),
            ("driver", sc.driver_name()),
            ("arms", sc.arms),
            ("perturbations", [p.get("op") for p in sc.invisible_hand] or "—"),
            ("oracle.success", sc.oracle.success or "—"),
            ("oracle.must", sc.oracle.must or "—"),
            ("oracle.endpoints", sc.oracle.endpoints or "—"),
        ],
    )
    return _emit(data, args.json, human)


# --------------------------------------------------------------------------- run
def _summarize_runs(records: list[Any]) -> dict[str, Any]:
    from scoreboard.metrics import MetricsStore

    store = MetricsStore(":memory:")
    store.ingest([r.to_dict() for r in records])
    scenarios = sorted({r.scenario for r in records})
    summary: dict[str, Any] = {
        "scenarios": {},
        "totals": {
            "runs": len(records),
            "oracle_pass": sum(r.oracle_passed for r in records),
            "unapproved_irreversible": sum(r.unapproved_irreversible for r in records),
            "api_calls": sum(r.api_calls for r in records),
        },
    }
    for sc in scenarios:
        summary["scenarios"][sc] = [
            {
                "arm": s.arm,
                "n": s.n,
                "oracle_pass_rate": s.oracle_pass_rate,
                "unapproved_irreversible": s.unapproved_irreversible,
                "mean_trace": s.mean_trace_completeness,
            }
            for s in store.arm_summaries(sc)
        ]
    store.close()
    return summary


def cmd_run(args: argparse.Namespace) -> int:
    from bench.runner.run import run_experiment, run_scenario

    if args.run_cmd == "scenario":
        records = run_scenario(args.id, arm=args.arm)
    else:
        records = run_experiment(args.id)
        if not records:
            return _emit(
                {"error": f"no scenarios mapped to experiment {args.id!r}"},
                args.json,
                f"no scenarios for experiment {args.id}",
            )
    summary = _summarize_runs(records)
    lines = ["# run summary"]
    for sc, arms in summary["scenarios"].items():
        lines.append(f"  {sc}:")
        for a in arms:
            lines.append(
                f"    {a['arm']:<4} n={a['n']:<3} oracle={a['oracle_pass_rate']:.2f} unappr={a['unapproved_irreversible']} trace={a['mean_trace']:.2f}"
            )
    t = summary["totals"]
    lines.append(
        f"  totals: {t['runs']} runs · oracle-pass {t['oracle_pass']}/{t['runs']} · unappr-irrev {t['unapproved_irreversible']} · API {t['api_calls']}"
    )
    return _emit(summary, args.json, "\n".join(lines))


# --------------------------------------------------------------------------- inspect
def cmd_inspect(args: argparse.Namespace) -> int:
    from bench.oracle import evaluate, evaluate_must
    from bench.runner.drivers import get_driver
    from bench.scenarios import load_scenario

    sc = load_scenario(args.id)
    driver = get_driver(sc)
    ctx = driver(sc, args.arm, args.seed, {})

    beliefs = []
    if ctx.claims is not None:
        for c in ctx.claims.all()[: args.limit]:
            beliefs.append(
                {
                    "iri": str(c.iri),
                    "subject": str(c.subject),
                    "predicate": str(c.predicate),
                    "value": str(c.objectValue),
                    "method": str(getattr(c.method, "value", c.method)),
                }
            )
    events = [
        {"type": e.event_type, "id": e.id, "trace": e.trace, "payload": e.payload}
        for e in ctx.events[: args.limit]
    ]
    success: object = None
    if sc.oracle.success:
        try:
            success = bool(evaluate(sc.oracle.success, ctx))
        except Exception as exc:  # noqa: BLE001 - surfaced for the operator
            success = f"error: {exc}"
    musts = {m: evaluate_must(m, ctx) for m in sc.oracle.must}
    data = {
        "scenario": sc.name,
        "arm": args.arm,
        "seed": args.seed,
        "success": success,
        "must": musts,
        "metrics": {k: v for k, v in ctx.extras.items() if isinstance(v, (int, float, bool))},
        "claims": ctx.claims.count() if ctx.claims else 0,
        "bus_events": len(ctx.events),
        "drilldown": dict(ctx.extras.get("drilldown", {})),
        "beliefs_sample": beliefs,
        "events_sample": events,
        "api_calls": ctx.api_calls,
    }
    lines = [
        f"# inspect {sc.name} [{args.arm} seed{args.seed}]",
        f"  success: {success}",
        f"  must: {musts}",
        f"  claims: {data['claims']}  bus_events: {data['bus_events']}  api_calls: {data['api_calls']}",
        f"  metrics: {data['metrics']}",
    ]
    if data["drilldown"]:
        first = next(iter(data["drilldown"].items()))
        lines.append(f"  drilldown[{first[0]}]: {' -> '.join(first[1])}")
    return _emit(data, args.json, "\n".join(lines))


# --------------------------------------------------------------------------- parser
def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)  # --json works before or after the subcommand
    # SUPPRESS default so a leaf occurrence never clobbers a top-level --json (argparse parent quirk).
    common.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="emit machine-readable JSON"
    )

    parser = argparse.ArgumentParser(
        prog="console", description="Musubi operator cockpit", parents=[common]
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser(
        "status", parents=[common], help="environment + invariant + artifact health"
    ).set_defaults(func=cmd_status)
    sub.add_parser("doctor", parents=[common], help="alias for status").set_defaults(
        func=cmd_status
    )
    sub.add_parser(
        "providers", parents=[common], help="LLM providers + active engine"
    ).set_defaults(func=cmd_providers)

    ont = sub.add_parser("ontology", help="inspect / validate the ontology").add_subparsers(
        dest="ontology_cmd", required=True
    )
    ont.add_parser("concepts", parents=[common], help="list generated concepts").set_defaults(
        func=cmd_ontology
    )
    ov = ont.add_parser("validate", parents=[common], help="SHACL world_ok on a JSON-LD file")
    ov.add_argument("--file", required=True)
    ov.set_defaults(func=cmd_ontology)

    scn = sub.add_parser("scenarios", help="list / show scenarios").add_subparsers(
        dest="scenarios_cmd", required=True
    )
    scn.add_parser("ls", parents=[common], help="list scenarios").set_defaults(func=cmd_scenarios)
    ss = scn.add_parser("show", parents=[common], help="show a scenario's DSL")
    ss.add_argument("id")
    ss.set_defaults(func=cmd_scenarios)

    run = sub.add_parser("run", help="run a scenario or experiment").add_subparsers(
        dest="run_cmd", required=True
    )
    rs = run.add_parser("scenario", parents=[common], help="run one scenario")
    rs.add_argument("id")
    rs.add_argument("--arm", default=None)
    rs.set_defaults(func=cmd_run)
    re_ = run.add_parser("experiment", parents=[common], help="run an experiment's scenarios")
    re_.add_argument("id")
    re_.set_defaults(func=cmd_run)

    ins = sub.add_parser(
        "inspect", parents=[common], help="introspect one run (beliefs/events/oracle/drilldown)"
    )
    ins.add_argument("id")
    ins.add_argument("--arm", default="A4")
    ins.add_argument("--seed", type=int, default=0)
    ins.add_argument("--limit", type=int, default=8)
    ins.set_defaults(func=cmd_inspect)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # --json is a global flag; ensure it exists even when placed before the subcommand
    if not hasattr(args, "json"):
        args.json = False
    return int(args.func(args))
