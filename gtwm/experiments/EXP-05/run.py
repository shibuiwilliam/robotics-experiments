"""薄いラッパー。ロジックは `gtwm.eval.experiments.exp05` / `gtwm.eval.runner` に置く。"""

from __future__ import annotations

import datetime
import sys

from gtwm.eval.experiments.exp05 import measure
from gtwm.eval.runner import generate_report, run
from gtwm.utils.config import load_config, to_container

EXP_ID = "EXP-05"
CONFIG_PATH = "experiments/EXP-05/config.yaml"
PURPOSE = "物理と記録の乖離を自動検知できるかを検証する（H4、poc_plan.md 3.1）"
_CRITERIA_KEYS = ("detection_rate", "false_alarms_per_day", "detection_latency_median_s")


def main(run_timestamp: str) -> None:
    config = load_config(CONFIG_PATH)
    all_criteria = to_container(load_config("experiments/criteria.yaml"))
    criteria = {k: v for k, v in all_criteria["H4"]["metrics"].items() if k in _CRITERIA_KEYS}

    result = run(EXP_ID, config, measure, run_timestamp)
    generate_report(
        EXP_ID,
        PURPOSE,
        CONFIG_PATH,
        result,
        criteria,
        full_run_note=(
            "本実行には2週間分のP1運転ログ（乖離5型×10件以上、config.yaml の "
            "full_run 節を参照）が必要。所要時間は `gtwm sim gen` の実測生成速度から按分する。"
        ),
    )
    print(f"完了: {result.run_dir}")


if __name__ == "__main__":
    ts = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    main(ts)
