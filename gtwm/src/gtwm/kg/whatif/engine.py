"""WHAT-IF エンジン：PREDICT クエリをロールアウトし、KPIを算出する（poc_plan.md 5.6）。

手順（ontology.md「EPCIS と WHAT-IF」/ poc_plan.md 5.6 のコンパイル手順を実装）：
1. `compiler.compile_query()` で個体を解決し、`do()` を行動系列の上書きに変換する。
2. 開始状態（スロット）から `Dynamics.rollout` で介入込みのロールアウトを行う。
3. 各ホライズン・各サンプルの終端スロットを α（`Probe`）で記号化し、使い捨ての
   `KGStore` に信念として書き込む（識別は不要 — KPI はゾーン所属数の集計であり、
   時系列を跨いだ同一性追跡は不要なため、スロット添字ベースの仮個体IDを使う）。
4. `kg/queries/kpi_*.rq` で集計し、アンサンブル×サンプル分布から点推定と
   予測区間（`INTERVAL` で指定した確率のパーセンタイル）を返す。

このセッション（07）で実装済みのKPI変数は `queue_len` のみ。poc_plan.md 5.6 の例文が
求める `cycle_time` はイベントベースの滞在時間計測が必要で本セッションのスコープ外
（docs/status.md に明記、将来セッションでの拡張点）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import numpy as np
import torch
from rdflib.query import ResultRow
from torch import nn

from gtwm.grounding.anchors_labels import episode_meta_from_dir
from gtwm.grounding.ground_run import encode_single_frame_slots
from gtwm.grounding.probes import Probe
from gtwm.grounding.train_probes import train_probes
from gtwm.kg.schema import GT, Belief
from gtwm.kg.store import RdflibKGStore, load_query
from gtwm.kg.whatif.compiler import CompiledQuery, compile_query
from gtwm.kg.whatif.parser import WhatIfQuery, parse_whatif
from gtwm.sim.env import ZONE_NAMES
from gtwm.wm.dataset import read_single_frame
from gtwm.wm.dynamics import DynamicsHead
from gtwm.wm.train import WMModules

# smoke規模でも SAMPLES 200 のような大きな値を指定できるが、素朴な逐次ロールアウトを
# そのまま SAMPLES 回実行すると smoke の3分/5分予算を圧迫しうるため上限を設ける。
# アンサンブル（既定3）× 有効サンプル数がロールアウト総数になる。
MAX_EFFECTIVE_SAMPLES = 32
EXISTENCE_THRESHOLD = 0.5
DEFAULT_INTERVAL = 0.9
SUPPORTED_KPI_VARS = {"queue_len"}
_EPOCH = datetime.fromtimestamp(0, tz=UTC)


class WhatIfUnsupportedVarError(ValueError):
    """クエリが要求する `<var>` のうち、このセッションで未実装のものがある場合。"""


@dataclass
class VarPrediction:
    var: str
    horizon_s: float
    point_estimate: float
    interval_low: float
    interval_high: float
    n_rollouts: int


@dataclass
class WhatIfResult:
    query_text: str
    predictions: list[VarPrediction]
    model_version: str
    interval_prob: float


def _effective_samples(requested: int) -> int:
    return max(1, min(requested, MAX_EFFECTIVE_SAMPLES))


def _action_dim(modules: WMModules) -> int:
    head = cast(DynamicsHead, modules.dynamics.members[0])
    return cast(nn.Linear, head.action_in).in_features


def _queue_len_for_realization(
    probe: Probe, future_slots: torch.Tensor, zone_filter_idx: int | None
) -> float:
    """1回のロールアウト実現値（終端スロット状態）から queue_len を1つ算出する。"""
    with torch.no_grad():
        zone_probs = probe.zone_probs(future_slots, calibrated=True)  # [K,n_zones]
        existence = torch.sigmoid(probe.existence_head(future_slots).squeeze(-1))  # [K]
        zone_idx = zone_probs.argmax(dim=-1)  # [K]

    store = RdflibKGStore()
    beliefs = []
    for slot_idx in range(future_slots.shape[0]):
        if float(existence[slot_idx]) < EXISTENCE_THRESHOLD:
            continue
        zone_name = ZONE_NAMES[int(zone_idx[slot_idx])]
        beliefs.append(
            Belief(
                subject=f"gt:PredictedSlot_{slot_idx}",
                predicate="gt:currentZone",
                object=f"gt:Zone_{zone_name}",
                confidence=float(zone_probs[slot_idx, zone_idx[slot_idx]]),
                source="wm",
                valid_from=_EPOCH,
                transaction_time=_EPOCH,
            )
        )
    store.add_beliefs(beliefs)

    if zone_filter_idx is None:
        return float(len(beliefs))

    # `store.query()` は具象化された生グラフ（reification）を検索するため、
    # `?subject gt:currentZone ?zone` という平坦なパターンの kpi_*.rq には
    # `snapshot(t)` が返す材質化済みグラフを使う必要がある（session 03 の
    # `conditioning.extract_subgraph` と同じ流儀）。tests/unit/test_whatif_engine.py
    # の `test_queue_len_filters_by_zone` が、生グラフに対して直接クエリして
    # 0件になる回帰（実際に発生したバグ）を検出する。
    snapshot = store.snapshot(_EPOCH)
    target_zone = GT[f"Zone_{ZONE_NAMES[zone_filter_idx]}"]
    result = snapshot.query(load_query("kpi_queue_length"), initBindings={"zone": target_zone})
    rows = [row.asdict() for row in result if isinstance(row, ResultRow)]
    return float(rows[0]["count"]) if rows else 0.0


def run_whatif(
    query_text: str,
    episode_dir_name: str,
    set_name: str,
    probe_config: str = "configs/grounding/probe_train_smoke.yaml",
) -> WhatIfResult:
    """クエリ文字列を解析・コンパイルし、smoke規模のWMでロールアウトして結果を返す。"""
    query: WhatIfQuery = parse_whatif(query_text)
    compiled: CompiledQuery = compile_query(query)

    unsupported = sorted(set(compiled.vars) - SUPPORTED_KPI_VARS)
    if unsupported:
        raise WhatIfUnsupportedVarError(
            f"未実装のKPI変数: {unsupported}（実装済み: {sorted(SUPPORTED_KPI_VARS)}）。"
            "session 07 では queue_len のみ実装し、他はイベントベースの滞在時間計測が"
            "必要なため将来のセッションに委譲する（docs/status.md 参照）。"
        )

    probe, modules, cam_names, cam_params, _train_result = train_probes(probe_config)
    probe.eval()
    modules.slot_module.eval()
    modules.fusion.eval()
    modules.dynamics.eval()
    device = next(modules.dynamics.parameters()).device
    action_dim = _action_dim(modules)

    ep = episode_meta_from_dir(episode_dir_name, set_name)
    frame = read_single_frame(ep, 0).to(device)
    with torch.no_grad():
        slots0, _type_logits0 = encode_single_frame_slots(modules, frame, cam_names, cam_params)

    n_samples = _effective_samples(compiled.samples)
    interval_prob = compiled.interval if compiled.interval is not None else DEFAULT_INTERVAL
    zone_filter_idx = compiled.filters[0].zone_index if compiled.filters else None

    predictions: list[VarPrediction] = []
    for horizon in compiled.horizons:
        h_frames = max(1, int(round(horizon.seconds * ep.log_hz)))
        actions = torch.zeros(h_frames, action_dim, device=device)
        for interv in compiled.interventions:
            actions[:, interv.action_dim] = interv.action_value

        values: list[float] = []
        for sample_idx in range(n_samples):
            noise = torch.zeros_like(slots0) if sample_idx == 0 else 0.01 * torch.randn_like(slots0)
            slots_in = (slots0 + noise).unsqueeze(0)  # [1,K,D]
            with torch.no_grad():
                rollout = modules.dynamics.rollout(
                    slots_in, None, actions.unsqueeze(0), h_frames
                )  # [E,1,h,K,D]
            for e in range(rollout.shape[0]):
                future_slots = rollout[e, 0, -1]  # [K,D]
                values.append(_queue_len_for_realization(probe, future_slots, zone_filter_idx))

        arr = np.array(values)
        lo_pct = (1 - interval_prob) / 2 * 100
        hi_pct = 100 - lo_pct
        point = float(np.median(arr))
        lo = float(np.percentile(arr, lo_pct))
        hi = float(np.percentile(arr, hi_pct))
        for var in compiled.vars:
            predictions.append(
                VarPrediction(
                    var=var,
                    horizon_s=horizon.seconds,
                    point_estimate=point,
                    interval_low=lo,
                    interval_high=hi,
                    n_rollouts=len(arr),
                )
            )

    model_version = compiled.model_version or f"wm-smoke:{probe_config}"
    return WhatIfResult(
        query_text=query_text,
        predictions=predictions,
        model_version=model_version,
        interval_prob=interval_prob,
    )


__all__ = [
    "VarPrediction",
    "WhatIfResult",
    "WhatIfUnsupportedVarError",
    "run_whatif",
    "SUPPORTED_KPI_VARS",
]
