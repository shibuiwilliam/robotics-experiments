"""`gtwm ground run --episode <id>`：エピソードを流して信念を KG に入れ、ε を時系列で
記録し、乖離台帳エントリを作る（poc_plan.md 5.4、docs/prompts.md セッション05）。

パイプライン：α（probes）で意味プローブ、identity（ハンガリアン割当）で個体対応付け、
consistency（ε）で整合性ギャップを計算し、process_deviation が閾値を超えたサンプルを
乖離台帳（open）に登録する（poc_plan.md 5.4「優先ポリシー：PoCでは自動反映は行わず、
全件人が判定する」ため、ここでは open までしか作らない。confirm/dismiss/resolve は
人が UI 経由で行う）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch import Tensor

from gtwm.grounding.anchors_labels import episode_meta_from_dir
from gtwm.grounding.consistency import (
    EpsilonRecord,
    FactSample,
    compute_epsilon,
    load_epsilon_config,
)
from gtwm.grounding.identity import TrackedEntity, resolve_identities
from gtwm.grounding.ledger import DiscrepancyEntry, DiscrepancyLedger
from gtwm.grounding.probes import SlotFacts, slot_facts_to_beliefs, utc
from gtwm.grounding.record_consistency import detect_record_discrepancies
from gtwm.grounding.train_probes import train_probes
from gtwm.kg.store import RdflibKGStore
from gtwm.sim.env import ZONE_NAMES
from gtwm.utils.device import get_device
from gtwm.utils.paths import repo_root
from gtwm.wm.dataset import read_single_frame
from gtwm.wm.fusion import CameraParams
from gtwm.wm.train import WMModules

SAMPLE_STRIDE_FRAMES = 10  # 1秒おき（log_hz=10 前提）


@dataclass
class GroundRunResult:
    episode_id: str
    n_beliefs: int
    n_ledger_entries: int
    epsilon_records: list[EpsilonRecord] = field(default_factory=list)
    output_dir: Path = field(default_factory=Path)
    # 観測(フレーム読込開始)から信念KG更新(store.add_beliefs完了)までの経過時間（秒）。
    # フレームごとに1つ、EXP-11（N1、E2E遅延）が p50/p95 を計算するのに使う。現在の
    # 実装はバッチ処理（全フレーム処理後に1回だけ store.add_beliefs する）なので、
    # 早いフレームほど「バッチの残り処理＋最終コミット」を待つ分だけ大きい値になる
    # （ストリーミング処理に作り替えれば個々のフレームはもっと速く確定できる。
    # docs/status.md に明記）。
    frame_latencies_s: list[float] = field(default_factory=list)


def encode_single_frame_slots(
    modules: WMModules,
    frame: Tensor,
    cam_names: list[str],
    cam_params: dict[str, CameraParams],
) -> tuple[Tensor, Tensor]:
    """1フレーム（B=1,T=1）の融合スロット [K,D] と型ロジット [K,n_types] を返す。"""
    tokens = modules.encoder.encode(frame)  # [1,1,C,P,De]
    b, t, c, p, de = tokens.shape
    per_cam_tokens = tokens.permute(0, 2, 1, 3, 4).reshape(b * c, t, p, de)
    slots_flat, type_logits_flat = modules.slot_module(per_cam_tokens)
    k, d = slots_flat.shape[-2], slots_flat.shape[-1]
    n_types = type_logits_flat.shape[-1]
    slots_per_cam = {
        name: slots_flat.reshape(b, c, t, k, d)[:, i] for i, name in enumerate(cam_names)
    }
    type_logits_per_cam = type_logits_flat.reshape(b, c, t, k, n_types)
    fused, _ = modules.fusion(slots_per_cam, cam_params)  # [1,1,K,D]
    type_logits = type_logits_per_cam.mean(dim=1)  # [1,1,K,n_types]
    return fused[0, 0], type_logits[0, 0]  # [K,D], [K,n_types]


def run_ground(
    episode_dir_name: str,
    set_name: str,
    probe_config: str = "configs/grounding/probe_train_smoke.yaml",
    existence_threshold: float = 0.5,
) -> GroundRunResult:
    device = get_device()
    probe, modules, cam_names, cam_params, _train_result = train_probes(probe_config)
    probe.eval()
    modules.slot_module.eval()
    modules.fusion.eval()
    modules.dynamics.eval()

    ep = episode_meta_from_dir(episode_dir_name, set_name)
    poses = pd.read_parquet(ep.episode_dir / "poses.parquet")

    out_dir = repo_root() / "runs" / "ground" / ep.episode_id
    out_dir.mkdir(parents=True, exist_ok=True)

    store = RdflibKGStore()
    ledger = DiscrepancyLedger(out_dir / "ledger.sqlite")

    epsilon_cfg = load_epsilon_config()
    horizons = {name: float(v) for name, v in epsilon_cfg.horizons_s.items()}
    horizon_samples: dict[str, list[FactSample]] = {name: [] for name in horizons}

    beliefs_all = []
    n_ledger_entries = 0
    frame_start_times: list[float] = []

    frame_indices = list(range(0, ep.n_frames, SAMPLE_STRIDE_FRAMES))
    for frame_idx in frame_indices:
        frame_start_times.append(time.perf_counter())
        t_s = frame_idx / ep.log_hz
        frame = read_single_frame(ep, frame_idx).to(device)
        with torch.no_grad():
            slots, type_logits = encode_single_frame_slots(modules, frame, cam_names, cam_params)
            zone_probs = probe.zone_probs(slots, calibrated=True)
            zone_conf, zone_idx = zone_probs.max(dim=-1)
            existence_prob = torch.sigmoid(probe.existence_head(slots).squeeze(-1))
            floor_pred = probe.floor_head(slots)
            state_probs = probe.state_head(slots).softmax(dim=-1)
            state_conf, state_idx = state_probs.max(dim=-1)
            type_probs = type_logits.softmax(dim=-1)
            type_conf, type_idx = type_probs.max(dim=-1)

        atol = 0.5 / ep.log_hz
        pose_rows = poses[np.isclose(poses["t"].to_numpy(), t_s, atol=atol)]
        entities = [
            TrackedEntity(
                entity_gt_id=str(row.entity_gt_id),
                predicted_xy=np.array([row.x, row.y]),
                appearance=np.zeros(1),
                last_seen_step=frame_idx,
            )
            for row in pose_rows.itertuples()
        ]
        slot_positions = floor_pred.cpu().numpy()
        slot_appearance = np.zeros((slot_positions.shape[0], 1))
        assignment = resolve_identities(
            slot_positions, slot_appearance, entities, distance_gate=2.5, appearance_weight=0.0
        )

        # 1フレームにつき最大1回だけロールアウトする（K スロット全部が対象、個体ごとに
        # 繰り返し呼ぶと同じ計算を何度もすることになるため）。有効なホライズンが1つも
        # 無ければ（エピソード終盤で h 秒先がエピソード長を超える）ロールアウトは省略する。
        future_zone_idx_by_horizon: dict[str, torch.Tensor] = {}
        for h_name, h_s in horizons.items():
            h_frames = int(round(h_s * ep.log_hz))
            if h_frames <= 0 or frame_idx + h_frames >= ep.n_frames:
                continue
            with torch.no_grad():
                rollout = modules.dynamics.rollout(slots.unsqueeze(0), None, None, h_frames)
                future_slots = rollout.mean(dim=0)[0, -1]  # [K,D]
                future_zone_idx_by_horizon[h_name] = probe.zone_head(future_slots).argmax(dim=-1)

        t_dt = utc(t_s)
        for slot_idx, entity_gt_id in assignment.slot_to_entity.items():
            facts = SlotFacts(
                entity_gt_id=entity_gt_id,
                existence_prob=float(existence_prob[slot_idx]),
                zone_idx=int(zone_idx[slot_idx]),
                zone_confidence=float(zone_conf[slot_idx]),
                floor_xy=(float(floor_pred[slot_idx, 0]), float(floor_pred[slot_idx, 1])),
                type_idx=int(type_idx[slot_idx]),
                type_confidence=float(type_conf[slot_idx]),
                state_idx=int(state_idx[slot_idx]),
                state_confidence=float(state_conf[slot_idx]),
            )
            beliefs = slot_facts_to_beliefs(
                facts, t_dt, source="wm", existence_threshold=existence_threshold
            )
            beliefs_all.extend(beliefs)
            if not beliefs:
                continue

            gt_rows = pose_rows[pose_rows["entity_gt_id"] == entity_gt_id]
            if gt_rows.empty:
                continue
            gt_zone = str(gt_rows.iloc[0]["zone"])
            if gt_zone not in ZONE_NAMES:
                continue
            truth_zone_idx = ZONE_NAMES.index(gt_zone)

            for h_name, future_idx_tensor in future_zone_idx_by_horizon.items():
                sample = FactSample(
                    entity_gt_id=entity_gt_id,
                    t=t_s,
                    perceived_zone_idx=int(zone_idx[slot_idx]),
                    truth_zone_idx=truth_zone_idx,
                    future_zone_idx=int(future_idx_tensor[slot_idx]),
                )
                horizon_samples[h_name].append(sample)

                d = abs(sample.future_zone_idx - sample.perceived_zone_idx)
                is_perception_ok = sample.perceived_zone_idx == sample.truth_zone_idx
                if is_perception_ok and d > 0:
                    entry = DiscrepancyEntry(
                        discrepancy_id=f"{ep.episode_id}_{entity_gt_id}_{h_name}_{frame_idx}",
                        object_id=entity_gt_id,
                        physical_value=f"gt:Zone_{ZONE_NAMES[sample.future_zone_idx]}",
                        physical_confidence=float(zone_conf[slot_idx]),
                        physical_source="wm",
                        physical_valid_time=t_dt,
                        record_value=f"gt:Zone_{ZONE_NAMES[sample.perceived_zone_idx]}",
                        record_source_system="wms_mock",
                        record_registration_time=t_dt,
                        discrepancy_type="other",
                        severity="low",
                        detected_at=t_dt,
                        epsilon_at_detection=float(d) * float(epsilon_cfg.d_weights.position),
                    )
                    ledger.create(entry)
                    if not ledger.last_create_was_duplicate:
                        n_ledger_entries += 1

    # 記録 vs 物理の突き合わせ（poc_plan.md 2.2「(2)プロセス不遵守」）。上のループは
    # WMロールアウトの内部一貫性しか見ておらず、`sim/wms_mock.apply_injections` が
    # 注入する5型の異常（record 側の改変）を検知できないため、`events.parquet` を
    # 直接読んで突き合わせる（`record_consistency.py` 参照。EXP-05 の初回実行で
    # detection_rate=0.0 として発覚した欠落）。
    events_df = pd.read_parquet(ep.episode_dir / "events.parquet")
    for candidate in detect_record_discrepancies(events_df, poses):
        # 検知は記録が実際に登録された時刻（t_obs）より前には起こり得ない
        # （detection_latency_median_s を意味のある値にするための最小の遅延モデル。
        # t_true を detected_at に使うと定義上ゼロ遅延になってしまう）。
        physical_dt = utc(candidate.t_true)
        detected_dt = utc(candidate.t_obs)
        entry = DiscrepancyEntry(
            discrepancy_id=(
                f"{ep.episode_id}_{candidate.entity_gt_id}_"
                f"{candidate.discrepancy_type}_{candidate.t_true}"
            ),
            object_id=candidate.entity_gt_id,
            physical_value=candidate.physical_value,
            physical_confidence=1.0,
            physical_source="physical_gt",
            physical_valid_time=physical_dt,
            record_value=candidate.record_value,
            record_source_system="wms_mock",
            record_registration_time=detected_dt,
            discrepancy_type=candidate.discrepancy_type,
            severity="medium",
            detected_at=detected_dt,
            epsilon_at_detection=float(epsilon_cfg.d_weights.position),
        )
        ledger.create(entry)
        if not ledger.last_create_was_duplicate:
            n_ledger_entries += 1

    store.add_beliefs(beliefs_all)
    commit_time = time.perf_counter()
    frame_latencies_s = [commit_time - t0 for t0 in frame_start_times]

    epsilon_records = []
    for h_name, h_s in horizons.items():
        samples = horizon_samples[h_name]
        if not samples:
            continue
        epsilon_records.append(compute_epsilon(samples, horizon_s=h_s, cfg=epsilon_cfg))

    (out_dir / "epsilon.json").write_text(
        json.dumps(
            [
                {
                    "horizon_s": r.horizon_s,
                    "epsilon": r.epsilon,
                    "n_samples": r.n_samples,
                    "decomposition": r.decomposition,
                }
                for r in epsilon_records
            ],
            indent=2,
        )
    )
    (out_dir / "meta.json").write_text(
        json.dumps(
            {
                "episode_id": ep.episode_id,
                "set_name": set_name,
                "n_beliefs": len(beliefs_all),
                "n_ledger_entries": n_ledger_entries,
                "n_frames_processed": len(frame_indices),
            },
            indent=2,
        )
    )
    ledger.close()

    return GroundRunResult(
        episode_id=ep.episode_id,
        n_beliefs=len(beliefs_all),
        n_ledger_entries=n_ledger_entries,
        epsilon_records=epsilon_records,
        output_dir=out_dir,
        frame_latencies_s=frame_latencies_s,
    )


__all__ = ["GroundRunResult", "run_ground", "encode_single_frame_slots"]
