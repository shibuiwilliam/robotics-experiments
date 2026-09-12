"""薄いラッパー。ロジックは `gtwm.eval.experiments.exp06` / `gtwm.eval.runner` に置く。"""

from __future__ import annotations

import datetime
import sys

from gtwm.eval.experiments.exp06 import measure
from gtwm.eval.runner import generate_report, run
from gtwm.utils.config import load_config, to_container

EXP_ID = "EXP-06"
CONFIG_PATH = "experiments/EXP-06/config.yaml"
PURPOSE = "オントロジー制約（進入禁止ゾーン）を計画のハード制約として保証できるかを検証する（H5）"
_CRITERIA_KEYS = ("violations_with_shield", "throughput_loss")


def main(run_timestamp: str) -> None:
    config = load_config(CONFIG_PATH)
    all_criteria = to_container(load_config("experiments/criteria.yaml"))
    criteria = {k: v for k, v in all_criteria["H5"]["metrics"].items() if k in _CRITERIA_KEYS}

    result = run(EXP_ID, config, measure, run_timestamp)
    generate_report(
        EXP_ID,
        PURPOSE,
        CONFIG_PATH,
        result,
        criteria,
        full_run_note=(
            "本実行には poc_plan.md 6.2 通りの100タスク・P0模擬AGVでの実行、"
            "危険物隣接・容量制約シールドへの拡張が必要（config.yaml の full_run 節を参照）。"
        ),
    )
    print(f"完了: {result.run_dir}")


if __name__ == "__main__":
    ts = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    main(ts)
