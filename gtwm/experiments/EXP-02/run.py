"""薄いラッパー。ロジックは `gtwm.eval.experiments.exp02` / `gtwm.eval.runner` に置く。"""

from __future__ import annotations

import datetime
import sys

from gtwm.eval.experiments.exp02 import measure
from gtwm.eval.runner import generate_report, run
from gtwm.utils.config import load_config, to_container

EXP_ID = "EXP-02"
CONFIG_PATH = "experiments/EXP-02/config.yaml"
PURPOSE = "遮蔽下でもWM予測位置を使うことで個体の同一性を維持できるかを検証する（H1）"
_CRITERIA_KEYS = ("id_switch_rate",)


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
            "本実行には P0 の棚裏通過・積み重ね・作業者隠蔽シナリオ200回相当の生成が"
            "必要（config.yaml の full_run 節を参照）。"
        ),
    )
    print(f"完了: {result.run_dir}")


if __name__ == "__main__":
    ts = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    main(ts)
