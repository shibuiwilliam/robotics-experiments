"""プローブの束の単体テスト（M8-02、ADR-0009）。

重点:

1. **``active`` だけを走らせる** — `candidate` はシャドウ評価の対象で、その出力は主信念に
   入らない（設計書 §5.2）。
2. **空の束を作らせない** — 空の束は「何も起きていない」と同じ姿をしていて、接地が
   止まっていることと区別できない（所見 32）。
3. **1 つの潜在空間に縛る** — 違う WM 版を混ぜると、次元が偶然一致すれば動いてしまい、
   出力が無意味になる（設計書 §4.8）。
4. **較正済みの値しか返さない**（不変条件 7）。
"""

from __future__ import annotations

import numpy as np
import pytest

from sbp.bridge.calibration import TemperatureScaler
from sbp.bridge.probes import (
    LinearProbe,
    ProbeKind,
    ProbeMetrics,
    ProbeSpec,
    ProbeStatus,
)
from sbp.bridge.runner import ProbeSet, fluent_name
from sbp.core.errors import BridgeError

WM_A = "https://sbp.example/wm/koopman/0.1.0"
WM_B = "https://sbp.example/wm/koopman/0.2.0"
RUNNING = "https://sbp.example/core#Running"
FAULTED = "https://sbp.example/core#Faulted"
DIM = 4


def _metrics() -> ProbeMetrics:
    return ProbeMetrics(auroc=0.95, ece=0.02, alignment_residual=0.05, n_samples=200)


def _spec(
    *,
    grounds: str = RUNNING,
    status: ProbeStatus = ProbeStatus.ACTIVE,
    wm_version: str = WM_A,
    probe_id: str | None = None,
    sites: tuple[str, ...] = (),
    modes: tuple[str, ...] = (),
) -> ProbeSpec:
    local = fluent_name(grounds)
    return ProbeSpec(
        probe_id=probe_id or f"https://sbp.example/probe/{local}/1.0.0",
        grounds=grounds,
        kind=ProbeKind.CLASSIFIER,
        wm_version=wm_version,
        status=status,
        metrics=_metrics() if status is ProbeStatus.ACTIVE else None,
        validity_sites=sites,
        operating_modes=modes,
        ontology_version="0.5.0",
    )


def _probe(*, dim: int = DIM, seed: int = 3) -> LinearProbe:
    """学習済み・較正済みのプローブ。"""
    rng = np.random.default_rng(seed)
    latents = rng.normal(size=(80, dim))
    labels = latents[:, 0] > 0.0
    probe = LinearProbe(dim=dim, seed=seed).fit(latents, labels, epochs=80)
    probe.attach_calibrator(TemperatureScaler().fit(probe.logits(latents), labels))
    return probe


def test_runs_every_active_probe() -> None:
    """全 ``active`` プローブの出力がフルーエント名で返る。"""
    probe_set = ProbeSet(
        [(_probe(), _spec(grounds=RUNNING)), (_probe(seed=5), _spec(grounds=FAULTED))]
    )

    outputs = probe_set(np.zeros((6, DIM)))

    assert set(outputs) == {"Running", "Faulted"}
    assert all(values.shape == (6,) for values in outputs.values())
    assert probe_set.fluents == ("Faulted", "Running")
    assert len(probe_set) == 2


def test_probabilities_are_calibrated() -> None:
    """返るのは較正済み確率（不変条件 7）。

    較正器が無いプローブは :func:`LinearProbe.predict` が落ちるので、束が値を返せたこと自体が
    較正を通った証拠になる。値域の検査も入れておく。
    """
    probe_set = ProbeSet([(_probe(), _spec())])
    values = probe_set(np.random.default_rng(1).normal(size=(10, DIM)))["Running"]

    assert np.all((values >= 0.0) & (values <= 1.0))


def test_candidate_probes_do_not_run() -> None:
    """``candidate`` は走らない（シャドウ評価の対象であって主信念に入らない）。"""
    probe_set = ProbeSet(
        [
            (_probe(), _spec(grounds=RUNNING)),
            (_probe(seed=5), _spec(grounds=FAULTED, status=ProbeStatus.CANDIDATE)),
        ]
    )

    assert probe_set.fluents == ("Running",)


def test_an_empty_set_is_refused() -> None:
    """走るプローブが 0 本なら構築で落ちる。

    空の束で接地を続けると、下流は「変化が無かった」と読む。
    """
    with pytest.raises(BridgeError, match="no active probe"):
        ProbeSet([(_probe(), _spec(status=ProbeStatus.CANDIDATE))])


