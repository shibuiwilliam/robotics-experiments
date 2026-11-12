"""情報源信頼性モデル（設計書 §7.3、integration ルール）。

**業務記録は誤りうる**（設計書 §0.2 の仮定 A3）。この層はその「誤りかた」を分布として持ち、
信念更新に渡す尤度 ``P(record = x | 真の状態 s)`` を与える。

モデルは ``DataSource × 記録型`` ごとに次を持つ:

- **時刻ずれ分布** — 記録時刻と実際の発生時刻のずれ。時間不一致（類型 3）の判定閾値はここから導く。
- **誤記率・欠落率** — 記録が間違っている・無い確率。
- **系統誤差** — 再現性のある偏り（月末一括計上、数量丸め、テンプレ複写）。抑制規則の対象。
- **尤度関数** — 真偽状態なら混同行列、数値なら誤差分布。

パラメータの初期値は担当者ヒアリング相当の値を ``reliability/*.yaml`` に置き、解決済み不一致から
継続推定する（``update_from_resolution``）。確率は log 空間で扱う（CLAUDE.md 数値規約）。
"""

from __future__ import annotations

import math
from datetime import timedelta
from pathlib import Path
from statistics import NormalDist
from typing import TYPE_CHECKING, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbp.core.errors import IntegrationError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from sbp.core.records import Record

__all__ = [
    "ConfusionMatrix",
    "ReliabilityModel",
    "ReliabilityRegistry",
    "SystematicBias",
    "TimeOffsetProfile",
]

log = get_logger(__name__)

_LOG_EPSILON = 1e-12
"""log(0) を避けるための下限。"""


def _safe_log(p: float) -> float:
    """確率の自然対数（0 を下限で丸める）。"""
    return math.log(max(p, _LOG_EPSILON))


class TimeOffsetProfile(BaseModel):
    """時刻ずれの分布（設計書 §3.6 の許容プロファイル）。

    ``recorded_at - occurred_at`` の分布を正規分布で近似する。時間不一致（類型 3）の判定は
    この分布の指定パーセンタイルを閾値にする。

    Attributes:
        mean_s: ずれの平均（秒）。正なら事後記録が普通であることを表す。
        stddev_s: ずれの標準偏差（秒）。
        max_plausible_s: これを超えるずれは「あり得ない」とみなす上限（秒）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mean_s: float = 0.0
    stddev_s: float = Field(default=300.0, gt=0.0)
    max_plausible_s: float = Field(default=86_400.0, gt=0.0)

    def percentile_s(self, p: float) -> float:
        """指定パーセンタイルのずれ（秒）を返す。

        正規分布の分位点は標準ライブラリの :class:`statistics.NormalDist` を使う
        （自前の有理近似は誤差が大きく、閾値がずれると不一致判定が狂う）。

        Args:
            p: パーセンタイル（0 < p < 1）。
        """
        if not 0.0 < p < 1.0:
            raise ValueError(f"percentile must be in (0, 1), got {p}")
        return NormalDist(mu=self.mean_s, sigma=self.stddev_s).inv_cdf(p)

    def log_likelihood(self, offset: timedelta) -> float:
        """観測されたずれの対数尤度。

        Args:
            offset: ``recorded_at - occurred_at``。
        """
        seconds = offset.total_seconds()
        if abs(seconds) > self.max_plausible_s:
            return _safe_log(0.0)
        z = (seconds - self.mean_s) / self.stddev_s
        return -0.5 * z * z - math.log(self.stddev_s * math.sqrt(2.0 * math.pi))

    def is_implausible(self, offset: timedelta, *, percentile: float = 0.95) -> bool:
        """ずれが許容プロファイルの外にあるか（類型 3 の判定）。

        Args:
            offset: 観測されたずれ。
            percentile: 許容の上限パーセンタイル。
        """
        return abs(offset.total_seconds()) > abs(self.percentile_s(percentile))


class ConfusionMatrix(BaseModel):
    """真偽状態に対する記録の混同行列（設計書 §7.3 の尤度関数）。

    ``P(record says True | state is True)`` などを保持する。記録が「稼働中」と言ったときに
    本当に稼働している確率は、この行列と事前分布から出る。

    Attributes:
        true_positive: P(記録=真 | 状態=真)。
        false_positive: P(記録=真 | 状態=偽)。誤記・テンプレ複写で起きる。
        missing_rate: 状態が真でも記録が無い確率（欠落率）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    true_positive: float = Field(ge=0.0, le=1.0)
    false_positive: float = Field(ge=0.0, le=1.0)
    missing_rate: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _informative(self) -> Self:
        """記録が状態について何も語らない行列を拒否する。

        ``true_positive == false_positive`` だと記録は状態と独立であり、尤度比が 1 になって
        信念を一切動かさない。それは「この情報源を使わない」と宣言すべき状況であり、
        信頼性モデルとして登録するのは誤りである。
        """
        if abs(self.true_positive - self.false_positive) < 1e-9:
            raise ValueError(
                "true_positive equals false_positive: the record carries no information "
                "about the state. Remove the source instead of registering a useless model."
            )
        return self

    def log_likelihood(self, *, record_says: bool, state: bool) -> float:
        """記録と真の状態の組に対する対数尤度。

        Args:
            record_says: 記録が主張する真偽。
            state: 真の状態。
        """
        p_says_true = self.true_positive if state else self.false_positive
        return _safe_log(p_says_true if record_says else 1.0 - p_says_true)

    def log_likelihood_ratio(self, *, record_says: bool) -> float:
        """記録が主張したときの対数尤度比 ``log P(rec|真) - log P(rec|偽)``。

        信念更新ではこの値を事前の対数オッズに足す（設計書 §8.1 の記録尤度）。

        Args:
            record_says: 記録が主張する真偽。
        """
        return self.log_likelihood(record_says=record_says, state=True) - self.log_likelihood(
            record_says=record_says, state=False
        )


