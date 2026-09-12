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


@kg_app.callback(invoke_without_command=True)
def kg_main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _not_implemented("kg")


@wm_app.callback(invoke_without_command=True)
def wm_main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _not_implemented("wm")


@ground_app.callback(invoke_without_command=True)
def ground_main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _not_implemented("ground")


@exp_app.callback(invoke_without_command=True)
def exp_main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _not_implemented("exp")


@whatif_app.callback(invoke_without_command=True)
def whatif_main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _not_implemented("whatif")


@llm_app.callback(invoke_without_command=True)
def llm_main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _not_implemented("llm")


if __name__ == "__main__":
    app()
