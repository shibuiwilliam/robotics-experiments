"""モデルレジストリとドリフト監視（設計書 §4.8、M4-06・M4-07）。

> レジストリは WorldModelVersion をオントロジーに登録し、プローブと互換性行列を持つ。
> ドリフト監視：潜在分布シフト（MMD / PSI）、予測誤差の推移、整合残差の推移、プローブ較正の
> 劣化。閾値超過でシャドウ評価→再学習を起動。
> 昇格はカナリア（一部拠点・一部概念）→全体。旧版の推定は tx 時間で残る。

**``latest`` を禁じる理由。** 今日記録した信念は「どのモデルがどの重みで出したか」まで遡れな
ければ説明にならない（設計書 §8.8 の as-of 再構成）。``latest`` でロードすると、同じ
``wm_version`` を名乗る別の重みが後から入り込み、過去の信念の説明が静かに変わる。

**互換性行列を持つ理由。** プローブは潜在空間に対して学習されている。モデル版が変われば潜在の
意味が変わるため、**同じプローブをそのまま使えるとは限らない**。「次元が同じだから動く」は
最も危険な思い込みで、動くが出力が無意味になる。行列は「この版 × このプローブは評価済みか」を
明示的に持ち、未評価の組み合わせを active にできないようにする。

重みの置き場は ``s3://models/<model_id>/<version>/`` 配下（ADR-0003 により SeaweedFS）。
このモジュールは**場所と版の管理だけ**を行い、重みの直列化は各族に任せる（族ごとに形式が違う
ものを無理に共通化すると、どちらの族にも合わない形式になる）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from sbp.core.errors import ModelVersionError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sbp.worldmodel.base import ModelHealth, WorldModelMeta

__all__ = [
    "CompatibilityEntry",
    "DriftAlert",
    "ModelRegistry",
    "ModelStatus",
    "RegisteredModel",
    "evaluate_drift",
]

log = get_logger(__name__)


class ModelStatus(StrEnum):
    """モデル版の運用状態（設計書 §4.8 の昇格の段階）。"""

    CANDIDATE = "candidate"
    """登録済み。シャドウ評価の対象。推定を主信念に出さない。"""
    CANARY = "canary"
    """一部拠点・一部概念で稼働中。"""
    ACTIVE = "active"
    """全体で稼働中。"""
    DEPRECATED = "deprecated"
    """退役。過去の推定は tx 時間で残るため、説明のためにロード可能なままにする。"""

    @property
    def serves_main_belief(self) -> bool:
        """この状態のモデルの出力を主信念に入れてよいか。"""
        return self in {ModelStatus.CANARY, ModelStatus.ACTIVE}


@dataclass(frozen=True, slots=True)
class CompatibilityEntry:
    """「このモデル版 × このプローブ」の評価結果。

    Attributes:
        probe_id: プローブの IRI。
        auroc: 当該版の潜在で測った AUROC。
        ece: 同 ECE。
        align_residual: 同 整合残差。
        evaluated_at: 評価時刻（ISO 文字列）。
    """

    probe_id: str
    auroc: float
    ece: float
    align_residual: float
    evaluated_at: str

    def passes(self, *, min_auroc: float, max_ece: float, max_residual: float) -> bool:
        """昇格ゲートの AND 条件を満たすか（``.claude/rules`` の規定）。

        Args:
            min_auroc: AUROC の下限。
            max_ece: ECE の上限。
            max_residual: 整合残差の上限。
        """
        return (
            self.auroc >= min_auroc and self.ece <= max_ece and self.align_residual <= max_residual
        )


@dataclass(slots=True)
class RegisteredModel:
    """レジストリの 1 エントリ。

    Attributes:
        meta: モデルのメタデータ。
        status: 運用状態。
        weights_uri: 重みの置き場。
        compatibility: プローブ IRI → 評価結果。
        health_history: 健全性の履歴（古い順）。
    """

    meta: WorldModelMeta
    status: ModelStatus
    weights_uri: str
    compatibility: dict[str, CompatibilityEntry] = field(default_factory=dict)
    health_history: list[ModelHealth] = field(default_factory=list)

    @property
    def version_iri(self) -> str:
        """``WorldModelVersion`` の IRI。"""
        return self.meta.version_iri

    def record_compatibility(self, entry: CompatibilityEntry) -> None:
        """プローブとの互換性評価を記録する。

        Args:
            entry: 評価結果。
        """
        self.compatibility[entry.probe_id] = entry
        log.info(
            "worldmodel.compatibility_recorded",
            wm_version=self.version_iri,
            probe=entry.probe_id,
            auroc=entry.auroc,
            ece=entry.ece,
        )

    def supports(self, probe_id: str) -> bool:
        """このプローブとの組み合わせが評価済みか。"""
        return probe_id in self.compatibility


class ModelRegistry:
    """モデル版の登録・ロード・昇格を管理する。

    状態遷移は ``candidate → canary → active`` と ``* → deprecated`` のみ。``candidate`` から
    直接 ``active`` に飛べないのは、カナリア無しの全体昇格が設計書 §4.8 に反するためである。
    """

    _ALLOWED: ClassVar[dict[ModelStatus, frozenset[ModelStatus]]] = {
        ModelStatus.CANDIDATE: frozenset({ModelStatus.CANARY, ModelStatus.DEPRECATED}),
        ModelStatus.CANARY: frozenset({ModelStatus.ACTIVE, ModelStatus.DEPRECATED}),
        ModelStatus.ACTIVE: frozenset({ModelStatus.DEPRECATED}),
        ModelStatus.DEPRECATED: frozenset(),
    }

    def __init__(self, *, bucket: str = "models") -> None:
        """Args:
        bucket: 重みを置くバケット名（ADR-0003 の SeaweedFS）。
        """
        self._bucket = bucket
        self._models: dict[str, RegisteredModel] = {}

    def register(self, meta: WorldModelMeta) -> RegisteredModel:
        """モデル版を ``candidate`` として登録する。

        Args:
            meta: モデルのメタデータ。

        Raises:
            ModelVersionError: 同じ版が既に登録されている場合。
        """
        if meta.version_iri in self._models:
            raise ModelVersionError(
                f"model version {meta.version_iri} is already registered; bump the version "
                "instead of overwriting (past beliefs must stay explainable)"
            )
        entry = RegisteredModel(
            meta=meta,
            status=ModelStatus.CANDIDATE,
            weights_uri=f"s3://{self._bucket}/{meta.model_id.rsplit('/', 1)[-1]}/{meta.version}/",
        )
        self._models[meta.version_iri] = entry
        log.info(
            "worldmodel.registered",
            wm_version=meta.version_iri,
            weights_uri=entry.weights_uri,
            domain=meta.domain,
        )
        return entry

    def get(self, model_id: str, version: str) -> RegisteredModel:
        """版を指定してエントリを取り出す。

        Args:
            model_id: モデルの IRI。
            version: 版。``latest`` は拒否する。

        Raises:
            ModelVersionError: ``latest`` を指定した、または未登録の場合。
        """
        if version == "latest":
            raise ModelVersionError(
                "loading by 'latest' is forbidden; name the concrete version so that a belief "
                "recorded today stays explainable by the weights that produced it (design §4.8)"
            )
        key = f"{model_id}/{version}"
        try:
            return self._models[key]
        except KeyError as exc:
            raise ModelVersionError(
                f"model version {key} is not registered; known: {sorted(self._models)}"
            ) from exc

    def versions_of(self, model_id: str) -> tuple[str, ...]:
        """あるモデルの登録済みの版（昇順）。"""
        return tuple(
            sorted(
                entry.meta.version
                for entry in self._models.values()
                if entry.meta.model_id == model_id
            )
        )

    def serving(self, domain: str) -> tuple[RegisteredModel, ...]:
        """あるドメインで稼働中（canary / active）のモデル。"""
        return tuple(
            entry
            for entry in self._models.values()
            if entry.meta.domain == domain and entry.status.serves_main_belief
        )

    def promote(
        self,
        model_id: str,
        version: str,
        to: ModelStatus,
        *,
        required_probes: Iterable[str] = (),
        min_auroc: float,
        max_ece: float,
        max_residual: float,
    ) -> RegisteredModel:
        """運用状態を進める（互換性の評価を条件にする）。

        Args:
            model_id: モデルの IRI。
            version: 版。
            to: 遷移先の状態。
            required_probes: この版で動かすプローブ。全てが評価済みかつゲート通過であること。
            min_auroc: AUROC の下限。
            max_ece: ECE の上限。
            max_residual: 整合残差の上限。

        Raises:
            ModelVersionError: 許可されない遷移、未評価のプローブ、ゲート未達の場合。
        """
        entry = self.get(model_id, version)
        if to not in self._ALLOWED[entry.status]:
            allowed = sorted(status.value for status in self._ALLOWED[entry.status])
            raise ModelVersionError(
                f"cannot move {entry.version_iri} from {entry.status.value} to {to.value}; "
                f"allowed: {allowed or ['(terminal)']}"
            )
        if to.serves_main_belief:
            for probe_id in required_probes:
                compatibility = entry.compatibility.get(probe_id)
                if compatibility is None:
                    raise ModelVersionError(
                        f"probe {probe_id} has not been evaluated against {entry.version_iri}; "
                        "matching latent dimensions do not imply matching latent meaning"
                    )
                if not compatibility.passes(
                    min_auroc=min_auroc, max_ece=max_ece, max_residual=max_residual
                ):
                    raise ModelVersionError(
                        f"probe {probe_id} fails the gate on {entry.version_iri}: "
                        f"AUROC={compatibility.auroc:.4f} ECE={compatibility.ece:.4f} "
                        f"residual={compatibility.align_residual:.4f}"
                    )
        previous = entry.status
        entry.status = to
        log.info(
            "worldmodel.promoted",
            wm_version=entry.version_iri,
            from_status=previous.value,
            to_status=to.value,
            probes=sorted(entry.compatibility),
        )
        return entry

    def record_health(self, model_id: str, version: str, health: ModelHealth) -> RegisteredModel:
        """健全性の測定を履歴に積む。

        Args:
            model_id: モデルの IRI。
            version: 版。
            health: 測定結果。

        Raises:
            ModelVersionError: 測定結果の版がエントリと食い違う場合。
        """
        entry = self.get(model_id, version)
        if health.wm_version != entry.version_iri:
            raise ModelVersionError(
                f"health report names {health.wm_version} but was recorded against "
                f"{entry.version_iri}"
            )
        entry.health_history.append(health)
        return entry


@dataclass(frozen=True, slots=True)
class DriftAlert:
    """ドリフト検出の結果（イベントとして発行する）。

    Attributes:
        wm_version: 対象のモデル版。
        breached: 閾値を超えた指標の名前。
        trend: 指標名 → 直近と基準の差（悪化が正）。
        action: 推奨する処置（``shadow_evaluate`` / ``retrain`` / ``none``）。
    """

    wm_version: str
    breached: tuple[str, ...]
    trend: dict[str, float]
    action: str

    @property
    def needs_attention(self) -> bool:
        """人・ジョブの介入が要るか。"""
        return self.action != "none"


def evaluate_drift(
    entry: RegisteredModel,
    *,
    shift_warn: float,
    error_warn: float,
    residual_warn: float,
    window: int = 3,
) -> DriftAlert:
    """健全性の履歴からドリフトを判定する（設計書 §4.8）。

    **1 回の測定で再学習を起動しない。** 閾値を一度超えただけなら ``shadow_evaluate``（影で
    新版を評価する）に留め、直近 ``window`` 回で悪化が続いているときに ``retrain`` を出す。
    単発の超過で再学習を回すと、計測ノイズで学習ジョブが暴走する。

    Args:
        entry: 対象のレジストリエントリ。
        shift_warn: 潜在シフトの警告閾値。
        error_warn: 予測誤差の警告閾値。
        residual_warn: 整合残差の警告閾値。
        window: 傾向を見る直近の測定回数。

    Returns:
        ドリフト判定。

    Raises:
        ModelVersionError: 健全性の履歴が無い場合。
    """
    if not entry.health_history:
        raise ModelVersionError(
            f"{entry.version_iri} has no health history; run health() before judging drift"
        )
    latest = entry.health_history[-1]
    breached = latest.breaches(
        shift_warn=shift_warn, error_warn=error_warn, residual_warn=residual_warn
    )
    baseline = entry.health_history[0]
    trend = {
        "latent_shift": latest.latent_shift - baseline.latent_shift,
        "pred_error": latest.pred_error - baseline.pred_error,
        "align_residual": latest.align_residual - baseline.align_residual,
    }

    recent = entry.health_history[-window:]
    sustained = len(recent) >= window and all(
        report.breaches(shift_warn=shift_warn, error_warn=error_warn, residual_warn=residual_warn)
        for report in recent
    )
    if not breached:
        action = "none"
    elif sustained:
        action = "retrain"
    else:
        action = "shadow_evaluate"

    alert = DriftAlert(wm_version=entry.version_iri, breached=breached, trend=trend, action=action)
    if alert.needs_attention:
        log.info(
            "worldmodel.drift_detected",
            wm_version=entry.version_iri,
            breached=list(breached),
            action=action,
            measurements=len(entry.health_history),
        )
    return alert
