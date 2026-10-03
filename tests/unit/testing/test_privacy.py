"""潜在復元攻撃のテスト（不変条件 9 の検証、M7-06）。

**ここで証明したいことと、証明できないことを分けて書く。**

証明したいこと: 保存している潜在から、**個体を同定できない**こと。不変条件 9 が守りたいのは
「外観埋め込みを保存しなければ人を追跡できない」という性質であり、潜在がその抜け道になって
いないかを測る。

証明できないこと: 「どんな攻撃者にも安全」。ここの攻撃者は線形回帰と cosine 最近傍しか使わない。
深い復号器を学習させればもっと戻せる可能性がある。

**属性が復元できること自体は違反ではない。** 潜在から「軸受温度」が戻るのはむしろ基盤が
機能している証拠である（プローブが状態を読めるのと同じ理屈）。危険なのは個体の同定だけで、
両者を混同すると「潜在は何も表現してはいけない」という使えない結論になる。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from sbp.testing.privacy import (
    PrivacyAudit,
    PrivacyAuditError,
    attribute_recovery,
    identity_linkage,
)
from sbp.testing.synth import SynthConfig, SynthWorld

_START = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
_ENTITIES = 24
_LATENT_DIM = 8


def _world() -> SynthWorld:
    """標本を多めに取った合成世界（分割して評価できるように）。"""
    return SynthWorld(
        seed=7,
        config=SynthConfig(
            start=_START,
            duration=timedelta(hours=3),
            step=timedelta(seconds=5),
            cycles=4,
        ),
    )


def _fleet_latents(*, fingerprint: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """同型の設備 N 台を 1 つの符号化器で見た潜在を、2 つの時間帯について作る。

    全台が**同じ状態軌跡**を持つ（同型の設備で、同じ運転をしている）ようにする。こうすると
    潜在に残る個体差はノイズと ``fingerprint`` だけになり、**同定が当たったときの原因が
    1 つに絞れる**。台ごとに別の基底を使うと、基底の違いが指紋になって当然当たるが、それは
    合成データの作りの問題であって潜在の性質ではない。

    Args:
        fingerprint: 個体ごとの固定オフセットの大きさ。0 なら個体差はノイズだけ。
        seed: 乱数種。

    Returns:
        ``(時間帯 A, 時間帯 B)`` の潜在。行が個体で、行の順序は両者で対応する。
    """
    rng = np.random.default_rng(seed)
    base = np.asarray(_world().latents("mid"), dtype=np.float64)[:, :_LATENT_DIM]
    half = base.shape[0] // 2
    state_a = base[:half].mean(axis=0)
    state_b = base[half:].mean(axis=0)

    offsets = rng.normal(0.0, 1.0, size=(_ENTITIES, _LATENT_DIM)) * fingerprint
    noise_a = rng.normal(0.0, 0.3, size=(_ENTITIES, _LATENT_DIM))
    noise_b = rng.normal(0.0, 0.3, size=(_ENTITIES, _LATENT_DIM))
    return state_a + offsets + noise_a, state_b + offsets + noise_b


class TestAttributeRecovery:
    """潜在から観測量を戻す（陽性対照になる攻撃）。"""

    def test_bearing_temperature_is_recoverable(self) -> None:
        """軸受温度は潜在から戻る。**これは違反ではない。**

        潜在は状態を表現するために作られており、状態は温度を決めている。戻らなかったら
        プローブも動かない。この攻撃が成功することが、他の攻撃の結果を読む前提になる。
        """
        world = _world()
        latents = np.asarray(world.latents("mid"), dtype=np.float64)
        temperature = np.asarray(world.observations, dtype=np.float64)[
            :, world.channels.index("bearing_temperature")
        ]
        result = attribute_recovery(latents, temperature, name="bearing temperature", seed=3)
        assert result.recovered
        assert result.score > 0.8

    def test_in_sample_scoring_is_refused(self) -> None:
        """標本が足りなければ measure しない（過適合を漏洩と読み違えない）。"""
        rng = np.random.default_rng(1)
        with pytest.raises(PrivacyAuditError, match="need more samples"):
            attribute_recovery(
                rng.normal(size=(12, 16)),
                rng.normal(size=12),
                name="too small",
                seed=1,
            )

    def test_pure_noise_is_not_recoverable(self) -> None:
        """潜在と無関係な値は戻らない（攻撃が何でも当てるわけではない）。"""
        rng = np.random.default_rng(5)
        result = attribute_recovery(
            rng.normal(size=(600, 8)),
            rng.normal(size=600),
            name="unrelated noise",
            seed=2,
        )
        assert not result.recovered

    def test_mismatched_shapes_are_refused(self) -> None:
        """形が合わなければ落とす。"""
        rng = np.random.default_rng(9)
        with pytest.raises(PrivacyAuditError, match="do not line up"):
            attribute_recovery(
                rng.normal(size=(100, 4)), rng.normal(size=50), name="mismatch", seed=1
            )


class TestIdentityLinkage:
    """個体の同定（本命の攻撃）。"""

    def test_fingerprinted_latents_are_linkable(self) -> None:
        """個体固定のオフセットがあれば同定できる——**攻撃の実装が動くことの確認**。"""
        window_a, window_b = _fleet_latents(fingerprint=3.0, seed=11)
        result = identity_linkage(window_a, window_b, name="fingerprinted latents")
        assert result.recovered
        assert result.score > 0.8

    def test_state_only_latents_are_not_linkable(self) -> None:
        """状態しか載っていない潜在からは同定できない（不変条件 9 が狙う性質）。"""
        window_a, window_b = _fleet_latents(fingerprint=0.0, seed=11)
        result = identity_linkage(window_a, window_b, name="state-only latents")
        assert not result.recovered
        assert result.chance == pytest.approx(1.0 / _ENTITIES)

    def test_misaligned_windows_are_refused(self) -> None:
        """個体数が違う 2 窓は突き合わせられない。"""
        rng = np.random.default_rng(4)
        with pytest.raises(PrivacyAuditError, match="same individuals"):
            identity_linkage(rng.normal(size=(10, 4)), rng.normal(size=(9, 4)))

    def test_single_entity_is_refused(self) -> None:
        """1 個体では当たって当然なので測らない（chance = 1.0）。"""
        rng = np.random.default_rng(4)
        with pytest.raises(PrivacyAuditError, match="at least 2"):
            identity_linkage(rng.normal(size=(1, 4)), rng.normal(size=(1, 4)))


class TestAuditConclusiveness:
    """陽性対照の無い監査は結論にしない。"""

    def test_audit_with_working_control_concludes_no_leak(self) -> None:
        """対照が成功し本命が失敗 → 「線形・最近傍の範囲で漏れなし」。"""
        control_a, control_b = _fleet_latents(fingerprint=3.0, seed=11)
        target_a, target_b = _fleet_latents(fingerprint=0.0, seed=11)
        audit = PrivacyAudit(
            control=identity_linkage(control_a, control_b, name="fingerprinted (control)"),
            target=identity_linkage(target_a, target_b, name="state-only (target)"),
        )
        assert audit.is_conclusive
        assert not audit.leaks
        assert "漏洩なし" in audit.explain()

    def test_failed_control_makes_the_audit_inconclusive(self) -> None:
        """対照が失敗したら、本命が失敗していても**漏洩ありとして扱う**（安全側）。

        攻撃の実装や標本が壊れていた可能性と区別できないため、「何も復元できなかった」を
        安全の証拠にしない。
        """
        target_a, target_b = _fleet_latents(fingerprint=0.0, seed=11)
        audit = PrivacyAudit(
            control=identity_linkage(target_a, target_b, name="broken control"),
            target=identity_linkage(target_a, target_b, name="state-only (target)"),
        )
        assert not audit.is_conclusive
        assert audit.leaks
        assert "監査不成立" in audit.explain()

    def test_leaking_target_is_reported(self) -> None:
        """本命が成功したら漏洩として報告する。"""
        control_a, control_b = _fleet_latents(fingerprint=3.0, seed=11)
        audit = PrivacyAudit(
            control=identity_linkage(control_a, control_b, name="fingerprinted (control)"),
            target=identity_linkage(control_a, control_b, name="stored latents"),
        )
        assert audit.is_conclusive
        assert audit.leaks
        assert "漏洩:" in audit.explain()

    def test_lift_is_zero_when_chance_is_certain(self) -> None:
        """当て推量が 1.0 のとき lift は 0（0 除算で無限大にしない）。"""
        control_a, control_b = _fleet_latents(fingerprint=3.0, seed=11)
        result = identity_linkage(control_a, control_b)
        certain = type(result)(
            name=result.name, score=1.0, chance=1.0, samples=result.samples, detail=""
        )
        assert certain.lift == 0.0
        assert not certain.recovered
