"""EXP-09 連合ツインと漏洩評価（H8、poc_plan.md 6.2）の測定ロジック。

手順（poc_plan.md 6.2 原文）：「本サイトと模擬第二拠点の間で、到着予定と在庫状態の予測を
EDC経由で交換。受領側で予測の較正（ECE）を評価。別途、潜在表現を共有した場合の漏洩を、
復元デコーダ学習と人物再識別で評価。出力：ECE、ODRLポリシー違反の監査ログ、復元画像の
品質（SSIM/PSNR）、再識別精度」。

このセッション（10）の簡略化（docs/status.md 参照）：
- EDC ではなく ADR-0002 の最小 HTTP コネクタ（`dataspace.connector.Connector`）を
  in-process で使う。実際に Docker 越しに HTTP 到達性・healthcheck が機能することは
  `make up-p2` で別途手動確認済み（このスモーク測定自体は Docker に依存しない）。
- 「模擬第二拠点」＝同じ倉庫レイアウトを別 seed（`config.site_b_seed`）で生成した
  データセット（`config.site_b_set`）。レイアウト自体を変える必要はない
  （docs/prompts.md セッション10「別レイアウトである必要はなく、seedを変えれば十分」）。
- 受領側 ECE：site_b の α（ゾーン確率、較正済み）を `PredictionRecord` として
  `Connector.handle_request` 経由で site_a へ払い出し、受け取った側で `ece()` を計算する。
  「正解ラベル」は site_b 自身の真値（poses.parquet）を使う簡略化（実運用では受領側が
  独自に後から確認するまでの遅延・不確実性があるが、本 smoke では即時に分かる前提）。
- ポリシー違反：意図的に1件、許可されていない目的（"marketing"）での交換要求を追加発行し、
  拒否されて監査ログに残ることを確認する（EXP-09 の「ODRLポリシー違反の監査ログ」指標）。
- 漏洩評価（復元攻撃・再識別攻撃）は `dataspace.leakage.run_leakage_evaluation` に委譲する。
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import torch
from omegaconf import DictConfig

from gtwm.dataspace.audit import AuditLog
from gtwm.dataspace.connector import Connector
from gtwm.dataspace.leakage import run_leakage_evaluation
from gtwm.dataspace.models import ExchangeRequest, PredictionRecord, Provenance
from gtwm.dataspace.policy import OdrlPolicy
from gtwm.eval.metrics import ece
from gtwm.grounding.ground_run import encode_single_frame_slots
from gtwm.grounding.train_probes import train_probes
from gtwm.sim.env import ZONE_NAMES
from gtwm.sim.generate import GenConfig, generate_set
from gtwm.utils.device import get_device
from gtwm.utils.paths import repo_root
from gtwm.wm.dataset import list_episodes, read_single_frame

_PURPOSE = "inbound_planning"


def _ensure_dataset(set_name: str, n_episodes: int, duration_s: float, seed: int) -> None:
    if (repo_root() / "data" / "sim" / set_name).exists():
        existing = list_episodes(set_name)
        if len(existing) >= n_episodes:
            return
    cfg = GenConfig(set_name=set_name, episodes=n_episodes, duration_s=duration_s, seed=seed)
    generate_set(cfg)


def _site_b_predictions(set_name: str, probe_config: str, stride: int) -> list[PredictionRecord]:
    """site_b の α（較正済みゾーン確率）を予測レコード化する（受領側 ECE 評価の対象）。"""
    probe, modules, cam_names, cam_params, _result = train_probes(probe_config)
    probe.eval()
    modules.slot_module.eval()
    modules.fusion.eval()
    device = get_device()

    records: list[PredictionRecord] = []
    for ep in list_episodes(set_name):
        poses = pd.read_parquet(ep.episode_dir / "poses.parquet")
        for frame_idx in range(0, ep.n_frames, stride):
            t_s = frame_idx / ep.log_hz
            frame = read_single_frame(ep, frame_idx).to(device)
            with torch.no_grad():
                slots, _type_logits = encode_single_frame_slots(
                    modules, frame, cam_names, cam_params
                )
                zone_probs = probe.zone_probs(slots, calibrated=True)
                _conf, idx = zone_probs.max(dim=-1)

            atol = 0.5 / ep.log_hz
            pose_rows = poses[np.isclose(poses["t"].to_numpy(), t_s, atol=atol)]
            # 簡略化のため先頭スロットのみを1予測として扱う（複数スロット分の予測交換は
            # 実装済みの `PredictionRecord` を繰り返し発行すればよいが、smoke では十分）。
            if pose_rows.empty or slots.shape[0] == 0:
                continue
            slot_idx = 0
            gt_zone = str(pose_rows.iloc[0]["zone"])
            correct = gt_zone in ZONE_NAMES and ZONE_NAMES[int(idx[slot_idx])] == gt_zone
            records.append(
                PredictionRecord(
                    variable="future_zone",
                    point_estimate=float(idx[slot_idx]),
                    interval_low=float(idx[slot_idx]),
                    interval_high=float(idx[slot_idx]),
                    horizon_s=0.0,
                    model_version=f"wm-smoke:{probe_config}",
                    provenance=Provenance(
                        generated_by="gt:ProbeAlpha",
                        attributed_to="site_b",
                        generated_at=datetime.now(UTC),
                    ),
                    outcome_correct=correct,
                )
            )
    return records


def measure(config: DictConfig, seed: int) -> dict[str, Any]:
    probe_config = str(config.get("probe_config", "configs/grounding/probe_train_smoke.yaml"))
    site_b_set = str(config.get("site_b_set", "p2_site_b"))
    site_b_seed = int(config.get("site_b_seed", 9000)) + seed
    n_episodes = int(config.get("n_episodes", 2))
    duration_s = float(config.get("duration_s", 30.0))
    stride = int(config.get("frame_stride", 10))

    _ensure_dataset(site_b_set, n_episodes, duration_s, site_b_seed)

    # --- 拠点間の予測交換（ADR-0002 の Connector を in-process で使う） ---
    audit_db = repo_root() / "runs" / "dataspace_smoke" / f"seed{seed}" / "audit.sqlite"
    audit_db.parent.mkdir(parents=True, exist_ok=True)
    if audit_db.exists():
        audit_db.unlink()  # 再実行時に前回の台帳と混ざらないようにする
    policy = OdrlPolicy(permitted_purposes=frozenset({_PURPOSE}), retention_days=30)
    site_b_connector = Connector(site_id="site_b", policy=policy, audit=AuditLog(audit_db))

    # `since` は予測生成より前の時刻でなければならない（`Connector.handle_request` は
    # `generated_at >= since` の予測だけを返す）。当初 `since=now`（生成後の時刻）を渡して
    # いたため常に0件になっていた実バグを、実際に `make exp EXP=EXP-09` を走らせて発見・修正。
    since = datetime.now(UTC)
    predictions = _site_b_predictions(site_b_set, probe_config, stride)
    for p in predictions:
        site_b_connector.publish_prediction(p)

    now = datetime.now(UTC)
    granted_resp = site_b_connector.handle_request(
        ExchangeRequest(
            requester_site="site_a", purpose=_PURPOSE, item_type="prediction", since=since
        ),
        now=now,
    )
    # 意図的なポリシー違反：許可されていない目的での要求 -> 拒否され監査ログに残るはず。
    denied_resp = site_b_connector.handle_request(
        ExchangeRequest(
            requester_site="site_a", purpose="marketing", item_type="prediction", since=since
        ),
        now=now,
    )
    assert denied_resp.granted is False  # ポリシー強制が機能していることの実行時保証

    received = [p for p in granted_resp.items if isinstance(p, PredictionRecord)]
    site_a_ece = _received_side_ece(received)

    leakage = run_leakage_evaluation(site_b_set, probe_config, frame_stride=stride)
    reid_valid = leakage.reid_chance_rate > 0 and not math.isnan(leakage.reid_top1_accuracy)
    reid_vs_chance = (
        leakage.reid_top1_accuracy / leakage.reid_chance_rate if reid_valid else float("nan")
    )

    return {
        "ece": site_a_ece,
        "reconstruction_ssim": leakage.reconstruction_ssim,
        "reid_top1": leakage.reid_top1_accuracy,
        "reid_chance_rate": leakage.reid_chance_rate,
        "reid_top1_vs_chance": reid_vs_chance,
        "n_predictions_exchanged": float(len(received)),
        "n_policy_violations": float(site_b_connector.audit.count_violations()),
        "n_reconstruction_samples": float(leakage.n_reconstruction_samples),
        "n_reid_samples": float(leakage.n_reid_samples),
    }


def _received_side_ece(received: list[PredictionRecord]) -> float:
    """受領した予測の確信度は `PredictionRecord` に運ばれないため（意図的：poc_plan.md 5.5
    は値・区間・ホライズン・モデル版のみを交換対象とし確信度は明示していない）、ここでは
    `outcome_correct` の充足率そのものを一様確信度1.0とみなした ECE として計算する
    （全予測を「確信度1.0」として送っている体で較正誤差=誤り率になる、という簡略化。
    本実行では `ExchangeBelief.confidence` を伴う信念の交換に切り替えて正しく評価すること
    を次セッションへの申し送りとする）。
    """
    if not received:
        return float("nan")
    correct = [bool(p.outcome_correct) for p in received if p.outcome_correct is not None]
    if not correct:
        return float("nan")
    confidences = [1.0] * len(correct)
    return ece(confidences, correct)
