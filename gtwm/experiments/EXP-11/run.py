"""薄いラッパー。ロジックは `gtwm.eval.experiments.exp11` / `gtwm.eval.runner` に置く。"""

from __future__ import annotations

import datetime
import sys

from gtwm.eval.experiments.exp11 import measure
from gtwm.eval.runner import generate_report, run
from gtwm.utils.config import load_config, to_container

EXP_ID = "EXP-11"
CONFIG_PATH = "experiments/EXP-11/config.yaml"
PURPOSE = "非機能要件（E2E遅延、稼働率、コスト）を満たすかを検証する（N1、poc_plan.md 3.1）"
_CRITERIA_KEYS = ("e2e_latency_s", "availability", "monthly_cost_per_zone")


def main(run_timestamp: str) -> None:
    config = load_config(CONFIG_PATH)
    all_criteria = to_container(load_config("experiments/criteria.yaml"))
    criteria_raw = {k: v for k, v in all_criteria["N1"]["metrics"].items() if k in _CRITERIA_KEYS}
    # measure() が返すキー名（e2e_latency_p50_s）と criteria.yaml のキー名
    # （e2e_latency_s）が異なるため、report.md の結果表・判定用に読み替える。
    criteria = dict(criteria_raw)
    if "e2e_latency_s" in criteria:
        criteria["e2e_latency_p50_s"] = criteria.pop("e2e_latency_s")

    result = run(EXP_ID, config, measure, run_timestamp)
    generate_report(
        EXP_ID,
        PURPOSE,
        CONFIG_PATH,
        result,
        criteria,
        full_run_note=(
            "本実行には継続計測（デプロイ後、観測→信念KG更新の実時間ログ）と、"
            "最低1営業日以上の連続稼働実績（config.yaml の full_run 節を参照）が必要。"
        ),
    )
    print(f"完了: {result.run_dir}")


if __name__ == "__main__":
    ts = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    main(ts)
