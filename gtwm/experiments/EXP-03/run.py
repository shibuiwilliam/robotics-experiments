"""薄いラッパー。ロジックは `gtwm.eval.experiments.exp03` / `gtwm.eval.runner` に置く。"""

from __future__ import annotations

import datetime
import sys

from gtwm.eval.experiments.exp03 import measure
from gtwm.eval.runner import generate_report, run
from gtwm.utils.config import load_config, to_container

EXP_ID = "EXP-03"
CONFIG_PATH = "experiments/EXP-03/config.yaml"
PURPOSE = "記号条件付け（γ）が世界モデルの予測誤差を改善するかを検証する（H2）"
_CRITERIA_KEYS = ("error_improvement_60s", "effective_horizon_ratio")


def main(run_timestamp: str) -> None:
    config = load_config(CONFIG_PATH)
    all_criteria = to_container(load_config("experiments/criteria.yaml"))
    criteria = {k: v for k, v in all_criteria["H2"]["metrics"].items() if k in _CRITERIA_KEYS}

    result = run(EXP_ID, config, measure, run_timestamp)
    generate_report(
        EXP_ID,
        PURPOSE,
        CONFIG_PATH,
        result,
        criteria,
        full_run_note=(
            "本実行には60秒以上先を評価できるエピソード生成（30秒超）が必要。"
            "smokeは1〜8秒先までしか測定できない（config.yaml の full_run 節を参照）。"
        ),
    )
    print(f"完了: {result.run_dir}")


if __name__ == "__main__":
    ts = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    main(ts)
