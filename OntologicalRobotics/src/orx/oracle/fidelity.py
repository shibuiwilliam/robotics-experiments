"""忠実度メトリクス（PROJECT.md §7.1）— 世界グラフ vs 真理グラフ。

入力はすべて common のデータ構造（スナップショット・アンカー対応・主張）で、
ORコアへの依存はない。
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from orx.common.schemas import (
    AnchorObservation,
    FidelityReport,
    StateSnapshot,
    ZoneTransition,
)
from orx.oracle.truth import FIDELITY_PREDICATES, truth_entity

_IN_ZONE_SUFFIX = "#inZone"


def majority_alignment(observations: list[AnchorObservation]) -> dict[str, str]:
    """世界個体 → 真値物体ID の多数決対応（同数は辞書順最小で決定的に）。"""
    votes: dict[str, Counter[str]] = defaultdict(Counter)
    for obs in observations:
        votes[obs.entity_iri][obs.true_object_id] += 1
    alignment: dict[str, str] = {}
    for entity, counter in votes.items():
        best = max(sorted(counter.items()), key=lambda kv: kv[1])
        alignment[entity] = best[0]
    return alignment


def pairwise_identity_prf(
    observations: list[AnchorObservation],
) -> tuple[float, float, float]:
    """ペアワイズ同一性 precision/recall/F1（真値物体ID対応に基づく）。"""
    n = len(observations)
    tp = fp = fn = 0
    for i in range(n):
        for j in range(i + 1, n):
            pred_same = observations[i].entity_iri == observations[j].entity_iri
            true_same = observations[i].true_object_id == observations[j].true_object_id
            if pred_same and true_same:
                tp += 1
            elif pred_same and not true_same:
                fp += 1
            elif true_same and not pred_same:
                fn += 1
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def _mapped_world_triples(
    snap: StateSnapshot, alignment: dict[str, str]
) -> set[tuple[str, str, str]]:
    mapped: set[tuple[str, str, str]] = set()
    for t in snap.triples:
        if t.predicate not in FIDELITY_PREDICATES:
            continue
        subject = alignment.get(t.subject)
        if subject is None:
            # 真値対応のない個体の主張は偽陽性として残す（潰さない）
            mapped.add((t.subject, t.predicate, t.object))
        else:
            mapped.add((truth_entity(subject), t.predicate, t.object))
    return mapped


def triple_prf(
    world_snaps: list[StateSnapshot],
    truth_snaps: list[StateSnapshot],
    alignment: dict[str, str],
) -> tuple[float, float, float]:
    """D2述語集合に対するトリプル precision/recall/F1（ティック横断マイクロ平均）。"""
    tp = fp = fn = 0
    for world, truth in zip(world_snaps, truth_snaps, strict=True):
        world_set = _mapped_world_triples(world, alignment)
        truth_set = {
            t.as_tuple() for t in truth.triples if t.predicate in FIDELITY_PREDICATES
        }
        tp += len(world_set & truth_set)
        fp += len(world_set - truth_set)
        fn += len(truth_set - world_set)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def position_rmse(
    world_snaps: list[StateSnapshot],
    truth_snaps: list[StateSnapshot],
    alignment: dict[str, str],
) -> float:
    """対応付いた個体の位置RMSE [m]。対応が一つも無ければ inf。"""
    squared: list[float] = []
    for world, truth in zip(world_snaps, truth_snaps, strict=True):
        for entity, true_id in alignment.items():
            wp = world.positions.get(entity)
            tp = truth.positions.get(truth_entity(true_id))
            if wp is None or tp is None:
                continue
            squared.append(sum((a - b) ** 2 for a, b in zip(wp, tp, strict=True)))
    if not squared:
        return math.inf
    return math.sqrt(sum(squared) / len(squared))


def zone_transitions(
    snaps: list[StateSnapshot], subject_map: dict[str, str] | None = None
) -> list[ZoneTransition]:
    """スナップショット列から inZone の変化イベントを導出する。"""
    in_zone_pred = next(p for p in FIDELITY_PREDICATES if p.endswith(_IN_ZONE_SUFFIX))
    last: dict[str, str] = {}
    transitions: list[ZoneTransition] = []
    for snap in snaps:
        for t in snap.triples:
            if t.predicate != in_zone_pred:
                continue
            subject = subject_map.get(t.subject, t.subject) if subject_map else t.subject
            zone = t.object.strip("<>")
            prev = last.get(subject)
            if prev is not None and prev != zone:
                transitions.append(
                    ZoneTransition(object_id=subject, to_zone=zone, sim_time=snap.sim_time)
                )
            last[subject] = zone
    return transitions


def transition_metrics(
    world_transitions: list[ZoneTransition],
    truth_transitions: list[ZoneTransition],
    max_delay_s: float = 5.0,
) -> tuple[float | None, float]:
    """状態遷移の平均検出遅延と取りこぼし率。真値遷移が無ければ (None, 0.0)。"""
    if not truth_transitions:
        return None, 0.0
    delays: list[float] = []
    misses = 0
    for tt in truth_transitions:
        candidates = [
            wt.sim_time
            for wt in world_transitions
            if wt.object_id == tt.object_id
            and wt.to_zone == tt.to_zone
            and -1.0 <= wt.sim_time - tt.sim_time <= max_delay_s
        ]
        if not candidates:
            misses += 1
        else:
            delays.append(max(0.0, min(candidates) - tt.sim_time))
    mean_delay = sum(delays) / len(delays) if delays else None
    return mean_delay, misses / len(truth_transitions)


def fidelity_report(
    world_snaps: list[StateSnapshot],
    truth_snaps: list[StateSnapshot],
    anchor_observations: list[AnchorObservation],
    staleness_rate: float,
    max_transition_delay_s: float = 5.0,
) -> FidelityReport:
    """忠実度レポートを構成する（C10 が記録・リプレイの最後に呼ぶ）。"""
    if len(world_snaps) != len(truth_snaps):
        raise ValueError(
            f"スナップショット数不一致: world={len(world_snaps)} truth={len(truth_snaps)}"
        )
    alignment = majority_alignment(anchor_observations)
    tp, tr, tf1 = triple_prf(world_snaps, truth_snaps, alignment)
    ip, ir, if1 = pairwise_identity_prf(anchor_observations)
    rmse = position_rmse(world_snaps, truth_snaps, alignment)
    world_trans = zone_transitions(
        world_snaps, subject_map={e: truth_entity(t) for e, t in alignment.items()}
    )
    truth_trans = zone_transitions(truth_snaps)
    delay, miss_rate = transition_metrics(world_trans, truth_trans, max_transition_delay_s)
    return FidelityReport(
        n_eval_ticks=len(world_snaps),
        triple_precision=round(tp, 6),
        triple_recall=round(tr, 6),
        triple_f1=round(tf1, 6),
        identity_precision=round(ip, 6),
        identity_recall=round(ir, 6),
        identity_f1=round(if1, 6),
        position_rmse=round(rmse, 6) if math.isfinite(rmse) else rmse,
        transition_mean_delay_s=None if delay is None else round(delay, 6),
        transition_miss_rate=round(miss_rate, 6),
        staleness_rate=round(staleness_rate, 6),
    )
