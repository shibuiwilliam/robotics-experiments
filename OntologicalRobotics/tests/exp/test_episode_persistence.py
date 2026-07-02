"""エピソード入力の永続化（D-3）の round-trip テスト。

s2/s4/s6/s7 の生成済みエピソードを `episodes/` に JSON 永続化 → 型付き復元 → モデル等価を
検証する。アーカイブされた run ディレクトリ**だけ**から再採点（アーカイブ単独リプレイ）が
成立するための最小保証（PROJECT.md §5.2 不変条件6・§7.2）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from orx.common.config import load_config
from orx.common.paths import repo_root
from orx.exp.record import episode_path, load_episode, persist_episode


def _s2() -> tuple[BaseModel, type[BaseModel]]:
    from orx.exp.suites.s2_allergen import generator
    from orx.exp.suites.s2_allergen.model import S2Episode, S2World

    world = load_config(repo_root() / "configs/world/s2_allergen.yaml", S2World)
    return generator.generate_episode(world, 201), S2Episode


def _s4() -> tuple[BaseModel, type[BaseModel]]:
    from orx.exp.suites.s4_inspection import generator
    from orx.exp.suites.s4_inspection.model import S4Episode, S4World

    world = load_config(repo_root() / "configs/world/s4_inspection.yaml", S4World)
    return generator.generate_episode(world, 401, 0.4), S4Episode


def _s6() -> tuple[BaseModel, type[BaseModel]]:
    from orx.exp.suites.s6_recycling import generator
    from orx.exp.suites.s6_recycling.model import S6Episode, S6World

    world = load_config(repo_root() / "configs/world/s6_recycling.yaml", S6World)
    return generator.generate_episode(world, 601, 0.2), S6Episode


def _s7() -> tuple[BaseModel, type[BaseModel]]:
    from orx.exp.suites.s7_ownership import generator
    from orx.exp.suites.s7_ownership.model import S7Episode, S7World

    world = load_config(repo_root() / "configs/world/s7_ownership.yaml", S7World)
    return generator.generate_episode(world, 701, 0.5), S7Episode


@pytest.mark.parametrize("factory", [_s2, _s4, _s6, _s7], ids=["s2", "s4", "s6", "s7"])
def test_episode_round_trip(tmp_path: Path, factory) -> None:  # noqa: ANN001
    episode, model = factory()
    persist_episode(tmp_path, episode, seed=1, knob="k", value=0.5)
    path = episode_path(tmp_path, seed=1, knob="k", value=0.5)
    assert path.exists()
    restored = load_episode(path, model)
    assert restored == episode  # JSON 経由で完全等価（アーカイブ単独リプレイの前提）


def test_persist_episode_noop_without_dir(tmp_path: Path) -> None:
    """record_dir=None（テスト等の記録不要パス）では何も書かない。"""
    episode, _model = _s6()
    persist_episode(None, episode, seed=1)
    assert list(tmp_path.iterdir()) == []


def test_main_label_without_knob(tmp_path: Path) -> None:
    episode, model = _s2()
    persist_episode(tmp_path, episode, seed=201)
    path = tmp_path / "episodes" / "main-seed201.json"
    assert path.exists()
    assert load_episode(path, model) == episode
