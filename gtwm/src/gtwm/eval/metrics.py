"""指標の実装（C14）。関数名・定義は `.claude/rules/experiments.md`「指標」節と
docs/poc_plan.md 付録A に一致させる。閾値・重みは呼び出し側が
`configs/grounding/epsilon.yaml` / `experiments/criteria.yaml` から渡す
（このモジュールに数値をハードコードしない）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import numpy as np


def position_mismatch(
    pred_zone: str,
    ref_zone: str,
    adjacent_zone_pairs: set[frozenset[str]] | None = None,
    adjacent_penalty: float = 0.5,
) -> float:
    """位置事実の不一致率（付録A）：ゾーン一致=0、隣接ゾーン=`adjacent_penalty`、それ以外=1。

    例：position_mismatch("gt:Zone_A", "gt:Zone_A") == 0.0
        position_mismatch("gt:Zone_A", "gt:Zone_B", {frozenset({"gt:Zone_A","gt:Zone_B"})}) == 0.5
        position_mismatch("gt:Zone_A", "gt:Zone_C") == 1.0
    """
    if pred_zone == ref_zone:
        return 0.0
    if adjacent_zone_pairs and frozenset({pred_zone, ref_zone}) in adjacent_zone_pairs:
        return adjacent_penalty
    return 1.0


def fact_distance_d(mismatches: dict[str, float], weights: dict[str, float]) -> float:
    """事実集合間の距離 d = Σ_k w_k・m_k（付録A）。

    `mismatches` は事実種別（position/state/relations/type）ごとの不一致率 m_k ∈ [0,1]。
    `weights` は `configs/grounding/epsilon.yaml` の `d_weights` と一致させる（既定：
    position 0.4, state 0.3, relations 0.2, type 0.1）。存在(existence)は前提条件として
    別集計のため対象外。

    例：fact_distance_d({"position": 1.0, "state": 0.0}, {"position": 0.4, "state": 0.3})
        == 0.4 * 1.0 + 0.3 * 0.0 == 0.4
    """
    keys = set(mismatches) & set(weights)
    return sum(weights[k] * mismatches[k] for k in keys)


def epsilon_h(d_values: Sequence[float]) -> float:
    """ε_h = (1/N) Σ_t d(α(f^h(z_t)), F^h(α(z_t)))（付録A）。

    `d_values` は評価時刻 t ごとの d の値の列。

    例：epsilon_h([0.0, 0.4, 0.2]) == 0.2
    """
    if not d_values:
        raise ValueError("d_values が空です（評価時刻数 N=0）")
    return sum(d_values) / len(d_values)


def grounding_prf(
    preds: Sequence[int], labels: Sequence[int], n_classes: int | None = None
) -> dict[str, object]:
    """事実種別ごとの精度・再現率・F1（付録A「接地精度」）。

    クラスごとの precision/recall/f1 と、マクロ平均を返す。

    例：grounding_prf([0, 1, 1], [0, 1, 0])
        -> クラス0: precision=0.5, recall=1.0, f1=2/3
           クラス1: precision=1.0, recall=0.5, f1=2/3
           macro_f1 == 2/3
    """
    if len(preds) != len(labels):
        raise ValueError("preds と labels は同じ長さである必要があります")
    classes = sorted(set(labels) | set(preds)) if n_classes is None else list(range(n_classes))
    per_class: dict[int, dict[str, float]] = {}
    f1s = []
    for c in classes:
        tp = sum(1 for p, y in zip(preds, labels, strict=True) if p == c and y == c)
        fp = sum(1 for p, y in zip(preds, labels, strict=True) if p == c and y != c)
        fn = sum(1 for p, y in zip(preds, labels, strict=True) if p != c and y == c)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        per_class[c] = {"precision": precision, "recall": recall, "f1": f1}
        f1s.append(f1)
    accuracy = sum(1 for p, y in zip(preds, labels, strict=True) if p == y) / len(labels)
    return {
        "per_class": per_class,
        "macro_f1": sum(f1s) / len(f1s) if f1s else 0.0,
        "accuracy": accuracy,
    }


def id_switch_rate(n_switches: int, n_objects: int, duration_hours: float) -> float:
    """ID切替率（付録A）：単位時間・物体あたりのID切替回数（回/物体・時間）。

    例：id_switch_rate(n_switches=1, n_objects=2, duration_hours=0.5) == 1.0
    """
    if n_objects <= 0 or duration_hours <= 0:
        raise ValueError("n_objects と duration_hours は正である必要があります")
    return n_switches / (n_objects * duration_hours)


def idf1(idtp: int, idfp: int, idfn: int) -> float:
    """IDF1（付録A「接地精度」、多物体追跡の標準定義）：
    IDF1 = 2*IDTP / (2*IDTP + IDFP + IDFN)。

    例：idf1(idtp=8, idfp=2, idfn=2) == 16/20 == 0.8
    """
    denom = 2 * idtp + idfp + idfn
    if denom == 0:
        return 1.0
    return 2 * idtp / denom


def count_id_switches(track_entity_sequence: Sequence[str | None]) -> int:
    """1トラック（永続ID、例：スロット index）が時間軸に沿って指す真値個体IDの列から、
    ID切替回数を数える（付録A「ID切替率」）。

    `None` はそのフレームで対応する個体が見つからなかった（未マッチ／遮蔽で保留中）ことを
    表し、直前の非 `None` 値と比較する対象からは除外する（保持からの復帰は切替ではない）。

    例：count_id_switches(["a", "a", None, "a", "b"]) == 1（a→None→a は継続、a→b のみ切替）
    """
    switches = 0
    last: str | None = None
    for entity_id in track_entity_sequence:
        if entity_id is None:
            continue
        if last is not None and entity_id != last:
            switches += 1
        last = entity_id
    return switches


def id_counts_from_matches(
    matched_pairs_per_frame: Sequence[Sequence[tuple[str, str]]],
    n_unmatched_preds_per_frame: Sequence[int] | None = None,
    n_unmatched_gts_per_frame: Sequence[int] | None = None,
) -> tuple[int, int, int]:
    """フレームごとの、既に空間的対応付け済みの (予測トラックID, 真値ID) ペア列から
    IDTP/IDFP/IDFN を数える（付録A「接地精度」、多物体追跡評価の簡易版）。

    ペアは呼び出し側（例：`grounding/identity.py` のハンガリアン割当）が既に解決済みの
    ものを渡す想定（このモジュール自身は空間マッチングを行わない）。各予測トラックIDには、
    全フレームを通じて最も多くペアになった真値IDを多数決で割り当てる（identity mapping）。
    このマッピングと一致するペアはIDTP、一致しないペアはIDFP+IDFN（そのトラックは
    間違った個体を指しており、本来の個体は見逃されている）として数える。
    `n_unmatched_preds_per_frame` / `n_unmatched_gts_per_frame` は対応相手が
    見つからなかった予測/真値のフレームごとの件数（追加のIDFP/IDFN）。

    例：matched_pairs_per_frame=[[("t1","gt1")], [("t1","gt1")], [("t1","gt1")]] なら
        (idtp, idfp, idfn) == (3, 0, 0)。
    """
    n_frames = len(matched_pairs_per_frame)
    unmatched_preds = n_unmatched_preds_per_frame or [0] * n_frames
    unmatched_gts = n_unmatched_gts_per_frame or [0] * n_frames
    if len(unmatched_preds) != n_frames or len(unmatched_gts) != n_frames:
        raise ValueError(
            "unmatched の各列は matched_pairs_per_frame と同じフレーム数である必要があります"
        )

    co_occurrence: dict[str, dict[str, int]] = {}
    for pairs in matched_pairs_per_frame:
        for pred_id, gt_id in pairs:
            bucket = co_occurrence.setdefault(pred_id, {})
            bucket[gt_id] = bucket.get(gt_id, 0) + 1
    mapping = {p: max(counts, key=lambda g: counts[g]) for p, counts in co_occurrence.items()}

    idtp = idfp = idfn = 0
    for pairs, n_up, n_ug in zip(
        matched_pairs_per_frame, unmatched_preds, unmatched_gts, strict=True
    ):
        for pred_id, gt_id in pairs:
            if mapping.get(pred_id) == gt_id:
                idtp += 1
            else:
                idfp += 1
                idfn += 1
        idfp += n_up
        idfn += n_ug
    return idtp, idfp, idfn


def effective_horizon(errors_by_horizon: dict[float, float], tau: float) -> float | None:
    """有効ホライズン H*（付録A）：記号空間誤差が τ を超えない最大のホライズン h。

    全ホライズンで誤差が τ を超える場合は None を返す。

    例：effective_horizon({10.0: 0.1, 60.0: 0.3}, tau=0.2) == 10.0
    """
    ok = [h for h, err in errors_by_horizon.items() if err <= tau]
    return max(ok) if ok else None


def detection_rate(n_detected: int, n_injected: int) -> float:
    """検知率 = 検知された注入乖離数 ÷ 注入数（付録A、対象・型が正しい場合のみ検知）。

    例：detection_rate(45, 50) == 0.9
    """
    if n_injected <= 0:
        raise ValueError("n_injected は正である必要があります")
    return n_detected / n_injected


def false_alarms_per_day(n_false_alarms: int, n_days: float) -> float:
    """誤報数（付録A）：注入台帳に該当がなく、人の確認で乖離が存在しなかった台帳エントリ数
    （1日あたり）。

    例：false_alarms_per_day(4, 2.0) == 2.0
    """
    if n_days <= 0:
        raise ValueError("n_days は正である必要があります")
    return n_false_alarms / n_days


def detection_latency(
    detected_at: Sequence[datetime], injected_at: Sequence[datetime]
) -> list[float]:
    """検知遅延（付録A）= 台帳生成時刻 − 注入時刻（秒）。ペアごとの値のリストを返す
    （中央値・分布の要約は呼び出し側で `statistics.median` 等を使う）。

    例：detection_latency([t0+60s], [t0]) == [60.0]
    """
    if len(detected_at) != len(injected_at):
        raise ValueError("detected_at と injected_at は同じ長さである必要があります")
    return [(d - i).total_seconds() for d, i in zip(detected_at, injected_at, strict=True)]


def ece(confidences: Sequence[float], correct: Sequence[bool], n_bins: int = 10) -> float:
    """ECE（付録A）：確信度を `n_bins` 区間に分け、各区間の平均確信度と実際の正答率の差を
    区間サイズで加重平均する。`grounding/probes.py` の `expected_calibration_error`
    （学習ループ内で使う Tensor 版）と同一の定義。

    例：ece([0.9, 0.9], [True, False]) == 0.4（1区間に2件、平均確信度0.9、正答率0.5、|0.9-0.5|=0.4）
    """
    if len(confidences) != len(correct):
        raise ValueError("confidences と correct は同じ長さである必要があります")
    n = len(confidences)
    if n == 0:
        return 0.0
    conf = np.asarray(confidences, dtype=float)
    corr = np.asarray(correct, dtype=float)
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    total = 0.0
    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        mask = (conf > lo) & (conf <= hi) if i > 0 else (conf >= lo) & (conf <= hi)
        if not mask.any():
            continue
        acc_in_bin = corr[mask].mean()
        conf_in_bin = conf[mask].mean()
        total += (mask.sum() / n) * abs(acc_in_bin - conf_in_bin)
    return float(total)


def reconstruction_ssim(pred: np.ndarray, ref: np.ndarray) -> float:
    """復元画像のSSIM（付録A「漏洩評価」）：単一グローバルウィンドウの簡易 SSIM
    （skimage 非依存。厳密な local-windowed SSIM が必要になれば `scikit-image` を
    `ml` extra に追加して置き換えること。ここでは復元攻撃の粗い品質評価が目的）。

    pred, ref: 同形状の配列（画素値は同じスケール、例えば [0,1] または [0,255]）。

    例：reconstruction_ssim(x, x) == 1.0（完全一致）
    """
    if pred.shape != ref.shape:
        raise ValueError("pred と ref は同形状である必要があります")
    p = pred.astype(np.float64).ravel()
    r = ref.astype(np.float64).ravel()
    data_range = max(r.max() - r.min(), p.max() - p.min(), 1e-8)
    c1 = (0.01 * data_range) ** 2
    c2 = (0.03 * data_range) ** 2
    mu_p, mu_r = p.mean(), r.mean()
    var_p, var_r = p.var(), r.var()
    cov = ((p - mu_p) * (r - mu_r)).mean()
    numerator = (2 * mu_p * mu_r + c1) * (2 * cov + c2)
    denominator = (mu_p**2 + mu_r**2 + c1) * (var_p + var_r + c2)
    return float(numerator / denominator)


def reid_top1(preds: Sequence[int], labels: Sequence[int]) -> float:
    """人物再識別のTop-1精度（付録A「漏洩評価」）。チャンス率との比較は呼び出し側で行う
    （例：`reid_top1(...) <= chance_rate * 1.2`）。

    例：reid_top1([1, 2, 3], [1, 2, 4]) == 2/3
    """
    if len(preds) != len(labels):
        raise ValueError("preds と labels は同じ長さである必要があります")
    if not preds:
        return 0.0
    return sum(1 for p, y in zip(preds, labels, strict=True) if p == y) / len(preds)


def roc_auc(scores: Sequence[float], labels: Sequence[int]) -> float:
    """AUROC（poc_plan.md 3.1 H3「ドリフト検知のAUROC」）。

    Mann-Whitney U 統計量からノンパラメトリックに計算する（scikit-learn 非依存）：
    AUC = (陽性クラスの順位和 − n_pos(n_pos+1)/2) / (n_pos・n_neg)。
    同点は平均順位で扱う。`labels` は0/1（1=陽性、例：ドリフト後のエピソード）。

    例：roc_auc([0.1, 0.4, 0.9], [0, 0, 1]) == 1.0（陽性が常に陰性よりスコアが高い）
        roc_auc([0.5, 0.5], [0, 1]) == 0.5（完全な同点＝チャンスレベル）
    """
    if len(scores) != len(labels):
        raise ValueError("scores と labels は同じ長さである必要があります")
    scores_arr = np.asarray(scores, dtype=float)
    labels_arr = np.asarray(labels, dtype=int)
    n_pos = int((labels_arr == 1).sum())
    n_neg = int((labels_arr == 0).sum())
    if n_pos == 0 or n_neg == 0:
        raise ValueError("labels には陽性・陰性の両方が必要です")

    order = np.argsort(scores_arr, kind="mergesort")
    sorted_scores = scores_arr[order]
    ranks = np.empty(len(scores_arr))
    i = 0
    rank = 1
    while i < len(sorted_scores):
        j = i
        while j + 1 < len(sorted_scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        avg_rank = (rank + rank + (j - i)) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        rank += j - i + 1
        i = j + 1

    sum_ranks_pos = float(ranks[labels_arr == 1].sum())
    return (sum_ranks_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


__all__ = [
    "position_mismatch",
    "fact_distance_d",
    "epsilon_h",
    "grounding_prf",
    "id_switch_rate",
    "idf1",
    "count_id_switches",
    "id_counts_from_matches",
    "effective_horizon",
    "detection_rate",
    "false_alarms_per_day",
    "detection_latency",
    "ece",
    "reconstruction_ssim",
    "reid_top1",
    "roc_auc",
]
