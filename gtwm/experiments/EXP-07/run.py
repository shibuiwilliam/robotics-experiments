"""薄いラッパー。ロジックは `gtwm.eval.experiments.exp07` / `gtwm.eval.runner` に置く。"""

from __future__ import annotations

import datetime
import sys

from gtwm.eval.experiments.exp07 import measure
from gtwm.eval.runner import generate_report, run
from gtwm.utils.config import load_config, to_container

EXP_ID = "EXP-07"
CONFIG_PATH = "experiments/EXP-07/config.yaml"
PURPOSE = "WHAT-IFの反実仮想予測（介入前の予測KPI）が実測と整合するかを検証する（H6）"
_CRITERIA_KEYS = ("kpi_relative_error", "interval_coverage_90")


def main(run_timestamp: str) -> None:
    config = load_config(CONFIG_PATH)
    all_criteria = to_container(load_config("experiments/criteria.yaml"))
    criteria = {k: v for k, v in all_criteria["H6"]["metrics"].items() if k in _CRITERIA_KEYS}

    result = run(EXP_ID, config, measure, run_timestamp)
    generate_report(
        EXP_ID,
        PURPOSE,
        CONFIG_PATH,
        result,
        criteria,
        full_run_note=(
            "本実行には poc_plan.md 6.2 通りの実サイトでの介入実施（各2回）、"
            "300秒級エピソードでの測定、行動空間のエンティティ×プロパティ意味付け"
            "（ADR候補、config.yaml の full_run 節参照）が必要。"
        ),
    )
    print(f"完了: {result.run_dir}")


if __name__ == "__main__":
    ts = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    main(ts)
