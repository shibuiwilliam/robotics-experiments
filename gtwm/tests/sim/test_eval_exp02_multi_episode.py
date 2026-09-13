"""`eval/experiments/exp02.measure()` の複数エピソード集計を実データ（`make sim-smoke`
生成物）で検証する。実ファイル読込を伴うため tests/unit ではなく tests/sim に置く
（.claude/rules 「tests/unit は1件1秒以内、ネットワーク・Docker・GPU 不要」）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from omegaconf import OmegaConf

from gtwm.eval.experiments.exp02 import measure

pytestmark = pytest.mark.sim

_SMOKE_EPISODE_DIR = (
    Path(__file__).resolve().parents[2] / "data" / "sim" / "smoke" / "ep_0000_seed0"
)


@pytest.mark.skipif(not _SMOKE_EPISODE_DIR.exists(), reason="make sim-smoke 未実行")
def test_measure_with_multiple_episode_ids_pools_across_them() -> None:
    """`config.episode_ids`（複数）を指定すると、単一 episode_id のみの場合と比べて
    n_frames が倍増し（同じスモークエピソードを2回渡すため）、n_episodes が
    記録されることを実データで確認する（新しいエピソードを別途生成しなくても、
    同一エピソードを2回渡すことでループ・プール処理そのものは検証できる）。"""
    config_single = OmegaConf.create({"set_name": "smoke", "episode_id": "ep_0000_seed0"})
    single = measure(config_single, seed=0)

    config_multi = OmegaConf.create(
        {"set_name": "smoke", "episode_ids": ["ep_0000_seed0", "ep_0000_seed0"]}
    )
    multi = measure(config_multi, seed=0)

    assert multi["n_episodes"] == 2
    assert multi["n_frames"] == single["n_frames"] * 2
    # 同一エピソードを2回集計しているだけなので、プール後の値は単一回と一致するはず。
    assert multi["id_switch_rate"] == pytest.approx(single["id_switch_rate"])
    assert multi["idf1"] == pytest.approx(single["idf1"])
