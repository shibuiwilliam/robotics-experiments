"""指標の書き込み配線の棚卸し（M8-15）。

25 指標を「配線済み」と「未配線（理由つき）」に分け、**コードの実際の呼び出しと突き合わせる**。
配線を足したのに分類を直し忘れる、または分類だけ直して配線が無い、のどちらでも落ちる。
未配線の指標が「忘れられている」のではなく「計算する場所が運用に無い」ことを、理由で固定する。
"""

from __future__ import annotations

import re
from pathlib import Path

from sbp.core.metrics import METRICS

ROOT = Path(__file__).resolve().parents[2]

WIRED = {
    "update_latency": "belief-worker",
    "event_log_lag": "belief-worker",
    "contrib_observed": "belief-worker",
    "contrib_record": "belief-worker",
    "contrib_plan": "belief-worker",
    "belief_entropy": "belief-worker",
    "lifting_delay": "bridge-worker",
    "discrepancies_raised": "reconcile-worker",
    "suppression_rate": "reconcile-worker",
    "cdc_lag": "ingest-worker",
    "mapping_violations": "ingest-worker",
    "resolution_time": "learning-worker（解決の適用）",
    "reliability_updates": "learning-worker（解決の適用）",
    "learning_yield": "learning-worker（解決の適用）",
    "latent_shift": "encode ジョブ（WM の health）",
    "pred_error": "encode ジョブ（WM の health）",
    "inference_latency": "encode ジョブ",
    "probe_auroc": "train-probe ジョブ",
    "probe_ece": "train-probe ジョブ",
    "align_residual": "train-probe ジョブ（評価枠の整合残差）",
}

UNWIRED = {
    "forecast_coverage": "予測サービスの入口が無い（H-24）",
    "forecast_crps": "予測サービスの入口が無い（H-24）",
    "regrounding_duration": "再接地の `run(plan)` が無い（H-24）",
    "identity_bind_rate": "同一性束縛の運用経路が無い（H-24）",
    "identity_revoke_rate": "同一性束縛の運用経路が無い（H-24）",
}

_SOURCES = (
    list((ROOT / "services").glob("*.py"))
    + list((ROOT / "scripts").glob("*.py"))
    + [ROOT / "src/sbp/worldmodel/encoding.py"]
)


def _referenced(key: str) -> bool:
    pattern = re.compile(rf"""["']{re.escape(key)}["']""")
    return any(pattern.search(path.read_text(encoding="utf-8")) for path in _SOURCES)


def test_every_metric_is_classified_exactly_once() -> None:
    keys = {m.key for m in METRICS.values()}
    assert set(WIRED) | set(UNWIRED) == keys
    assert not set(WIRED) & set(UNWIRED)


def test_wired_metrics_are_written_by_some_operating_code() -> None:
    missing = [key for key in WIRED if not _referenced(key)]
    assert not missing, f"分類は配線済みだが、書く運用コードが無い: {missing}"


def test_unwired_metrics_really_have_no_writer() -> None:
    wired_now = [key for key in UNWIRED if _referenced(key)]
    assert not wired_now, f"書く運用コードができた。UNWIRED から WIRED へ移す: {wired_now}"
