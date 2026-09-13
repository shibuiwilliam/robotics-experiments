"""EXP-08 概念発見（H7、poc_plan.md 6.2）の測定ロジック。

手順（poc_plan.md 6.2 原文）：「新しい荷姿・工程・置き場運用を1種ずつ、オントロジー
未登録のまま導入。2週間後に残差クラスタから候補を生成し、盲検の評価者が上位5候補を
判定。出力：出現率、候補の説明の妥当性（評価者3名の一致率）」。

このセッション（09）の簡略化（docs/status.md 参照）：
- 「2週間後」ではなく `config.data_set`（既定 `p2_concept`、`config.n_episodes` 本の
  30秒エピソード）を対象にする。
- 「盲検の評価者3名の一致率」は人手評価が必須のため、本関数は測定しない。代わりに、
  機械的に測れる代理指標（`injected_concept_top5_hit`：候補上位5件が、注入した3種の
  未登録概念のうちいくつと時間的に対応するか）のみを算出する。最終判定（説明の妥当性）
  は EXP-10 と同様、人が行う工程として README/report.md に明記する。
- LLM 呼出は `config.llm_config_path`（既定 `configs/llm_mock.yaml`＝mock、無課金）を使う。
  本実行では `configs/llm.yaml`（実プロバイダ）に切り替える。
"""

from __future__ import annotations

from typing import Any

from omegaconf import DictConfig

from gtwm.eval.scoring import score_concept_discovery
from gtwm.grounding.concept_discovery import run_concept_discovery
from gtwm.sim.concept_injection import ConceptInjectionConfig
from gtwm.sim.generate import GenConfig, generate_set
from gtwm.wm.dataset import list_episodes


def _ensure_concept_dataset(set_name: str, n_episodes: int, duration_s: float, seed: int) -> None:
    """`data/sim/<set_name>` が無ければ、3種の概念注入を有効にして生成する。"""
    existing = list_episodes(set_name) if _dataset_exists(set_name) else []
    if len(existing) >= n_episodes:
        return
    cfg = GenConfig(
        set_name=set_name,
        episodes=n_episodes,
        duration_s=duration_s,
        seed=seed,
        concept_injections=ConceptInjectionConfig(
            enable_oversized_cargo_tagging=True,
            enable_reinspection=True,
            enable_staging_overflow_tagging=True,
        ),
    )
    generate_set(cfg)


def _dataset_exists(set_name: str) -> bool:
    from gtwm.utils.paths import repo_root

    return (repo_root() / "data" / "sim" / set_name).exists()


def measure(config: DictConfig, seed: int) -> dict[str, Any]:
    set_name = str(config.get("data_set", "p2_concept"))
    n_episodes = int(config.get("n_episodes", 2))
    duration_s = float(config.get("duration_s", 30.0))
    _ensure_concept_dataset(set_name, n_episodes, duration_s, seed)

    episodes = list_episodes(set_name)
    episode_ids = [ep.episode_id for ep in episodes][:n_episodes]

    probe_config = str(config.get("probe_config", "configs/grounding/probe_train_smoke.yaml"))
    llm_config_path = str(config.get("llm_config_path", "configs/llm_mock.yaml"))
    min_cluster_size = int(config.get("min_cluster_size", 3))
    top_n = int(config.get("top_n", 5))

    _store, records = run_concept_discovery(
        episode_ids=episode_ids,
        set_name=set_name,
        probe_config=probe_config,
        llm_config_path=llm_config_path,
        min_cluster_size=min_cluster_size,
        top_n=top_n,
    )

    scoring = score_concept_discovery(
        candidates=list(records),
        episode_ids=episode_ids,
        time_tolerance_s=float(config.get("time_tolerance_s", 5.0)),
    )

    return {
        "n_episodes": len(episode_ids),
        "n_candidates": len(records),
        "injected_concept_top5_hit": len(scoring.hit_types),
        "n_injected_types": len(scoring.injected_types),
        "hit_types_csv": ",".join(scoring.hit_types) if scoring.hit_types else "none",
    }