class SystematicBias(BaseModel):
    """系統誤差（設計書 §7.3、付録 C）。

    再現性のある偏りであり、**抑制規則の対象**になる（設計書 §8.2: 抑制は「信頼性モデルで
    説明できる系統誤差」に限定する）。偏りを明示的に持つことで、「説明できる不一致」と
    「説明できない不一致」を分けられる。

    Attributes:
        kind: 偏りの種別。
        description: 人が読む説明（抑制の理由として来歴に残す）。
        magnitude: 偏りの大きさ（種別ごとに意味が変わる。日数・丸め単位など）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["month_end_batch", "quantity_rounding", "template_copy", "shift_boundary"]
    description: str
    magnitude: float = 0.0

    def explains(self, record: Record) -> bool:
        """この偏りが当該記録のずれを説明できるか。

        Args:
            record: 判定対象の記録。
        """
        lag = record.reporting_lag
        if self.kind == "month_end_batch":
            # 月末一括計上: 記録日が月末付近で、遅れが月内に収まる
            return lag is not None and timedelta(0) <= lag <= timedelta(days=31)
        if self.kind == "shift_boundary":
            # シフト境界での丸めは、遅れがシフト長以内に収まる場合に説明できる。
            return lag is not None and abs(lag) <= timedelta(hours=self.magnitude or 8)
        if self.kind == "template_copy":
            # テンプレ複写: 時刻のずれでは説明できない（値の一致で判定するため常に偽）
            return False
        # 残るのは quantity_rounding。これは時刻ではなく数量の偏りなので、
        # 時刻のずれは説明しない（数量の照合側で扱う）。
        return False


class ReliabilityModel(BaseModel):
    """``DataSource × 記録型`` ごとの信頼性モデル。

    Attributes:
        source: 情報源 IRI（CURIE で宣言し IRI に展開される）。
        record_type: 記録型 IRI。
        time_offset: 時刻ずれの分布。
        confusion: 真偽状態に対する混同行列（真偽を主張しない記録型では ``None``）。
        numeric_error_stddev: 数値の記録誤差の標準偏差（数値を主張しない記録型では ``None``）。
        systematic_biases: 系統誤差の一覧。
        observations: このモデルの推定に使った解決済み不一致の件数（継続推定の進み具合）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    record_type: str
    time_offset: TimeOffsetProfile = Field(default_factory=TimeOffsetProfile)
    confusion: ConfusionMatrix | None = None
    numeric_error_stddev: float | None = Field(default=None, gt=0.0)
    systematic_biases: tuple[SystematicBias, ...] = ()
    observations: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _expand_iris(self) -> Self:
        """CURIE を IRI に展開する。"""
        from sbp.integration.mappings import expand_curie  # noqa: PLC0415 — 循環 import 回避

        object.__setattr__(self, "source", expand_curie(self.source))
        object.__setattr__(self, "record_type", expand_curie(self.record_type))
        return self

    @property
    def key(self) -> tuple[str, str]:
        """レジストリの鍵（情報源 × 記録型）。"""
        return (self.source, self.record_type)

    def log_likelihood_boolean(self, *, record_says: bool, state: bool) -> float:
        """真偽の記録に対する対数尤度。

        Args:
            record_says: 記録が主張する真偽。
            state: 真の状態。

        Raises:
            IntegrationError: 混同行列を持たないモデルで呼んだ場合。
        """
        if self.confusion is None:
            raise IntegrationError(
                f"reliability model for {self.record_type} has no confusion matrix; "
                "it does not assert boolean states"
            )
        return self.confusion.log_likelihood(record_says=record_says, state=state)

    def log_likelihood_numeric(self, *, recorded: float, state: float) -> float:
        """数値の記録に対する対数尤度（正規誤差を仮定）。

        Args:
            recorded: 記録された値。
            state: 真の値。

        Raises:
            IntegrationError: 数値誤差分布を持たないモデルで呼んだ場合。
        """
        if self.numeric_error_stddev is None:
            raise IntegrationError(
                f"reliability model for {self.record_type} has no numeric error model"
            )
        z = (recorded - state) / self.numeric_error_stddev
        return -0.5 * z * z - math.log(self.numeric_error_stddev * math.sqrt(2.0 * math.pi))

    def explains_as_systematic(self, record: Record) -> SystematicBias | None:
        """記録のずれを説明できる系統誤差を返す（無ければ ``None``）。

        不一致の抑制はこれが返る場合に限る（設計書 §8.2）。抑制の理由として来歴に残す。

        Args:
            record: 判定対象の記録。
        """
        for bias in self.systematic_biases:
            if bias.explains(record):
                return bias
        return None

    def update_from_resolution(
        self,
        *,
        outcome: Literal["physical_correct", "record_correct"],
        observed_lag_s: float | None,
    ) -> ReliabilityModel:
        """解決済み不一致から信頼性モデルを更新した新しいモデルを返す（設計書 §8.2）。

        「物理が正しかった」なら記録の誤りであり、誤記率を上げる方向に動かす。
        「記録が正しかった」なら記録の信頼を上げる。更新は**小さく**する（1 件の解決で
        モデルが大きく振れると、解決の揺らぎがそのまま尤度に乗る）。

        Args:
            outcome: 解決結果。
            observed_lag_s: 観測された時刻ずれ（秒）。不明なら ``None``。

        Returns:
            更新後の新しいモデル（この型は frozen なので元は変わらない）。
        """
        step = 1.0 / (self.observations + 10.0)  # 観測が増えるほど動きを小さくする
        confusion = self.confusion
        if confusion is not None:
            if outcome == "physical_correct":
                # 記録が誤っていた → 誤記率（false_positive）を少し上げる
                new_fp = min(confusion.false_positive + step * 0.5, confusion.true_positive - 1e-6)
                confusion = confusion.model_copy(update={"false_positive": max(new_fp, 0.0)})
            else:
                # 記録が正しかった → true_positive を少し上げる
                confusion = confusion.model_copy(
                    update={"true_positive": min(confusion.true_positive + step * 0.5, 1.0)}
                )

        time_offset = self.time_offset
        if observed_lag_s is not None:
            # 時刻ずれの平均を移動平均で寄せる
            new_mean = time_offset.mean_s + step * (observed_lag_s - time_offset.mean_s)
            time_offset = time_offset.model_copy(update={"mean_s": new_mean})

        return self.model_copy(
            update={
                "confusion": confusion,
                "time_offset": time_offset,
                "observations": self.observations + 1,
            }
        )


