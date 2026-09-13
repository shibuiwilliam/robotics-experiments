"""概念発見（C11、poc_plan.md 5.4・H7、.claude/rules/world_model.md「接地層の実装規則」）。

手順：予測残差の収集（LLM不使用）→ HDBSCAN クラスタリング（LLM不使用）→ 既存記号で
説明できるクラスタの除外 → 上位クラスタのみ `gtwm.llm`（概念命名タスク）で命名・説明を
生成 → `gt:ConceptCandidate`（reviewStatus=pending）として KG に保存。

オントロジーへの自動追加は行わない（人が承認したものだけ gt-core.ttl に手で追加する、
ontology.md「LLM の扱い」）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import hdbscan
import numpy as np
import pydantic
import rdflib
import torch

from gtwm.grounding.ground_run import encode_single_frame_slots
from gtwm.grounding.train_probes import train_probes
from gtwm.kg.schema import GT
from gtwm.kg.store import RdflibKGStore
from gtwm.llm.client import LLMClient
from gtwm.llm.prompt_loader import load_prompt
from gtwm.utils.device import get_device
from gtwm.wm.dataset import read_single_frame
from gtwm.wm.fusion import CameraParams
from gtwm.wm.train import WMModules

if TYPE_CHECKING:
    from gtwm.wm.dataset import EpisodeMeta

SAMPLE_STRIDE_FRAMES = 5

# 既存記号（型ヘッド）で十分説明できる、とみなす確信度の下限。これ未満のスロットのみ
# 「未知概念かもしれない」候補として残す（既存記号で高確信度に説明できるクラスタは除外）。
_TYPE_CONFIDENCE_EXPLAINED = 0.6


@dataclass
class ResidualSample:
    """1フレーム・1スロットぶんの予測残差特徴量。"""

    episode_id: str
    frame_idx: int
    slot_idx: int
    residual_vec: np.ndarray  # 1ステップ先の予測残差（[D]、Dynamics.rollout vs 実測エンコード）
    type_confidence: float
    type_idx: int


class ConceptNamingOutput(pydantic.BaseModel):
    proposed_label_ja: str
    proposed_label_en: str
    definition: str
    relation_to_existing_classes: str
    evidence_summary: str


@dataclass
class ConceptCandidateRecord:
    candidate_id: str
    cluster_id: int
    n_members: int
    mean_residual_norm: float
    naming: ConceptNamingOutput
    # メンバーの (episode_id, frame_idx) 一覧。`eval/scoring.py`（盲検境界の唯一の例外）が
    # 注入台帳との時間的な対応付けに使う。残差ベクトル自体は含めない（不要な肥大化を避ける）。
    member_episode_frames: list[tuple[str, int]]


def _encode_episode_slots(
    modules: WMModules,
    ep: EpisodeMeta,
    cam_names: list[str],
    cam_params: dict[str, CameraParams],
    device: str,
    stride: int = SAMPLE_STRIDE_FRAMES,
) -> tuple[list[int], torch.Tensor, torch.Tensor]:
    """サンプリングした各フレームのスロット [K,D] と型ロジット [K,n_types] を返す。"""
    frame_indices = list(range(0, ep.n_frames, stride))
    all_slots = []
    all_type_logits = []
    for frame_idx in frame_indices:
        frame = read_single_frame(ep, frame_idx).to(device)
        with torch.no_grad():
            slots, type_logits = encode_single_frame_slots(modules, frame, cam_names, cam_params)
        all_slots.append(slots)
        all_type_logits.append(type_logits)
    return frame_indices, torch.stack(all_slots), torch.stack(all_type_logits)  # [T,K,D],[T,K,C]


def collect_residuals(
    episode_id: str,
    set_name: str,
    probe_config: str = "configs/grounding/probe_train_smoke.yaml",
) -> list[ResidualSample]:
    """予測残差を収集する（LLM不使用）。1ステップ先の Dynamics ロールアウトと、
    実際に次フレームをエンコードして得たスロットとの差を残差とする。"""
    from gtwm.grounding.anchors_labels import episode_meta_from_dir

    device = get_device()
    probe, modules, cam_names, cam_params, _train_result = train_probes(probe_config)
    probe.eval()
    modules.slot_module.eval()
    modules.fusion.eval()
    modules.dynamics.eval()

    ep = episode_meta_from_dir(episode_id, set_name)
    frame_indices, slots_seq, type_logits_seq = _encode_episode_slots(
        modules, ep, cam_names, cam_params, device
    )
    type_probs = type_logits_seq.softmax(dim=-1)
    type_conf, type_idx = type_probs.max(dim=-1)  # [T,K]

    samples: list[ResidualSample] = []
    for t in range(len(frame_indices) - 1):
        slots_t = slots_seq[t]  # [K,D]
        with torch.no_grad():
            rollout = modules.dynamics.rollout(slots_t.unsqueeze(0), None, None, 1)
            pred_next = rollout.mean(dim=0)[0, -1]  # [K,D]
        actual_next = slots_seq[t + 1]  # [K,D]
        residual = (pred_next - actual_next).cpu().numpy()  # [K,D]
        for k in range(residual.shape[0]):
            samples.append(
                ResidualSample(
                    episode_id=episode_id,
                    frame_idx=frame_indices[t],
                    slot_idx=k,
                    residual_vec=residual[k],
                    type_confidence=float(type_conf[t, k]),
                    type_idx=int(type_idx[t, k]),
                )
            )
    return samples


def cluster_residuals(
    samples: list[ResidualSample], min_cluster_size: int = 3
) -> dict[int, list[ResidualSample]]:
    """HDBSCAN でクラスタリングする（LLM不使用）。ノイズラベル(-1)は除く。

    `allow_single_cluster=True`：HDBSCAN は既定では「データ全体が単一のクラスタ」という
    結果を許さない（木の根はクラスタとみなされない）ため、支配的な残差パターンが1つしか
    無い場合に全件ノイズ扱いになってしまう。概念発見では単一の未知概念パターンも検出対象
    にしたいため許可する。
    """
    if len(samples) < min_cluster_size:
        return {}
    features = np.stack([s.residual_vec for s in samples])
    clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size, allow_single_cluster=True)
    labels = clusterer.fit_predict(features)
    clusters: dict[int, list[ResidualSample]] = {}
    for label, sample in zip(labels, samples, strict=True):
        if label == -1:
            continue
        clusters.setdefault(int(label), []).append(sample)
    return clusters


def filter_unexplained_clusters(
    clusters: dict[int, list[ResidualSample]],
    residual_norm_percentile: float = 50.0,
    type_confidence_threshold: float = _TYPE_CONFIDENCE_EXPLAINED,
) -> dict[int, list[ResidualSample]]:
    """既存記号で説明できるクラスタを除外し、「うまく説明できない」クラスタだけを残す。

    2つの信号を使う：
    (1) 平均予測残差ノルムが低い（＝WMが1ステップ先をよく予測できている）クラスタは、
        既存の動態モデルで十分説明できるとみなし除外する。閾値は「全クラスタの中央値
        以上の残差ノルムを持つクラスタだけを残す」という相対基準（`residual_norm_percentile`）
        で決め、固定の絶対値をコードに書かない。
    (2) 型ヘッドの平均確信度が極端に低いクラスタ（型そのものが不明）も対象に残す
        （(1)を満たさなくても、型不明という別の意味で「既存記号で説明できない」ため）。

    NOTE：型ヘッド（pallet/case/agv/worker/equipment/none の6分類）の確信度だけでは
    「概念的な新規性」を判定できない（例：積み重ねケースの上段は依然として高確信度で
    "case" に分類されるが、荷姿としては未登録の変種である）。そのため (1) を主信号、
    (2) を補助信号として扱う。
    """
    if not clusters:
        return {}
    mean_norms = {
        label: float(np.mean([np.linalg.norm(m.residual_vec) for m in members]))
        for label, members in clusters.items()
    }
    norm_cutoff = float(np.percentile(list(mean_norms.values()), residual_norm_percentile))

    unexplained = {}
    for label, members in clusters.items():
        mean_conf = float(np.mean([m.type_confidence for m in members]))
        high_residual = mean_norms[label] >= norm_cutoff
        low_type_confidence = mean_conf < type_confidence_threshold
        if high_residual or low_type_confidence:
            unexplained[label] = members
    return unexplained


def name_candidates(
    clusters: dict[int, list[ResidualSample]],
    llm_config_path: str = "configs/llm.yaml",
    top_n: int = 5,
) -> list[ConceptCandidateRecord]:
    """上位クラスタ（メンバー数の多い順）について `gtwm.llm` の concept_naming タスクで
    命名・説明を生成する（ここで初めて LLM を使う）。"""
    client = LLMClient(config_path=llm_config_path)
    ranked = sorted(clusters.items(), key=lambda kv: len(kv[1]), reverse=True)[:top_n]
    records = []
    try:
        for cluster_id, members in ranked:
            mean_norm = float(np.mean([np.linalg.norm(m.residual_vec) for m in members]))
            feature_summary = (
                f"n_members={len(members)}, mean_residual_norm={mean_norm:.4f}, "
                f"episodes={sorted({m.episode_id for m in members})}, "
                f"mean_type_confidence={np.mean([m.type_confidence for m in members]):.3f}"
            )
            prompt = load_prompt(
                "concept_naming",
                cluster_id=cluster_id,
                n_members=len(members),
                feature_summary=feature_summary,
            )
            resp = client.call("concept_naming", prompt, schema=ConceptNamingOutput, max_tokens=500)
            naming = ConceptNamingOutput.model_validate_json(resp.text)
            records.append(
                ConceptCandidateRecord(
                    candidate_id=f"ConceptCandidate_{cluster_id}",
                    cluster_id=cluster_id,
                    n_members=len(members),
                    mean_residual_norm=mean_norm,
                    naming=naming,
                    member_episode_frames=[(m.episode_id, m.frame_idx) for m in members],
                )
            )
    finally:
        client.close()
    return records


def save_candidates_to_kg(store: RdflibKGStore, records: list[ConceptCandidateRecord]) -> None:
    """`gt:ConceptCandidate` として KG に保存する（reviewStatus=pending、人の承認待ち）。

    `gt-core.ttl` への自動追加は行わない（ontology.md「オントロジーの自動編集
    （クラス追加）はしない」）。
    """
    for rec in records:
        subj = GT[rec.candidate_id]
        store.graph.add((subj, rdflib.RDF.type, GT.ConceptCandidate))
        store.graph.add(
            (
                subj,
                GT.proposedLabel,
                rdflib.Literal(f"{rec.naming.proposed_label_ja} / {rec.naming.proposed_label_en}"),
            )
        )
        store.graph.add((subj, GT.reviewStatus, rdflib.Literal("pending")))
        evidence = json.dumps(
            {
                "cluster_id": rec.cluster_id,
                "n_members": rec.n_members,
                "mean_residual_norm": rec.mean_residual_norm,
                "definition": rec.naming.definition,
                "relation_to_existing_classes": rec.naming.relation_to_existing_classes,
                "evidence_summary": rec.naming.evidence_summary,
            },
            ensure_ascii=False,
        )
        store.graph.add((subj, GT.evidenceCluster, rdflib.Literal(evidence)))


def run_concept_discovery(
    episode_ids: list[str],
    set_name: str,
    probe_config: str = "configs/grounding/probe_train_smoke.yaml",
    llm_config_path: str = "configs/llm.yaml",
    min_cluster_size: int = 3,
    top_n: int = 5,
) -> tuple[RdflibKGStore, list[ConceptCandidateRecord]]:
    """複数エピソードにわたって残差収集→クラスタリング→フィルタ→命名→KG保存を行う。"""
    all_samples: list[ResidualSample] = []
    for episode_id in episode_ids:
        all_samples.extend(collect_residuals(episode_id, set_name, probe_config))
    clusters = cluster_residuals(all_samples, min_cluster_size=min_cluster_size)
    unexplained = filter_unexplained_clusters(clusters)
    records = name_candidates(unexplained, llm_config_path=llm_config_path, top_n=top_n)
    store = RdflibKGStore()
    save_candidates_to_kg(store, records)
    return store, records
