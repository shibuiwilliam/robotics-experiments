"""プローブ重みの直列化（M8-01）。

学習済みプローブを**プロセスの外に出せる形**にする。これが無いと、プローブは学習した
プロセスの寿命しか持たず、接地ワーカーは「起動時に毎回学習する」以外の選択肢を持てない
（``docs/PROGRESS.md`` の既知の制約「重みの直列化とロード」）。

**このモジュールは I/O を持たない。** ``bridge`` は永続化しない層であり（import-linter の
契約「bridge は永続化しない」）、ここが扱うのはオブジェクトとバイト列の相互変換までである。
置き場への読み書きは :mod:`sbp.knowledge.artifacts`、両者を繋ぐのはワーカー（``services/``）
——潜在索引の読み出しが ``services`` 側にあるのと同じ形で、層の境界がここにも出ている。

**形式は JSON である。** 線形プローブの重みは「潜在のどの方向が概念に対応するか」を示す
検査材料であり（設計書 §5.2）、人が読めて diff が取れることに意味がある。バイナリにすると
「重みを公開するのは意図的である」という :class:`~sbp.bridge.probes.LinearProbe` の設計が
置き場の形式で殺される。float64 は Python の ``json`` が ``repr`` 経由で往復するため、
値は厳密に保たれる（契約テストで検査する）。

**較正器を欠いた ``active`` は保存もロードも拒否する**（不変条件 7）。較正されていない
プローブを置き場に入れると、ロードした側は「保存されているのだから使える」と読む。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from sbp.bridge.calibration import TemperatureScaler
from sbp.bridge.probes import LinearProbe, ProbeSpec, ProbeStatus
from sbp.core.errors import BridgeError, CalibrationError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from sbp.bridge.calibration import Calibrator

__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "CalibrationState",
    "ProbeBundle",
    "TemperatureState",
    "artifact_key",
    "bundle_probe",
    "dumps",
    "loads",
    "restore_probe",
]

log = get_logger(__name__)

BUNDLE_SCHEMA_VERSION = "1.0.0"
"""束ね形式の版。

**読めない版は読まない。** 未知の版を「だいたい同じだろう」と読むと、意味の変わった
フィールドを黙って使うことになる。後方互換の範囲（MINOR 以下）だけを受け入れる。
"""


class TemperatureState(BaseModel):
    """温度スケーリングの状態（:class:`~sbp.bridge.calibration.TemperatureScaler`）。

    Attributes:
        kind: 較正器の種類を示すタグ。
        temperature: 推定した温度。``> 1`` は自信過剰、``< 1`` は自信不足を意味する。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["temperature"] = "temperature"
    temperature: float = Field(gt=0.0)


CalibrationState = Annotated[TemperatureState, Field(discriminator="kind")]
"""較正器の状態。

いまは温度スケーリングだけである。識別子付きの共用体にしてあるのは、較正器を増やしたときに
**既存の束ねが読めなくなる形**（フィールドの意味を変える）を避けるためで、新しい較正器は
``kind`` を足して加法的に拡張する。
"""


