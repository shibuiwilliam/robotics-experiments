"""ORX CLI エントリポイント（typer）。

コマンド本体は各フェーズで実装する（docs/IMPLEMENTATION_PLAN.md 参照）。
現時点では足場のみ — サブコマンド未実装。
"""

import typer

app = typer.Typer(
    name="orx",
    help="ORX (Ontological Robotics eXperiments) — オントロジー駆動ロボティクス検証基盤。",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """ORX CLI。`orx <command> --help` で各コマンドの説明を表示する。"""


@app.command()
def version() -> None:
    """インストールされている ORX のバージョンを表示する。"""
    from orx import __version__

    typer.echo(f"orx {__version__}")
