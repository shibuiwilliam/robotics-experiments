"""SBP の設定。閾値・上限などの定数はすべてここに集約する（CLAUDE.md コーディング規約）。

環境変数は ``SBP_`` プレフィクス。環境変数を追加したら ``.env.example`` を同じコミットで更新する。
層別の閾値（``SBP_BRIDGE_*`` など）も直下の項目と同じく ``.env`` から読む
（:class:`_DotenvSettings`）。
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class LlmMode(StrEnum):
    """LLM の実行モード。既定は ``replay``（fixture 再生、ネットワーク不使用）。"""

    REPLAY = "replay"
    RECORD = "record"
    LIVE = "live"


class WritebackMode(StrEnum):
    """業務システムへの書き戻しモード。既定は ``dry``。"""

    DRY = "dry"
    LIVE = "live"


class _DotenvSettings(BaseSettings):
    """``.env`` を読む設定の共通基底。``Settings`` と層別の閾値のすべてが継承する。

    **入れ子の ``BaseSettings`` は親の ``env_file`` を引き継がない。** 層別の閾値は
    ``default_factory`` で別々に構築されるので、基底で ``env_file`` を持たせないと
    ``SBP_BRIDGE_*`` などは環境変数からしか読まれず、``.env`` に書いた値が**警告無しに既定値に
    なる**（``docs/PROGRESS.md`` 所見 108）。優先順位は直下の項目と同じで、環境変数が
    ``.env`` に勝つ。
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


class BridgeSettings(_DotenvSettings):
    """接地層の閾値（設計書 §5.2, §5.3, §5.7）。"""

    model_config = SettingsConfigDict(env_prefix="SBP_BRIDGE_")

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

    probe_ids: Annotated[tuple[str, ...], NoDecode] = ()
    """接地ワーカーが走らせるプローブの IRI（版を含む）。ADR-0009。

    **既定値を置かない。** 適当な版を既定にすると、設定漏れが「別の版のプローブで接地して
    いた」という形で現れる。環境変数はカンマ区切り（``a,b``）で書く——``NoDecode`` を付けて
    pydantic-settings の JSON 解釈を外しているのは、``.env`` に JSON 配列を書かせると
    運用でクォートを間違えるためである。
    """

    wm_version: str = ""
    """接地ワーカーが読む潜在を作った WM 版の IRI。ADR-0009。

    プローブの ``wm_version`` と一致しなければ起動時に落とす。次元が偶然一致している違う
    潜在空間を読むと、動いてしまって出力が無意味になる（設計書 §4.8）。
    """

    @field_validator("probe_ids", mode="before")
    @classmethod
    def _split_probe_ids(cls, value: object) -> object:
        """カンマ区切りの文字列を受け付ける。

        空白だけの要素は落とす。``NoDecode`` により、ここに来る値は環境変数の生の文字列である。

                Args:
                    value: 環境変数の生の値、または既に列になっているもの。
        """
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @model_validator(mode="after")
    def _hysteresis_ordered(self) -> BridgeSettings:
        """ヒステリシスは下側 < 上側でなければちらつき除去として機能しない。"""
        if self.hysteresis_low >= self.hysteresis_high:
            raise ValueError(
                "hysteresis_low must be strictly below hysteresis_high "
                f"(got low={self.hysteresis_low}, high={self.hysteresis_high})"
            )
        return self


