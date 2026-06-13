"""S1 メタモルフィック・テスト（H-4 / PROJECT.md §7.4）。

バーコード・ロットIDを意味保存リネーム（順序保存サフィックス）しても、決定的経路の
採点（per-condition の completion/F1/location/success）が不変であることを確認する。
不変＝系がオントロジー構造に依拠し、文字列表層一致に依存していない証拠。
"""

from pathlib import Path

import pytest

from orx.common.config import WorldConfig, load_config
from orx.common.paths import repo_root
from orx.exp.metamorphic import rename_world_identifiers
from orx.exp.suites.s1_lot_recall import runner

SEEDS = [101, 102, 103]


def _scores(world: WorldConfig, runs_root: Path) -> dict:
    out: dict[int, dict] = {}
    for seed in SEEDS:
        scores, _lot = runner._eval_seed(
            world, seed, runs_root, None, 0.0, duration_s=18.0, claim_ttl_s=8.0
        )
        out[seed] = {c: scores[c].model_dump() for c in runner.CONDITIONS}
    return out


def test_scores_invariant_under_identifier_rename(tmp_path: Path) -> None:
    base = load_config(repo_root() / runner.WORLD, WorldConfig)
    renamed = rename_world_identifiers(base, suffix="Z9")

    base_scores = _scores(base, tmp_path / "base")
    renamed_scores = _scores(renamed, tmp_path / "renamed")

    assert base_scores == renamed_scores, (
        "識別子リネームで採点が変化した＝表層文字列依存の疑い（構造依拠でない）"
    )


def test_rename_actually_changed_identifiers() -> None:
    """変換が実際に識別子を変えている（テストが空振りでない）ことの確認。"""
    base = load_config(repo_root() / runner.WORLD, WorldConfig)
    renamed = rename_world_identifiers(base, suffix="Z9")
    base_bc = {b.barcode for b in base.boxes if b.barcode}
    renamed_bc = {b.barcode for b in renamed.boxes if b.barcode}
    assert base_bc and renamed_bc and base_bc.isdisjoint(renamed_bc)
    assert all(b.barcode.endswith("Z9") for b in renamed.boxes if b.barcode)


def test_empty_suffix_rejected() -> None:
    base = load_config(repo_root() / runner.WORLD, WorldConfig)
    with pytest.raises(ValueError, match="suffix"):
        rename_world_identifiers(base, suffix="")
