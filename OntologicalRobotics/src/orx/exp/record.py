"""C9/C10 — シナリオ実験の記録永続化ヘルパ（IMPROVEMENT.md D-3）。

「アーカイブされた run ディレクトリだけからリプレイ（再採点）を再実行できる」ことが目標
（PROJECT.md §5.2 不変条件6・§7.2）。エピソード入力モデルを持つ suite（s2/s4/s6/s7）は
生成した Episode をここで JSON 永続化する。エピソードが world+seed から決定的に導出される
suite（s3/s5/s8）は、`orx.exp.scenario.run_experiment` が保存するコンフィグアーカイブ
（experiment_config.json / world_config.yaml）＋ git_commit が記録に相当する
（scheme="deterministic-regeneration"）。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

EPISODES_DIR = "episodes"


def episode_path(
    exp_dir: Path, seed: int, knob: str | None = None, value: float | None = None
) -> Path:
    """S1 の命名（`{knob}-{value}-seed{seed}`）に整合したエピソード保存先。"""
    label = f"{knob}-{value}-seed{seed}" if knob is not None else f"main-seed{seed}"
    return exp_dir / EPISODES_DIR / f"{label}.json"


def persist_episode(
    exp_dir: Path | None,
    episode: BaseModel,
    seed: int,
    knob: str | None = None,
    value: float | None = None,
) -> None:
    """生成済みエピソード入力を run ディレクトリ配下へ JSON 永続化する。

    exp_dir=None（テスト等の記録不要パス）では何もしない。書込は生成点ごとに1回・
    実験オーケストレーション層で行い、物理ステップループには入らない（不変条件4）。
    """
    if exp_dir is None:
        return
    path = episode_path(exp_dir, seed, knob, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(episode.model_dump_json(indent=2) + "\n", encoding="utf-8")


def load_episode(path: Path, model: type[BaseModel]) -> BaseModel:
    """永続化済みエピソードを型付きで復元する（アーカイブ単独リプレイの入口）。"""
    return model.model_validate(json.loads(path.read_text(encoding="utf-8")))
