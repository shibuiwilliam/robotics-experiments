"""SBP の設定。閾値・上限などの定数はすべてここに集約する（CLAUDE.md コーディング規約）。

環境変数は ``SBP_`` プレフィクス。環境変数を追加したら ``.env.example`` を同じコミットで更新する。
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LlmMode(StrEnum):
    """LLM の実行モード。既定は ``replay``（fixture 再生、ネットワーク不使用）。"""

    REPLAY = "replay"
    RECORD = "record"
    LIVE = "live"


class WritebackMode(StrEnum):
    """業務システムへの書き戻しモード。既定は ``dry``。"""

    DRY = "dry"
    LIVE = "live"


class BridgeSettings(BaseSettings):
    """接地層の閾値（設計書 §5.2, §5.3, §5.7）。"""

    model_config = SettingsConfigDict(env_prefix="SBP_BRIDGE_", extra="ignore")

    promote_min_auroc: float = Field(default=0.85, ge=0.0, le=1.0)
    """プローブ昇格の AUROC 下限（AND 条件の 1 つ）。"""

    promote_max_ece: float = Field(default=0.05, ge=0.0, le=1.0)
    """プローブ昇格の ECE 上限（AND 条件の 1 つ）。"""

    hysteresis_high: float = Field(default=0.7, ge=0.0, le=1.0)
    """事象昇格のヒステリシス上側閾値（フルーエント成立と判定する確率）。"""

    hysteresis_low: float = Field(default=0.3, ge=0.0, le=1.0)
    """事象昇格のヒステリシス下側閾値（フルーエント終了と判定する確率）。"""

    min_duration_s: float = Field(default=20.0, gt=0.0)
    """ちらつき除去のための最小持続時間（秒）。"""

    weak_evidence_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    """この確信度未満のイベントは登録するが信念更新の尤度としては弱く扱う（設計書 §5.7）。"""

    alignment_residual_warn: float = Field(default=0.2, ge=0.0)
    """整合残差がこの値を超えたらドリフト監視へ通知する。"""

    identity_revoke_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    """IdentityClaim をこの確信度未満で撤回し、不一致（類型 4）を起票する。"""

    @model_validator(mode="after")
    def _hysteresis_ordered(self) -> BridgeSettings:
        """ヒステリシスは下側 < 上側でなければちらつき除去として機能しない。"""
        if self.hysteresis_low >= self.hysteresis_high:
            raise ValueError(
                "hysteresis_low must be strictly below hysteresis_high "
                f"(got low={self.hysteresis_low}, high={self.hysteresis_high})"
            )
        return self


class ReasoningSettings(BaseSettings):
    """推論層の閾値（設計書 §8.1, §8.2）。"""

    model_config = SettingsConfigDict(env_prefix="SBP_REASONING_", extra="ignore")

    theta_existence: float = Field(default=0.8, ge=0.0, le=1.0)
    """類型 1（存在不一致）の確率閾値 θ_1。"""

    theta_state_js: float = Field(default=0.5, ge=0.0, le=1.0)
    """類型 2（状態不一致）の Jensen-Shannon 距離閾値 θ_2。"""

    time_tolerance_percentile: float = Field(default=0.95, gt=0.0, lt=1.0)
    """類型 3（時間不一致）で用いる許容プロファイルのパーセンタイル。"""

    theta_norm: float = Field(default=0.7, ge=0.0, le=1.0)
    """類型 5（規範不一致）の確率閾値 θ_5。"""

    persistence_s: float = Field(default=300.0, gt=0.0)
    """不一致判定に必要な持続時間 τ（秒）。情報源ごとに上書きしうる。"""

    bp_period_s: float = Field(default=60.0, gt=0.0)
    """実体間メッセージパッシング（loopy BP）の実行周期（秒）。"""

    bp_max_iterations: int = Field(default=5, ge=1)
    """loopy BP の 1 周期あたり最大反復回数。"""

    max_human_queries_per_hour: int = Field(default=4, ge=0)
    """エピステミック・スケジューラが人に発する質問の頻度上限。"""


class WorldModelSettings(BaseSettings):
    """WM 層の閾値（設計書 §4.8）。"""

    model_config = SettingsConfigDict(env_prefix="SBP_WM_", extra="ignore")

    latent_shift_warn: float = Field(default=0.1, ge=0.0)
    """潜在分布シフト（MMD / PSI）の警告閾値。"""

    pred_error_warn: float = Field(default=0.3, ge=0.0)
    """予測誤差の警告閾値（正規化済み）。"""

    ensemble_size: int = Field(default=3, ge=1, le=10)
    """アンサンブルの要素数（認識的不確実性の推定に用いる）。"""

    rollout_samples: int = Field(default=32, ge=1)
    """ロールアウトのサンプル軌道数。"""

    torch_device: Literal["mps", "cpu"] = "mps"
    """PyTorch のデバイス。CUDA は使わない（CLAUDE.md）。"""


class Settings(BaseSettings):
    """SBP 全体の設定。``.env`` から読み、プレフィクスは ``SBP_``。"""

    model_config = SettingsConfigDict(
        env_prefix="SBP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- ランタイム ----
    env: Literal["dev", "test", "prod"] = "dev"
    site_id: str = "siteA"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ---- イベントログ ----
    kafka_bootstrap: str = "localhost:9092"
    consumer_group_prefix: str = "sbp"

    # ---- Postgres ----
    pg_dsn: str = "postgresql://sbp:sbp@localhost:5432/sbp"

    # ---- 知識グラフ ----
    sparql_query_url: str = "http://localhost:7878/query"
    sparql_update_url: str = "http://localhost:7878/update"
    sparql_store_url: str = "http://localhost:7878/store"

    # ---- オブジェクトストア ----
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "sbp"
    s3_secret_key: str = "sbp-secret-change-me"
    s3_bucket_raw: str = "raw"
    s3_bucket_models: str = "models"

    # ---- LLM ----
    llm_mode: LlmMode = LlmMode.REPLAY
    llm_default: Literal["anthropic", "openai", "gemini"] = "anthropic"
    embeddings: Literal["openai", "gemini", "local"] = "openai"

    # ---- 書き戻し ----
    writeback_mode: WritebackMode = WritebackMode.DRY

    # ---- 層別の閾値 ----
    bridge: BridgeSettings = Field(default_factory=BridgeSettings)
    reasoning: ReasoningSettings = Field(default_factory=ReasoningSettings)
    worldmodel: WorldModelSettings = Field(default_factory=WorldModelSettings)

    @property
    def is_test(self) -> bool:
        """テスト環境かどうか。真値アクセスの許可判定などに使う。"""
        return self.env == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """プロセス内で共有する :class:`Settings` を返す（初回のみ ``.env`` を読む）。"""
    return Settings()
