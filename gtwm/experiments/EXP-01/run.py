"""薄いラッパー。ロジックは `gtwm.eval.experiments.exp01` / `gtwm.eval.runner` に置く
（`.claude/rules/experiments.md`：「run.py は run(config) を呼ぶだけ」）。
"""

from __future__ import annotations

import datetime
import sys

from gtwm.eval.experiments.exp01 import measure
from gtwm.eval.runner import generate_report, run
from gtwm.utils.config import load_config, to_container

EXP_ID = "EXP-01"
CONFIG_PATH = "experiments/EXP-01/config.yaml"
PURPOSE = "潜在状態から業務事実（位置・型）を高精度に復号できるかを検証する（H1、poc_plan.md 3.1）"
_CRITERIA_KEYS = ("position_fact_f1", "type_accuracy")


def main(run_timestamp: str) -> None:
    config = load_config(CONFIG_PATH)
    all_criteria = to_container(load_config("experiments/criteria.yaml"))
    criteria = {k: v for k, v in all_criteria["H1"]["metrics"].items() if k in _CRITERIA_KEYS}

    result = run(EXP_ID, config, measure, run_timestamp)
    generate_report(
        EXP_ID,
        PURPOSE,
        CONFIG_PATH,
        result,
        criteria,
        full_run_note=(
            "本実行には P0 の p0_train(5分x50本)+p0_eval(5分x10本) 相当のデータ生成"
            "（config.yaml の full_run 節を参照）が必要。所要時間は `gtwm sim gen` の"
            "実測生成速度（session02: 30秒エピソード約7秒）から按分して見積もる。"
        ),
    )
    print(f"完了: {result.run_dir}")


if __name__ == "__main__":
    ts = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    main(ts)
