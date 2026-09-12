"""gtwm CLI エントリポイント。"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from gtwm.doctor import run_all_checks

app = typer.Typer(help="世界モデル × オントロジー 知的データ空間 PoC CLI")
console = Console()

sim_app = typer.Typer(help="シミュレーション（未実装）")
kg_app = typer.Typer(help="知識グラフ（未実装）")
wm_app = typer.Typer(help="世界モデル（未実装）")
ground_app = typer.Typer(help="接地層（未実装）")
exp_app = typer.Typer(help="実験ランナー（未実装）")
whatif_app = typer.Typer(help="WHAT-IF クエリ（未実装）")
llm_app = typer.Typer(help="LLM クライアント（未実装）")

app.add_typer(sim_app, name="sim")
app.add_typer(kg_app, name="kg")
app.add_typer(wm_app, name="wm")
app.add_typer(ground_app, name="ground")
app.add_typer(exp_app, name="exp")
app.add_typer(whatif_app, name="whatif")
app.add_typer(llm_app, name="llm")


def _not_implemented(component: str) -> None:
    console.print(f"[red]未実装[/red]: {component} はまだ実装されていません。")
    raise typer.Exit(code=1)


@app.command()
def doctor() -> None:
    """実行環境（Python / MPS / MuJoCo / ffmpeg / Docker / .env）を検査する。"""
    results = run_all_checks()

    table = Table(title="gtwm doctor")
    table.add_column("項目")
    table.add_column("結果")
    table.add_column("詳細")

    for r in results:
        status = "[green]OK[/green]" if r.ok else "[yellow]NG[/yellow]"
        table.add_row(r.name, status, r.detail)

    console.print(table)


@sim_app.callback(invoke_without_command=True)
def sim_main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _not_implemented("sim")


@sim_app.command("gen")
def sim_gen(
    set_name: str = typer.Option(..., "--set", help="出力先セット名（data/sim/<set>/）"),
    episodes: int = typer.Option(1, "--episodes", help="生成するエピソード数"),
    duration: float = typer.Option(30.0, "--duration", help="1エピソードの長さ（秒）"),
    seed: int = typer.Option(0, "--seed", help="開始シード（エピソードごとに +1 される）"),
) -> None:
    """MuJoCo 倉庫シミュレーションでエピソードを生成する。"""
    from gtwm.sim.generate import GenConfig, generate_set

    cfg = GenConfig(set_name=set_name, episodes=episodes, duration_s=duration, seed=seed)
    dirs = generate_set(cfg)
    for d in dirs:
        console.print(f"[green]生成完了[/green]: {d}")


@kg_app.callback(invoke_without_command=True)
def kg_main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _not_implemented("kg")


@kg_app.command("validate")
def kg_validate() -> None:
    """ontology/ の ttl 構文・SHACL 自己整合・queries/*.rq 構文を検査する。"""
    from gtwm.kg.validate_cli import validate_all

    issues = validate_all()
    if not issues:
        console.print("[green]OK[/green]: gt-core.ttl / shapes / queries はすべて検証済み")
        return
    for issue in issues:
        console.print(f"[red]NG[/red] {issue}")
    raise typer.Exit(code=1)


@wm_app.callback(invoke_without_command=True)
def wm_main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _not_implemented("wm")


@wm_app.command("train")
def wm_train(
    config: str = typer.Option("configs/wm/base.yaml", "--config", help="Hydra 設定ファイルのパス"),
) -> None:
    """世界モデルを学習する（`gtwm.wm.train.train`）。"""
    from gtwm.utils.config import load_config
    from gtwm.wm.train import train as run_train

    cfg = load_config(config)
    metrics = run_train(cfg)
    console.print(f"[green]学習完了[/green]: {metrics}")


@wm_app.command("rollout")
def wm_rollout(
    config: str = typer.Option(
        "configs/wm/smoke.yaml", "--config", help="Hydra 設定ファイルのパス"
    ),
    horizon: int = typer.Option(10, "--horizon", help="ロールアウトのステップ数"),
) -> None:
    """学習済みチェックポイントからロールアウトし、潜在誤差を表示する（簡易版）。"""
    import torch

    from gtwm.utils.config import load_config
    from gtwm.utils.device import get_device
    from gtwm.wm.dataset import camera_names, list_episodes
    from gtwm.wm.train import build_modules, encode_sequence

    cfg = load_config(config)
    device = get_device()
    episodes = list_episodes(cfg.train.data_set)
    if not episodes:
        _not_implemented("wm rollout（データが無い: gtwm sim gen で生成してください）")
        return

    from gtwm.utils.paths import repo_root

    modules = build_modules(cfg, device)
    checkpoint_path = repo_root() / cfg.train.checkpoint_dir / "best.pt"
    if checkpoint_path.exists():
        state = torch.load(checkpoint_path, map_location=device)
        modules.slot_module.load_state_dict(state["slot_module"])
        modules.fusion.load_state_dict(state["fusion"])
        modules.dynamics.load_state_dict(state["dynamics"])
        modules.encoder.adapter.load_state_dict(state["adapter"])
        console.print(f"[green]チェックポイント読込[/green]: {checkpoint_path}")
    else:
        console.print("[yellow]チェックポイント無し[/yellow]: 初期化状態でロールアウトします")

    modules.slot_module.eval()
    modules.fusion.eval()
    modules.dynamics.eval()

    cam_names = camera_names(episodes)
    from gtwm.wm.dataset import WMSequenceDataset

    ds = WMSequenceDataset(episodes, seq_len=2, frame_stride=1)
    frames = ds[0].unsqueeze(0).to(device)
    with torch.no_grad():
        fused = encode_sequence(modules, frames, cam_names)
        context = fused[:, 0]
        rollout = modules.dynamics.rollout(context, None, None, horizon)
    console.print(f"[green]ロールアウト完了[/green]: shape={tuple(rollout.shape)}")


@ground_app.callback(invoke_without_command=True)
def ground_main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _not_implemented("ground")


@ground_app.command("run")
def ground_run_cmd(
    episode: str = typer.Option(..., "--episode", help="エピソードID（ディレクトリ名）"),
    set_name: str = typer.Option("smoke", "--set", help="data/sim/<set>/ のセット名"),
    probe_config: str = typer.Option(
        "configs/grounding/probe_train_smoke.yaml", "--probe-config", help="接地層設定"
    ),
) -> None:
    """接地層パイプラインを1エピソードに対して実行し、信念・ε・乖離台帳を生成する。"""
    from gtwm.grounding.ground_run import run_ground

    result = run_ground(episode, set_name, probe_config)
    console.print(
        f"[green]完了[/green]: beliefs={result.n_beliefs} "
        f"ledger_entries={result.n_ledger_entries} -> {result.output_dir}"
    )
    for record in result.epsilon_records:
        console.print(
            f"  epsilon_{record.horizon_s:.0f}s = {record.epsilon:.4f} "
            f"(n={record.n_samples}, decomposition={record.decomposition})"
        )


@exp_app.callback(invoke_without_command=True)
def exp_main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _not_implemented("exp")


@whatif_app.callback(invoke_without_command=True)
def whatif_main(
    ctx: typer.Context,
    query: str = typer.Argument(None, help="WHAT-IF クエリ文字列（付録Cの文法）"),
    episode: str = typer.Option(
        "ep_0000_seed0", "--episode", help="ロールアウトの初期状態に使うエピソードID"
    ),
    set_name: str = typer.Option("wm_smoke", "--set", help="data/sim/<set>/ のセット名"),
    probe_config: str = typer.Option(
        "configs/grounding/probe_train_smoke.yaml", "--probe-config", help="接地層設定"
    ),
) -> None:
    """WHAT-IF クエリを解析・コンパイル・ロールアウトし、KPIの予測を表示する。"""
    if ctx.invoked_subcommand is not None:
        return
    if query is None:
        _not_implemented("whatif（クエリ文字列を引数で渡してください）")
        return

    from gtwm.kg.whatif.compiler import WhatIfCompileError
    from gtwm.kg.whatif.engine import WhatIfUnsupportedVarError, run_whatif
    from gtwm.kg.whatif.parser import WhatIfSyntaxError

    try:
        result = run_whatif(query, episode, set_name, probe_config)
    except (WhatIfSyntaxError, WhatIfCompileError, WhatIfUnsupportedVarError) as exc:
        console.print(f"[red]NG[/red]: {exc}")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"WHAT-IF 結果（model={result.model_version}）")
    table.add_column("var")
    table.add_column("horizon(s)")
    table.add_column("point")
    table.add_column(f"interval({result.interval_prob:.0%})")
    table.add_column("n_rollouts")
    for p in result.predictions:
        table.add_row(
            p.var,
            f"{p.horizon_s:.0f}",
            f"{p.point_estimate:.3f}",
            f"[{p.interval_low:.3f}, {p.interval_high:.3f}]",
            str(p.n_rollouts),
        )
    console.print(table)


@llm_app.callback(invoke_without_command=True)
def llm_main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _not_implemented("llm")


if __name__ == "__main__":
    app()
