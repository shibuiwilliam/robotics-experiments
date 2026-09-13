"""薄いラッパー。ロジックは `gtwm.eval.experiments.exp08` / `gtwm.eval.runner` に置く。"""

from __future__ import annotations

import datetime
import sys

from gtwm.eval.experiments.exp08 import measure
from gtwm.eval.runner import generate_report, run
from gtwm.utils.config import load_config, to_container

EXP_ID = "EXP-08"
CONFIG_PATH = "experiments/EXP-08/config.yaml"
PURPOSE = (
    "予測残差から未知の概念（オントロジー未登録の荷姿・工程・運用）を発見できるかを検証する（H7）"
)
_CRITERIA_KEYS = ("injected_concept_top5_hit",)


def main(run_timestamp: str) -> None:
    config = load_config(CONFIG_PATH)
    all_criteria = to_container(load_config("experiments/criteria.yaml"))
    criteria = {k: v for k, v in all_criteria["H7"]["metrics"].items() if k in _CRITERIA_KEYS}

    result = run(EXP_ID, config, measure, run_timestamp)
    generate_report(
        EXP_ID,
        PURPOSE,
        CONFIG_PATH,
        result,
        criteria,
        full_run_note=(
            "本実行には poc_plan.md 6.2 通りの運用期間相当のデータ量、実LLMプロバイダへの"
            "切替（configs/llm.yaml）、そして評価者3名による候補判定（人手、"
            "docs/results/EXP-08_protocol.md）が必要。"
        ),
    )
    print(f"完了: {result.run_dir}")


if __name__ == "__main__":
    ts = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    main(ts)