def test_no_entries_at_all_is_refused() -> None:
    """そもそも 1 件も渡されなかった場合も落ちる。"""
    with pytest.raises(BridgeError, match="no active probe"):
        ProbeSet([])


def test_site_scope_is_applied() -> None:
    """拠点の範囲外のプローブは走らない。"""
    with pytest.raises(BridgeError, match="no active probe"):
        ProbeSet([(_probe(), _spec(sites=("siteB",)))], site="siteA")

    probe_set = ProbeSet([(_probe(), _spec(sites=("siteA",)))], site="siteA")
    assert probe_set.fluents == ("Running",)


def test_operating_mode_scope_is_applied() -> None:
    """運転モードの範囲外のプローブは走らない。"""
    with pytest.raises(BridgeError, match="no active probe"):
        ProbeSet([(_probe(), _spec(modes=("startup",)))], operating_mode="steady")


def test_mixing_wm_versions_is_refused() -> None:
    """違う WM 版のプローブを 1 つの束に入れられない。

    同じ潜在行列を 2 つの空間として解釈することになる。次元が偶然一致していれば動いてしまい、
    出力は無意味になる（設計書 §4.8）。
    """
    with pytest.raises(BridgeError, match="one latent space"):
        ProbeSet(
            [
                (_probe(), _spec(grounds=RUNNING, wm_version=WM_A)),
                (_probe(seed=5), _spec(grounds=FAULTED, wm_version=WM_B)),
            ]
        )


def test_mixing_dimensions_is_refused() -> None:
    """次元の違うプローブを 1 つの束に入れられない。"""
    with pytest.raises(BridgeError, match="share the latent dimension"):
        ProbeSet(
            [
                (_probe(dim=DIM), _spec(grounds=RUNNING)),
                (_probe(dim=DIM + 2, seed=5), _spec(grounds=FAULTED)),
            ]
        )


def test_two_active_probes_for_one_fluent_are_refused() -> None:
    """同じフルーエントに ``active`` が 2 本あると落ちる。

    どちらがそのフルーエントを代表するかが任意になる。昇格のときに旧版を
    ``deprecated`` にする運用が前提である。
    """
    with pytest.raises(BridgeError, match="two active probes"):
        ProbeSet(
            [
                (_probe(), _spec(probe_id="https://sbp.example/probe/Running/1.0.0")),
                (_probe(seed=5), _spec(probe_id="https://sbp.example/probe/Running/2.0.0")),
            ]
        )


def test_latent_shape_must_match() -> None:
    """潜在の形が束の次元と合わなければ落ちる。"""
    probe_set = ProbeSet([(_probe(), _spec())])

    with pytest.raises(BridgeError, match="expects latents of shape"):
        probe_set(np.zeros((5, DIM + 1)))
    with pytest.raises(BridgeError, match="expects latents of shape"):
        probe_set(np.zeros(DIM))


def test_probe_ids_are_exposed_for_provenance() -> None:
    """フルーエント → プローブ IRI が取れる（イベントの証拠に使う）。"""
    probe_set = ProbeSet([(_probe(), _spec())])
    assert probe_set.probe_ids() == {"Running": "https://sbp.example/probe/Running/1.0.0"}
    assert probe_set.spec_of("Running").grounds == RUNNING


def test_metadata_is_exposed() -> None:
    """束が属する潜在空間が読める（ワーカーが WM 版の一致を検査する）。"""
    probe_set = ProbeSet([(_probe(), _spec())])
    assert probe_set.wm_version == WM_A
    assert probe_set.latent_dim == DIM


@pytest.mark.parametrize(
    ("grounds", "expected"),
    [
        ("https://sbp.example/core#Running", "Running"),
        ("https://sbp.example/ent/Faulted", "Faulted"),
        ("urn:sbp:x#UnderMaintenance", "UnderMaintenance"),
    ],
)
def test_fluent_name_extraction(grounds: str, expected: str) -> None:
    """概念 IRI からローカル名を取る。"""
    assert fluent_name(grounds) == expected


def test_fluent_name_requires_a_local_part() -> None:
    """ローカル名が取れない IRI は拒否する。"""
    with pytest.raises(BridgeError, match="fluent local name"):
        fluent_name("https://sbp.example/core#")