class ReasoningSettings(_DotenvSettings):
    """推論層の閾値（設計書 §8.1, §8.2）。"""

    model_config = SettingsConfigDict(env_prefix="SBP_REASONING_")

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

    severity_value_scale: float = Field(default=1_000_000.0, gt=0.0)
    """深刻度の価値正規化基準。この額の業務価値で価値項が 1.0 になる。"""

    severity_persistence_scale_s: float = Field(default=3600.0, gt=0.0)
    """深刻度の持続時間正規化基準（秒）。この長さで持続項が 1.0 になる。"""

    bp_period_s: float = Field(default=60.0, gt=0.0)
    """実体間メッセージパッシング（loopy BP）の実行周期（秒）。"""

    bp_max_iterations: int = Field(default=5, ge=1)
    """loopy BP の 1 周期あたり最大反復回数。"""

    max_human_queries_per_hour: int = Field(default=4, ge=0)
    """エピステミック・スケジューラが人に発する質問の頻度上限。"""

    filter_step_s: float = Field(default=10.0, gt=0.0)
    """前向きフィルタの刻み幅（秒）。SynthWorld の標本間隔（10 秒）に合わせた既定。

    **滞在時間分布と同じ尺度でなければならない。** 刻みを変えると半マルコフの継続確率が
    変わり、同じ観測列でも事後が変わる（`sbp.reasoning.belief.TransitionPrior`）。
    """

    discrepancy_scope: Literal["site", "central"] = "site"
    """不一致の起票イベントを出すトピックの拠点部分（ADR-0010）。

    既定は拠点（``<site>.reasoning.discrepancy``）。検出器は自拠点のことだけを知っていれば
    よく、中央トピックは「拠点横断で**集約した**不一致」のための場所である。中央に直接出す
    運用に切り替えるときだけ ``central`` にする。**両方に出す形は作らない**——同じ不一致が
    2 つの ``event_id`` を持ち、冪等性（不変条件 4）が壊れる。
    """

    reconcile_period_s: float = Field(default=120.0, gt=0.0)
    """照合（五類型の検出）を回す周期（秒）。

    **信念更新の周期（``bp_period_s``）と分けている。** 同じ値にすると、照合を速くするために
    信念更新を遅くするか、その逆をすることになる。照合は窓（持続 τ）を見るので、τ より
    短い周期で回しても新しい判定材料は増えない。
    """

    window_samples: int = Field(default=360, ge=2, le=100_000)
    """照合の時間窓が保持する刻み数の上限。

    既定 360 × 刻み 10 秒 = 1 時間で、持続 τ（既定 300 秒）を十分覆う。**上限が τ を
    下回ると不一致が永久に検出されず、件数 0 として静かに通る**ので、``WindowStore`` が
    構築時に整合を検査する。
    """

    scheduler_max_queries: int = Field(default=10, ge=1, le=1_000)
    """1 周期で選ぶ観測クエリの件数上限。"""

    scheduler_total_cost: float = Field(default=1.0, gt=0.0)
    """1 周期で使える観測コストの合計（効用と同じ尺度）。

    **効用が 0 以下のクエリは選ばれない**ので、予算を増やしても無価値な観測は増えない
    （`sbp.reasoning.scheduler.schedule`）。予算は「価値のある観測が多すぎるときの上限」である。
    """

    batch_size: int = Field(default=100, ge=1, le=10_000)
    """ワーカーが 1 ループで取るイベントの最大件数。

    大きくすると取り込みは速いが、停止要求への応答が 1 バッチ分だけ遅れる（graceful shutdown は
    バッチの境界で効く）。
    """

    human_answer_confidence: float = Field(default=0.98, gt=0.5, lt=1.0)
    """人の確認回答が信念に入るときの確信度（ADR-0018）。成立なら ``c``、不成立なら ``1 − c``。

    1.0 にしない（人も誤る。信念が飽和して後から覆せなくなる）。回答者ごとに変えることは
    していない（ADR-0018 の未決）。
    """

    human_answer_uncertainty_s: float = Field(default=300.0, gt=0.0)
    """人の回答の有効時間の不確実幅（秒）。回答が「いつの状態」かには幅がある。"""

    expectation_verify_period_s: float = Field(default=60.0, gt=0.0)
    """期限切れの期待を検証する周期（秒、learning-worker。ADR-0017）。

    検証するのは期限を過ぎた ``Active`` だけなので、短くしても早く判定されるわけではない
    （期限が来てから、最大この秒数遅れて判定される）。
    """


class WorldModelSettings(_DotenvSettings):
    """WM 層の閾値（設計書 §4.8）。"""

    model_config = SettingsConfigDict(env_prefix="SBP_WM_")

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


class ActionsSettings(_DotenvSettings):
    """アクション層の閾値（設計書 §9）。"""

    model_config = SettingsConfigDict(env_prefix="SBP_ACTIONS_")

    record_effect_floor: float = Field(default=0.5, ge=0.0, le=1.0)
    """業務効果（``record_created`` / ``record_updated``）の期待の下端。

    契約が ``threshold`` を宣言していないときに使う。**記録の存在に対する信念は情報源の
    信頼性で割り引かれる**ので、1.0 にすると信頼性モデルが働いた時点で必ず違反になる。
    **実データで較正していない暫定値である**（合成データでは割引の実測値が出ない）。
    """


class IntegrationSettings(_DotenvSettings):
    """業務統合の取込ワーカーの設定（設計書 §7.1、ADR-0016）。"""

    model_config = SettingsConfigDict(env_prefix="SBP_INTEGRATION_")

    poll_period_s: float = Field(default=60.0, gt=0.0)
    """業務システムを読みに行く周期（秒）。資産台帳もこの周期で全行を同期する。"""

    mock_db_path: str = ""
    """開発用の業務システムモック（SQLite のファイル）のパス。

    **既定値を置かない。** 空なら取込ワーカーはコネクタ無しで起動し、``unwired`` で報告する——
    適当なパスを既定にすると、空のモックを読んで「業務記録が 0 件」を正常に見せる。実システムの
    コネクタは本リポジトリでは扱わない（ADR-0016 の未決）。
    """


class Settings(_DotenvSettings):
    """SBP 全体の設定。``.env`` から読み、プレフィクスは ``SBP_``。"""

    model_config = SettingsConfigDict(env_prefix="SBP_")

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
    actions: ActionsSettings = Field(default_factory=ActionsSettings)
    integration: IntegrationSettings = Field(default_factory=IntegrationSettings)

    @property
    def is_test(self) -> bool:
        """テスト環境かどうか。真値アクセスの許可判定などに使う。"""
        return self.env == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """プロセス内で共有する :class:`Settings` を返す（初回のみ ``.env`` を読む）。"""
    return Settings()