class ProbeBundle(BaseModel):
    """プローブ 1 つを外に出せる形にまとめたもの（契約）。

    Attributes:
        schema_version: 束ね形式の版。
        spec: 登録メタデータ（概念・WM 版・指標・身分）。
        latent_dim: 潜在の次元。``weights`` の長さと一致しなければならない。
        weights: 学習した重み。
        bias: バイアス項。
        calibration: 較正器の状態。``None`` は未較正（``candidate`` のみ許す）。
        trained: 学習済みかどうか。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = BUNDLE_SCHEMA_VERSION
    spec: ProbeSpec
    latent_dim: int = Field(ge=1)
    weights: tuple[float, ...]
    bias: float
    calibration: CalibrationState | None = None
    trained: bool

    @property
    def probe_id(self) -> str:
        """プローブの IRI（置き場のキーを組むのに使う）。"""
        return self.spec.probe_id


def _calibration_state(calibrator: Calibrator) -> CalibrationState:
    """較正器を直列化できる状態に変える。

    Args:
        calibrator: 取り付けられている較正器。

    Raises:
        BridgeError: 直列化の形を持たない較正器の場合。**黙って較正器を落とさない**
            ——落とすと、ロードした側は未較正のプローブを較正済みとして扱う。
    """
    if isinstance(calibrator, TemperatureScaler):
        return TemperatureState(temperature=calibrator.temperature)
    raise BridgeError(
        f"calibrator {type(calibrator).__name__} has no serialized form; add a "
        "CalibrationState variant for it rather than dropping it from the bundle "
        "(a dropped calibrator would load as 'uncalibrated but trusted')"
    )


def _restore_calibrator(state: CalibrationState) -> Calibrator:
    """直列化された状態から較正器を復元する。

    Args:
        state: 較正器の状態。
    """
    scaler = TemperatureScaler()
    # 保留データを持たないので ``fit`` は呼べない。推定済みの温度をそのまま戻す。
    scaler.restore(temperature=state.temperature)
    return scaler


def bundle_probe(probe: LinearProbe, spec: ProbeSpec) -> ProbeBundle:
    """学習済みプローブを束ねる。

    Args:
        probe: 学習済みの線形プローブ。
        spec: 対応する登録メタデータ。

    Returns:
        直列化できる束ね。

    Raises:
        BridgeError: 未学習のプローブを束ねようとした場合。
        CalibrationError: ``active`` なのに較正器を持たない場合（不変条件 7）。
    """
    if not probe.is_trained:
        raise BridgeError(
            f"probe {spec.probe_id} is not trained; bundling it would publish random "
            "weights under a registered probe id"
        )
    calibrator = probe.calibrator
    if spec.status is ProbeStatus.ACTIVE and calibrator is None:
        raise CalibrationError(
            f"probe {spec.probe_id} is active but has no calibrator; an active probe's "
            "output enters belief, and uncalibrated scores must not (invariant 7)"
        )
    bundle = ProbeBundle(
        spec=spec,
        latent_dim=probe.dim,
        weights=tuple(float(value) for value in probe.weights),
        bias=float(probe.bias),
        calibration=None if calibrator is None else _calibration_state(calibrator),
        trained=True,
    )
    log.info(
        "bridge.probe_bundled",
        probe_id=spec.probe_id,
        grounds=spec.grounds,
        status=spec.status.value,
        latent_dim=bundle.latent_dim,
        calibrated=bundle.calibration is not None,
    )
    return bundle


def restore_probe(bundle: ProbeBundle) -> tuple[LinearProbe, ProbeSpec]:
    """束ねからプローブを復元する。

    Args:
        bundle: 束ね。

    Returns:
        ``(プローブ, 登録メタデータ)``。

    Raises:
        BridgeError: 重みの長さが ``latent_dim`` と合わない、または未学習の束ねの場合。
        CalibrationError: ``active`` なのに較正器の状態が無い場合（不変条件 7）。
    """
    if len(bundle.weights) != bundle.latent_dim:
        raise BridgeError(
            f"probe {bundle.probe_id} declares latent_dim={bundle.latent_dim} but carries "
            f"{len(bundle.weights)} weights; the bundle is inconsistent"
        )
    if not bundle.trained:
        raise BridgeError(
            f"probe {bundle.probe_id} is bundled as untrained; refusing to load weights "
            "that were never fitted"
        )
    if bundle.spec.status is ProbeStatus.ACTIVE and bundle.calibration is None:
        raise CalibrationError(
            f"probe {bundle.probe_id} is active but the bundle has no calibration state; "
            "loading it would feed uncalibrated scores into belief (invariant 7)"
        )
    probe = LinearProbe(dim=bundle.latent_dim)
    probe.load_parameters(
        weights=np.asarray(bundle.weights, dtype=np.float64),
        bias=bundle.bias,
    )
    if bundle.calibration is not None:
        probe.attach_calibrator(_restore_calibrator(bundle.calibration))
    log.info(
        "bridge.probe_restored",
        probe_id=bundle.probe_id,
        grounds=bundle.spec.grounds,
        status=bundle.spec.status.value,
        latent_dim=bundle.latent_dim,
    )
    return probe, bundle.spec


def dumps(bundle: ProbeBundle) -> bytes:
    """束ねを UTF-8 の JSON バイト列にする。

    Args:
        bundle: 束ね。

    Returns:
        置き場に入れられるバイト列。キー順を固定し、人が diff を取れる体裁にする。
    """
    payload = bundle.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def loads(data: bytes) -> ProbeBundle:
    """JSON バイト列から束ねを読む。

    Args:
        data: :func:`dumps` が作ったバイト列。

    Returns:
        束ね。

    Raises:
        BridgeError: JSON として読めない、または束ね形式の版が非互換の場合。
    """
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError(f"probe bundle is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise BridgeError("probe bundle must be a JSON object")
    version = payload.get("schema_version")
    if not isinstance(version, str):
        raise BridgeError("probe bundle has no schema_version; refusing to guess the format")
    _require_compatible(version)
    return ProbeBundle.model_validate(payload)


def _require_compatible(version: str) -> None:
    """束ね形式の版が読める範囲にあるかを検査する。

    後方互換のみ許す（MAJOR が同じで、MINOR が自分以下）。**新しい版を読まない**のは、
    加えられたフィールドを無視して読むと「知らない制約が付いた束ね」を知らないまま使う
    ことになるためである。

    Args:
        version: 束ねが名乗る版。

    Raises:
        BridgeError: 版が semver として読めない、または非互換の場合。
    """
    try:
        found = tuple(int(part) for part in version.split("."))
        known = tuple(int(part) for part in BUNDLE_SCHEMA_VERSION.split("."))
    except ValueError as exc:
        raise BridgeError(
            f"probe bundle schema_version {version!r} is not semver (expected MAJOR.MINOR.PATCH)"
        ) from exc
    if len(found) != 3:
        raise BridgeError(
            f"probe bundle schema_version {version!r} is not semver (expected MAJOR.MINOR.PATCH)"
        )
    if found[0] != known[0] or found[1] > known[1]:
        raise BridgeError(
            f"probe bundle schema_version {version} is incompatible with this build "
            f"({BUNDLE_SCHEMA_VERSION}); only the same MAJOR and a MINOR at or below is readable"
        )


def artifact_key(spec: ProbeSpec) -> str:
    """置き場でのキーを ``probe_id`` から組む。

    IRI の末尾 2 節（``.../<ローカル名>/<版>``）を使い、``probes/<ローカル名>/<版>/probe.json``
    にする。レジストリが重みを ``<model_id>/<version>/`` 配下に置くのと同じ形で、**版が
    パスに出る**ことが重要である（設計書 §4.8）。

    ``latest`` を拒否する理由はレジストリと同じ: 同じ版を名乗る別の重みが後から入り込むと、
    過去の信念の説明が静かに変わる。

    Args:
        spec: 登録メタデータ。

    Returns:
        置き場のキー（相対パス）。

    Raises:
        BridgeError: ``probe_id`` が ``<ローカル名>/<版>`` の形を持たない場合、または
            版が ``latest`` の場合。
    """
    parts = [part for part in spec.probe_id.split("/") if part]
    if len(parts) < 2:
        raise BridgeError(
            f"probe_id {spec.probe_id!r} must end with '<local name>/<version>' so that the "
            "artifact key carries the version (design §4.8)"
        )
    local_name, version = parts[-2], parts[-1]
    if version == "latest":
        raise BridgeError(
            f"probe_id {spec.probe_id!r} uses 'latest'; an explicit version is required so "
            "that a past belief's explanation cannot change under the same name"
        )
    return f"probes/{local_name}/{version}/probe.json"
