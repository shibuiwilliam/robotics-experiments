"""プローブ重みの直列化の単体テスト（M8-01）。

重点:

1. **往復で値が変わらない** — 重み・バイアス・温度が float64 のまま戻る。ずれると、
   置き場から読んだプローブは「同じ版を名乗る別のプローブ」になる。
2. **較正器を欠いた ``active`` を通さない**（不変条件 7）— 保存とロードの両側で拒否する。
   片側だけだと、別の経路で入った束ねが素のシグモイドを信念に流す。
3. **次元の不一致で落ちる** — 別の WM 版の潜在に対する重みは、次元が偶然一致しても意味が違う。
4. **``latest`` を置き場のキーにできない** — 同じ名前の中身が入れ替わると、過去の信念の
   説明が静かに変わる（レジストリが ``latest`` を禁じるのと同じ理由）。
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from sbp.bridge.calibration import ConformalRegressor, TemperatureScaler
from sbp.bridge.probes import (
    LinearProbe,
    ProbeKind,
    ProbeMetrics,
    ProbeSpec,
    ProbeStatus,
)
from sbp.bridge.serialization import (
    BUNDLE_SCHEMA_VERSION,
    ProbeBundle,
    TemperatureState,
    artifact_key,
    bundle_probe,
    dumps,
    loads,
    restore_probe,
)
from sbp.core.errors import BridgeError, CalibrationError

PROBE_ID = "https://sbp.example/probe/Running/1.0.0"
GROUNDS = "https://sbp.example/ontology/core#Running"
WM_VERSION = "https://sbp.example/wm/koopman/0.1.0"
DIM = 6


def _spec(**overrides: object) -> ProbeSpec:
    defaults: dict[str, object] = {
        "probe_id": PROBE_ID,
        "grounds": GROUNDS,
        "kind": ProbeKind.CLASSIFIER,
        "wm_version": WM_VERSION,
        "ontology_version": "0.5.0",
    }
    return ProbeSpec.model_validate(defaults | overrides)


def _metrics() -> ProbeMetrics:
    return ProbeMetrics(auroc=0.95, ece=0.02, alignment_residual=0.05, n_samples=200)


def _trained_probe(*, calibrated: bool = True, seed: int = 7) -> LinearProbe:
    """学習済み（必要なら較正済み）のプローブを作る。"""
    rng = np.random.default_rng(seed)
    latents = rng.normal(size=(120, DIM))
    labels = latents[:, 0] > 0.0
    probe = LinearProbe(dim=DIM, seed=seed).fit(latents, labels, epochs=120)
    if calibrated:
        scaler = TemperatureScaler().fit(probe.logits(latents), labels)
        probe.attach_calibrator(scaler)
    return probe


def test_roundtrip_preserves_parameters_exactly() -> None:
    """重み・バイアス・温度が JSON を往復しても厳密に一致する。"""
    probe = _trained_probe()
    spec = _spec(status=ProbeStatus.ACTIVE, metrics=_metrics())
    bundle = bundle_probe(probe, spec)

    restored_probe, restored_spec = restore_probe(loads(dumps(bundle)))

    assert restored_spec == spec
    np.testing.assert_array_equal(restored_probe.weights, probe.weights)
    assert restored_probe.bias == probe.bias
    assert restored_probe.dim == probe.dim
    assert restored_probe.is_trained


def test_roundtrip_preserves_predictions() -> None:
    """復元したプローブが同じ潜在に対して同じ確率を返す。

    重みだけでなく較正器まで戻っていることの検査である。温度が落ちると確率がずれる。
    """
    probe = _trained_probe()
    spec = _spec(status=ProbeStatus.ACTIVE, metrics=_metrics())
    latents = np.random.default_rng(99).normal(size=(20, DIM))
    before = probe.predict(latents)

    restored, _restored_spec = restore_probe(loads(dumps(bundle_probe(probe, spec))))
    after = restored.predict(latents)

    np.testing.assert_array_equal(after.probabilities, before.probabilities)
    np.testing.assert_array_equal(after.raw_scores, before.raw_scores)


def test_bundling_an_active_probe_without_calibrator_fails() -> None:
    """較正器の無い ``active`` は保存できない（不変条件 7）。"""
    probe = _trained_probe(calibrated=False)
    spec = _spec(status=ProbeStatus.ACTIVE, metrics=_metrics())

    with pytest.raises(CalibrationError, match="invariant 7"):
        bundle_probe(probe, spec)


def test_loading_an_active_bundle_without_calibration_fails() -> None:
    """較正の無い ``active`` な束ねはロードできない（不変条件 7）。

    保存側の検査だけでは足りない。置き場の中身は別の経路（手で置く、古い版が書いた）でも
    入りうるため、**ロード側でも同じ条件を検査する**。
    """
    bundle = ProbeBundle(
        spec=_spec(status=ProbeStatus.ACTIVE, metrics=_metrics()),
        latent_dim=DIM,
        weights=tuple(0.1 for _ in range(DIM)),
        bias=0.0,
        calibration=None,
        trained=True,
    )

    with pytest.raises(CalibrationError, match="invariant 7"):
        restore_probe(bundle)


def test_candidate_without_calibrator_roundtrips() -> None:
    """``candidate`` は未較正でも保存・ロードできる（まだ信念に入らない）。"""
    probe = _trained_probe(calibrated=False)
    bundle = bundle_probe(probe, _spec())

    restored, restored_spec = restore_probe(loads(dumps(bundle)))

    assert restored_spec.status is ProbeStatus.CANDIDATE
    assert restored.calibrator is None
    # 未較正なので確率は出せない。「保存できる」と「使える」は別である
    with pytest.raises(BridgeError, match="no calibrator"):
        restored.predict(np.zeros((1, DIM)))


def test_untrained_probe_cannot_be_bundled() -> None:
    """未学習のプローブは保存できない（乱数の重みを登録済み IRI で配らない）。"""
    with pytest.raises(BridgeError, match="not trained"):
        bundle_probe(LinearProbe(dim=DIM), _spec())


def test_untrained_bundle_cannot_be_restored() -> None:
    """``trained=False`` の束ねはロードできない。"""
    bundle = ProbeBundle(
        spec=_spec(),
        latent_dim=DIM,
        weights=tuple(0.0 for _ in range(DIM)),
        bias=0.0,
        trained=False,
    )
    with pytest.raises(BridgeError, match="never fitted"):
        restore_probe(bundle)


def test_weight_count_must_match_latent_dim() -> None:
    """重みの数と ``latent_dim`` が食い違う束ねは拒否する。"""
    bundle = ProbeBundle(
        spec=_spec(),
        latent_dim=DIM,
        weights=(0.1, 0.2),
        bias=0.0,
        trained=True,
    )
    with pytest.raises(BridgeError, match="inconsistent"):
        restore_probe(bundle)


def test_load_parameters_rejects_dimension_mismatch() -> None:
    """次元の違う重みを直接流し込めない。

    別の WM 版で学習した重みは、次元が偶然一致しても意味が違う。ここは「形が違えば落ちる」
    ところまでしか守れないが、静かに動くことは防げる。
    """
    probe = LinearProbe(dim=DIM)
    with pytest.raises(BridgeError, match="does not match probe dimension"):
        probe.load_parameters(weights=np.zeros(DIM + 1), bias=0.0)


def test_attaching_a_non_calibrator_is_refused() -> None:
    """``apply`` を持たないものを較正器として取り付けられない（不変条件 7）。

    :class:`ConformalRegressor` は回帰用で ``apply`` を持たない。Protocol は型検査しか
    しないので、実行時の検査が無ければ分類プローブに付いてしまい、``predict`` まで
    「較正済み」として扱われる。
    """
    probe = _trained_probe(calibrated=False)

    with pytest.raises(CalibrationError, match="not a Calibrator"):
        probe.attach_calibrator(ConformalRegressor())  # type: ignore[arg-type]


def test_unknown_calibrator_is_not_silently_dropped() -> None:
    """直列化の形を持たない較正器は保存時に例外になる（黙って落とさない）。

    落とすと、ロードした側は未較正のプローブを較正済みとして扱う。``apply`` を持つが
    束ね形式を持たない較正器（将来足されるもの）を想定した検査である。
    """

    class _UnknownCalibrator:
        """``apply`` は持つが :class:`TemperatureState` に対応しない較正器。"""

        def apply(self, raw_scores: np.ndarray) -> np.ndarray:  # type: ignore[type-arg]
            return raw_scores

        @property
        def is_fitted(self) -> bool:
            return True

    probe = _trained_probe(calibrated=False)
    probe.attach_calibrator(_UnknownCalibrator())  # type: ignore[arg-type]

    with pytest.raises(BridgeError, match="no serialized form"):
        bundle_probe(probe, _spec())


@pytest.mark.parametrize("version", ["2.0.0", "1.1.0", "0.9.0", "1.0", "x.y.z"])
def test_incompatible_schema_versions_are_refused(version: str) -> None:
    """自分より新しい MINOR・違う MAJOR・semver でない版は読まない。"""
    payload = json.loads(dumps(bundle_probe(_trained_probe(), _spec())))
    payload["schema_version"] = version

    with pytest.raises(BridgeError):
        loads(json.dumps(payload).encode("utf-8"))


def test_same_minor_patch_bump_is_readable() -> None:
    """同じ MAJOR・MINOR で PATCH だけ違う版は読める（後方互換）。"""
    payload = json.loads(dumps(bundle_probe(_trained_probe(), _spec())))
    major, minor, _patch = BUNDLE_SCHEMA_VERSION.split(".")
    payload["schema_version"] = f"{major}.{minor}.99"

    assert loads(json.dumps(payload).encode("utf-8")).schema_version == f"{major}.{minor}.99"


def test_bundle_without_schema_version_is_refused() -> None:
    """版を名乗らない束ねは形式を推測せずに落とす。"""
    payload = json.loads(dumps(bundle_probe(_trained_probe(), _spec())))
    del payload["schema_version"]

    with pytest.raises(BridgeError, match="no schema_version"):
        loads(json.dumps(payload).encode("utf-8"))


def test_malformed_bytes_are_refused() -> None:
    """JSON でないバイト列は BridgeError になる（素の例外を上に出さない）。"""
    with pytest.raises(BridgeError, match="not valid UTF-8 JSON"):
        loads(b"\x00\x01not json")


def test_dumps_is_human_readable_and_stable() -> None:
    """置き場の中身は人が読めて diff が取れる（キー順が安定している）。"""
    bundle = bundle_probe(_trained_probe(), _spec())
    first = dumps(bundle)
    second = dumps(bundle)

    assert first == second
    text = first.decode("utf-8")
    assert "\n" in text, "indent された JSON であること"
    assert text.index('"bias"') < text.index('"weights"'), "キーが辞書順であること"


def test_artifact_key_carries_the_version() -> None:
    """置き場のキーに版が出る（設計書 §4.8 と同じ形）。"""
    assert artifact_key(_spec()) == "probes/Running/1.0.0/probe.json"


def test_artifact_key_rejects_latest() -> None:
    """``latest`` を置き場のキーにできない。"""
    with pytest.raises(BridgeError, match="latest"):
        artifact_key(_spec(probe_id="https://sbp.example/probe/Running/latest"))


def test_artifact_key_requires_name_and_version() -> None:
    """``<ローカル名>/<版>`` の形を持たない IRI は拒否する。"""
    with pytest.raises(BridgeError, match="local name"):
        artifact_key(_spec(probe_id="Running"))


def test_temperature_state_must_be_positive() -> None:
    """温度が 0 以下の束ねは構築できない（sigmoid(logit / T) が定義されない）。"""
    with pytest.raises(ValueError, match="greater_than"):
        TemperatureState(temperature=0.0)


def test_temperature_scaler_restore_rejects_nonpositive() -> None:
    """``restore`` も 0 以下の温度を拒否する。"""
    with pytest.raises(CalibrationError, match="positive"):
        TemperatureScaler().restore(temperature=-1.0)