class ReliabilityRegistry:
    """信頼性モデルの一覧（``reliability/*.yaml`` から読む）。"""

    def __init__(self, models: dict[tuple[str, str], ReliabilityModel]) -> None:
        """Args:
        models: (情報源 IRI, 記録型 IRI) → モデル。
        """
        self._models = models

    @classmethod
    def from_directory(cls, directory: Path) -> ReliabilityRegistry:
        """ディレクトリ内の全モデルを読み込む。

        Args:
            directory: ``reliability/`` のパス。

        Raises:
            IntegrationError: YAML が壊れている、または同じ鍵が重複している場合。
        """
        models: dict[tuple[str, str], ReliabilityModel] = {}
        for path in sorted(directory.glob("*.yaml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            for entry in payload.get("models", []):
                try:
                    model = ReliabilityModel.model_validate(entry)
                except Exception as exc:
                    raise IntegrationError(
                        f"invalid reliability model in {path.name}: {exc}"
                    ) from exc
                if model.key in models:
                    raise IntegrationError(
                        f"duplicate reliability model for {model.key} in {path.name}"
                    )
                models[model.key] = model
        if not models:
            raise IntegrationError(f"no reliability models found under {directory}")
        log.debug("reliability.loaded", count=len(models))
        return cls(models)

    def get(self, source: str, record_type: str) -> ReliabilityModel:
        """情報源と記録型からモデルを引く。

        Args:
            source: 情報源 IRI。
            record_type: 記録型 IRI。

        Raises:
            IntegrationError: モデルが登録されていない場合。**既定値で代替しない**
                （未知の情報源を暗黙に信頼するのは危険であり、明示的に登録させる）。
        """
        try:
            return self._models[(source, record_type)]
        except KeyError as exc:
            raise IntegrationError(
                f"no reliability model for source={source} record_type={record_type}. "
                "Register one in reliability/*.yaml; records from unknown sources must not "
                "be trusted by default (design §7.3)."
            ) from exc

    def for_record(self, record: Record) -> ReliabilityModel:
        """記録からモデルを引く。

        Args:
            record: 対象の記録。
        """
        return self.get(record.source, record.record_type)

    def replace(self, model: ReliabilityModel) -> None:
        """モデルを差し替える（継続推定の結果を反映する）。

        Args:
            model: 新しいモデル。
        """
        self._models[model.key] = model

    def keys(self) -> tuple[tuple[str, str], ...]:
        """登録済みの鍵。"""
        return tuple(sorted(self._models))
