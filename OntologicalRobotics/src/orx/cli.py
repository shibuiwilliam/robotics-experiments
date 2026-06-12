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
    """run/実験のMarkdownレポートを reports/ に再生成する。"""
    from orx.exp.report import write_run_report
    from orx.exp.runner import RESULTS, write_experiment_report

    target_dir = runs_root() / target_id
    if not target_dir.exists():
        _fail(f"run/exp が見つかりません: {target_dir}")
    try:
        if (target_dir / RESULTS).exists():
            out = write_experiment_report(target_dir, reports_dir())
        else:
            out = write_run_report(target_dir, reports_dir())
    except (ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    typer.echo(f"レポート生成: {out}")


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
