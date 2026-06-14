"""ORX CLI（typer）— CLIが製品である。

全コマンド: --help、入力検証（実行可能なエラーメッセージ）、失敗時は非零終了。
`orx demo` はAPIキー・ビューア・ネットワーク不要で動く（DoD）。
"""

from __future__ import annotations

from pathlib import Path

import typer

from orx.common.config import ConfigError, RunConfig, WorldConfig, load_config
from orx.common.paths import repo_root, reports_dir, runs_root

app = typer.Typer(
    name="orx",
    help="ORX (Ontological Robotics eXperiments) — オントロジー駆動ロボティクス検証基盤。",
    no_args_is_help=True,
)

DEMO_WORLD = "configs/world/demo_tiny.yaml"


def _fail(message: str) -> None:
    typer.secho(f"エラー: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _echo_fidelity(report_obj: object) -> None:
    from orx.common.schemas import FidelityReport
    from orx.exp.report import _fidelity_rows

    assert isinstance(report_obj, FidelityReport)
    typer.echo("")
    typer.echo("忠実度レポート (oracle):")
    for label, value in _fidelity_rows(report_obj):
        typer.echo(f"  {label:<22} {value}")


@app.callback()
def main() -> None:
    """ORX CLI。`orx <command> --help` で各コマンドの説明を表示する。"""


@app.command()
def version() -> None:
    """インストールされている ORX のバージョンを表示する。"""
    from orx import __version__

    typer.echo(f"orx {__version__}")


@app.command()
def demo(
    seed: int = typer.Option(7, help="ルートシード"),
    duration: float = typer.Option(12.0, help="記録するシム時間 [s]"),
) -> None:
    """オフラインのエンドツーエンド・スモークラン（P0パイプライン、〜1分）。

    sim → perception → anchoring → world graph → oracle を実行し、
    忠実度レポートを表示する。APIキー・ビューア不要。
    """
    from orx.exp.episode import record_episode

    world_path = repo_root() / DEMO_WORLD
    try:
        world = load_config(world_path, WorldConfig)
        config = RunConfig(world=world, duration_s=duration, root_seed=seed)
        typer.echo(f"記録中: world={world.name} seed={seed} duration={duration}s (stub/offline)")
        run_id, fidelity = record_episode(config, runs_root(), run_id=f"demo-seed{seed}")
    except (ConfigError, ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    typer.echo(f"run_id: {run_id}  ({runs_root() / run_id})")
    _echo_fidelity(fidelity)
    if fidelity.triple_f1 > 0.95:
        typer.secho("OK: 忠実度F1 > 0.95 (P0完了基準)", fg=typer.colors.GREEN)
    else:
        _fail(f"忠実度F1が基準未達: {fidelity.triple_f1}")


sim_app = typer.Typer(help="シミュレーション実行（`orx sim run`）")
app.add_typer(sim_app, name="sim")


@sim_app.command("run")
def sim_run(
    world_config: Path = typer.Argument(..., help="世界コンフィグ (configs/world/*.yaml)"),
    duration: float = typer.Option(20.0, help="記録するシム時間 [s]"),
    seed: int = typer.Option(7, help="ルートシード"),
    run_id: str | None = typer.Option(None, help="run ID（省略時は自動生成）"),
    embedder: str = typer.Option("stub", help="視覚埋め込み器: stub / clip"),
) -> None:
    """エピソードを記録し data/runs/<run_id>/ に保存する。"""
    from orx.exp.episode import record_episode

    try:
        world = load_config(world_config, WorldConfig)
        config = RunConfig(
            world=world, duration_s=duration, root_seed=seed, visual_embedder=embedder
        )
        actual_id, fidelity = record_episode(config, runs_root(), run_id=run_id)
    except (ConfigError, ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    typer.echo(f"記録完了: {runs_root() / actual_id}")
    _echo_fidelity(fidelity)


@app.command()
def replay(
    run_id: str = typer.Argument(..., help="data/runs/ 配下の run ID"),
    condition: str = typer.Option("OR-full", "--condition", help="再生条件"),
) -> None:
    """記録済みrunを指定条件で反実仮想リプレイする（物理再実行なし）。"""
    from orx.exp.episode import CONDITIONS, replay_episode

    run_dir = runs_root() / run_id
    if not run_dir.exists():
        _fail(
            f"run が見つかりません: {run_dir}\n"
            "（`orx demo` か `orx sim run` で記録してください）"
        )
    if condition not in CONDITIONS:
        _fail(f"未知の条件 {condition!r}。対応: {', '.join(sorted(CONDITIONS))}")
    try:
        fidelity = replay_episode(run_dir, condition=condition)
    except (ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    typer.echo(f"リプレイ完了: condition={condition}")
    _echo_fidelity(fidelity)


exp_app = typer.Typer(help="実験の実行（`orx exp run`）")
app.add_typer(exp_app, name="exp")


@exp_app.command("run")
def exp_run(
    experiment_config: Path = typer.Argument(
        ..., help="実験コンフィグ (configs/experiments/*.yaml)"
    ),
) -> None:
    """条件×シード×タスクの実験を実行し、結果とレポートを出力する。

    シード毎に1回記録し、全条件を反実仮想リプレイで対比較する。
    """
    from orx.common.providers import CacheMissError
    from orx.exp.runner import (
        load_experiment,
        run_experiment,
        summarize,
        write_experiment_report,
    )

    try:
        config = load_experiment(experiment_config)
        typer.echo(
            f"実験 {config.name}: task={config.task} "
            f"conditions={config.conditions} seeds={len(config.seeds)}"
        )
        exp_dir, result = run_experiment(config, runs_root(), progress=typer.echo)
        report_path = write_experiment_report(exp_dir, reports_dir())
    except CacheMissError as exc:
        _fail(f"{exc}\n（オフライン実行は provider.mode を stub にしてください）")
        return
    except (ConfigError, ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    typer.echo("")
    for line in summarize(result):
        typer.echo(line)
    typer.echo(f"結果: {exp_dir / 'results.json'}")
    typer.echo(f"レポート: {report_path}")


@app.command()
def report(
    target_id: str = typer.Argument(..., help="data/runs/ 配下の run ID または exp ID"),
) -> None:
    """run/実験/シナリオのMarkdownレポートを reports/ に再生成する。"""
    from orx.exp import scenario as scn
    from orx.exp.report import write_run_report
    from orx.exp.runner import RESULTS, write_experiment_report

    target_dir = runs_root() / target_id
    if not target_dir.exists():
        _fail(f"run/exp が見つかりません: {target_dir}")
    try:
        if scn.is_scenario_result(target_dir):
            out = scn.write_report(target_dir, reports_dir())
        elif (target_dir / RESULTS).exists():
            out = write_experiment_report(target_dir, reports_dir())
        else:
            out = write_run_report(target_dir, reports_dir())
    except (ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    typer.echo(f"レポート生成: {out}")


scenario_app = typer.Typer(help="業務シナリオ（T8〜T14）の一覧・デモ・実行")
app.add_typer(scenario_app, name="scenario")


@scenario_app.command("list")
def scenario_list() -> None:
    """登録済みシナリオの一覧（Tier・仮説・条件・ステータス）。"""
    from orx.exp import scenario as scn

    typer.echo(f"{'ID':<4}{'Suite':<6}{'Tier':<5}{'仮説':<16}{'状態':<12}名称")
    for spec in scn.all_specs():
        m = spec.meta
        typer.echo(
            f"{m.id:<4}{m.suite:<6}{m.tier:<5}{','.join(m.hypotheses):<16}"
            f"{m.status:<12}{m.title}"
        )


@scenario_app.command("demo")
def scenario_demo(
    scenario_id: str = typer.Argument(..., help="シナリオID（例 s1）"),
) -> None:
    """シナリオをオフラインでエンドツーエンド実行し、レポートを印字する。"""
    from orx.exp import scenario as scn

    try:
        spec = scn.get(scenario_id)
    except ValueError as exc:
        _fail(str(exc))
        return
    typer.echo(f"シナリオ {scenario_id} デモ実行中（stub/offline）…")
    result, report_md = spec.demo(runs_root(), typer.echo)
    typer.echo("")
    typer.echo(report_md)
    failures = [k for k, v in result.get("falsification", {}).items() if not v]
    if failures:
        _fail(f"失敗予言の検証が未達: {failures}")
    typer.secho(f"OK: シナリオ {scenario_id} 反証テスト green", fg=typer.colors.GREEN)


@scenario_app.command("milestone")
def scenario_milestone(
    name: str = typer.Argument("M-Scenario-A", help="マイルストーン名（既定: M-Scenario-A）"),
    tier: str = typer.Option("a", "--tier", help="対象: a=Tier A(S1,S2,S6) / all=全7シナリオ"),
) -> None:
    """シナリオ×全条件の比較レポートを生成する（既定: Tier A = M-Scenario-A）。"""
    from orx.exp import scenario as scn

    targets = {"a": scn.TIER_A, "all": scn.TIER_ALL}
    if tier not in targets:
        _fail(f"未知の tier {tier!r}（a / all）")
        return
    try:
        _runs, milestone = scn.run_milestone(targets[tier], runs_root(), progress=typer.echo)
    except (ConfigError, ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    md = scn.render_milestone(name, milestone)
    out_dir = reports_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"milestone-{name}.md"
    out_path.write_text(md, encoding="utf-8")
    typer.echo("")
    typer.echo(md)
    all_green = all(
        all(milestone["results"][sid].get("falsification", {}).values())
        for sid in milestone["scenarios"]
    )
    typer.echo(f"レポート: {out_path}")
    if not all_green:
        _fail("一部シナリオの反証予言が未達")
    typer.secho(f"OK: {name} 全シナリオ反証 green", fg=typer.colors.GREEN)


@scenario_app.command("run")
def scenario_run(
    experiment_config: Path = typer.Argument(
        ..., help="シナリオ実験コンフィグ (configs/experiments/s{n}_*.yaml)"
    ),
) -> None:
    """シナリオ実験（条件×シード×掃引）を実行し、結果とレポートを出力する。"""
    from orx.exp import scenario as scn

    try:
        config = scn.load_scenario_experiment(experiment_config)
        typer.echo(
            f"シナリオ {config.scenario}: conditions={config.conditions} "
            f"seeds={len(config.seeds)}"
        )
        exp_dir, result = scn.run_experiment(config, runs_root(), progress=typer.echo)
        report_path = scn.write_report(exp_dir, reports_dir())
    except (ConfigError, ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    typer.echo("")
    for line in scn.get(config.scenario).summarize(result):
        typer.echo(line)
    typer.echo(f"結果: {exp_dir / 'results.json'}")
    typer.echo(f"レポート: {report_path}")


@app.command()
def onboard(
    vendor_schema: Path = typer.Argument(
        ..., help="ベンダースキーマ定義 (configs/robots/*.yaml: schema名＋サンプル)"
    ),
    mode: str = typer.Option("heuristic", help="マッピング生成: heuristic / llm"),
    out_dir: Path | None = typer.Option(
        None, help="マッピング案の出力先（既定: ontology/mappings/proposals/）"
    ),
) -> None:
    """新ロボットのオンボーディング: マッピング案の生成→検証→レビュー差分（T5）。

    生成された案は人手レビュー用に保存され、承認後に ontology/mappings/ へ
    移して統合が完了する。llm モードは OPENAI_API_KEY と事前のコスト承認が必要。
    """
    import json as _json

    import yaml as _yaml

    from orx.common.providers import make_llm_client
    from orx.exp.suites.t5 import (
        heuristic_infer,
        llm_infer,
        mapping_to_yaml,
        validate_mapping,
    )

    try:
        data = _yaml.safe_load(vendor_schema.read_text(encoding="utf-8"))
        schema_name = data["schema"]
        samples = data["samples"]
        if mode == "heuristic":
            mapping = heuristic_infer(schema_name, samples)
        elif mode == "llm":
            from orx.common.providers import ProviderConfig

            provider = ProviderConfig(mode="openai", **data.get("provider", {}))
            mapping = llm_infer(schema_name, samples, make_llm_client(provider))
        else:
            _fail(f"未知のモード {mode!r}（heuristic / llm）")
            return
        errors = validate_mapping(mapping, schema_name, samples)
        proposal = mapping_to_yaml(mapping)
    except (KeyError, ValueError, OSError, _json.JSONDecodeError) as exc:
        _fail(str(exc))
        return
    target = (out_dir or (repo_root() / "ontology" / "mappings" / "proposals"))
    target.mkdir(parents=True, exist_ok=True)
    out_path = target / f"{schema_name}.yaml"
    out_path.write_text(proposal, encoding="utf-8")
    typer.echo(f"マッピング案: {out_path}")
    typer.echo(proposal)
    if errors:
        typer.secho("検証エラー（人手修正が必要）:", fg=typer.colors.YELLOW)
        for e in errors:
            typer.echo(f"  - {e}")
        raise typer.Exit(code=1)
    typer.secho(
        "検証OK。レビュー後 ontology/mappings/ へ移動して統合完了。",
        fg=typer.colors.GREEN,
    )


@app.command()
def cq(
    run_id: str | None = typer.Option(
        None, help="評価対象 run（省略時は正準デモ世界を新規記録して評価）"
    ),
) -> None:
    """コンピテンシー質問の回帰を実行し pass/fail 表を表示する。"""
    from orx.exp.cq import run_all_cqs
    from orx.exp.episode import record_episode

    try:
        if run_id is None:
            world = load_config(repo_root() / DEMO_WORLD, WorldConfig)
            config = RunConfig(world=world, duration_s=12.0, root_seed=7)
            actual_id, _ = record_episode(config, runs_root(), run_id="cq-canonical")
            run_dir = runs_root() / actual_id
        else:
            run_dir = runs_root() / run_id
            if not run_dir.exists():
                _fail(f"run が見つかりません: {run_dir}")
        results = run_all_cqs(run_dir)
    except (ConfigError, ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    typer.echo(f"{'CQ':<28} {'結果':<6} 詳細")
    failures = 0
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        color = typer.colors.GREEN if r.passed else typer.colors.RED
        detail = "" if r.passed else f"got={r.got} expected={r.expected}"
        typer.secho(f"{r.cq_id:<28} {status:<6} {detail}", fg=color)
        failures += 0 if r.passed else 1
    if failures:
        _fail(f"{failures}/{len(results)} 件のCQが失敗")
    typer.secho(f"全 {len(results)} 件のCQに合格", fg=typer.colors.GREEN)
