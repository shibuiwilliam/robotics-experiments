"""薄いラッパー。ロジックは `gtwm.eval.experiments.exp09` / `gtwm.eval.runner` に置く。"""

from __future__ import annotations

import datetime
import sys

from gtwm.eval.experiments.exp09 import measure
from gtwm.eval.runner import generate_report, run
from gtwm.utils.config import load_config, to_container

EXP_ID = "EXP-09"
CONFIG_PATH = "experiments/EXP-09/config.yaml"
PURPOSE = (
    "拠点間で予測を安全に（ODRLポリシー強制下で）交換でき、潜在共有の漏洩が小さいかを検証する（H8）"
)
_CRITERIA_KEYS = ("ece", "reconstruction_ssim", "reid_top1_vs_chance")


def main(run_timestamp: str) -> None:
    config = load_config(CONFIG_PATH)
    all_criteria = to_container(load_config("experiments/criteria.yaml"))
    criteria = {k: v for k, v in all_criteria["H8"]["metrics"].items() if k in _CRITERIA_KEYS}

    result = run(EXP_ID, config, measure, run_timestamp)
    generate_report(
        EXP_ID,
        PURPOSE,
        CONFIG_PATH,
        result,
        criteria,
        full_run_note=(
            "本実行には ADR-0002 のコネクタを実際に Docker (`make up-p2`) 越しに使う構成への"
            "切替、より多くのエピソード・フレームでの攻撃者モデル学習、確信度を伴う交換"
            "スキーマへの拡張が必要。"
        ),
    )
    print(f"完了: {result.run_dir}")


if __name__ == "__main__":
    ts = sys.argv[1] if len(sys.argv) > 1 else datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    main(ts)
