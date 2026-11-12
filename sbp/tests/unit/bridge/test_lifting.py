"""事象昇格の単体テスト（設計書 §5.3、§5.8）。

検証の軸は 3 つ:

1. **変化点検出が正しく働く** — ステップは拾い、定常とちらつきは拾わない。
2. **逆写像が原因を判別する** — Running が落ちたとき Fault か StopCmd かを他プローブで分ける
   （設計書 §14 の「Fault 0.56 / StopCmd 0.35」に対応）。
3. **点時刻のイベントを出さない** — 区間に必ず ``uncertainty_s`` が入る。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from sbp.bridge.bocpd import BocpdDetector, apply_hysteresis, detect_change_points
from sbp.bridge.calibration import TemperatureScaler
from sbp.bridge.lifting import (
    EventCalculusRules,
    lift_events,
    load_event_calculus,
)
from sbp.bridge.probes import LinearProbe
from sbp.core.config import BridgeSettings
from sbp.core.errors import BridgeError
from sbp.core.modality import Modality
from sbp.testing import LATENT_DIM, Fluent, SynthConfig, SynthWorld

T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[3]
PUMP = "https://sbp.example/site/siteA/asset/P-101"
VERSION = "0.3.0"


@pytest.fixture(scope="module")
def rules() -> EventCalculusRules:
    """``ontology/process/equipment-maintenance.lp`` から読んだ事象計算。"""
    return load_event_calculus(
        (REPO_ROOT / "ontology" / "process" / "equipment-maintenance.lp").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture(scope="module")
def world() -> SynthWorld:
    """§14 の筋書き（異常 → 故障停止）を含む合成世界。"""
    return SynthWorld(config=SynthConfig(start=T0, duration=timedelta(hours=2), cycles=2), seed=7)


def _timestamps(count: int, step_s: int = 10) -> list[datetime]:
    return [T0 + timedelta(seconds=step_s * i) for i in range(count)]


class TestHysteresis:
    def test_two_thresholds_suppress_flicker(self) -> None:
        """閾値 1 本なら往復する系列が、2 本なら安定する（設計書 §5.3 手順 2）。"""
        # 0.5 付近を往復する確率列
        probabilities = np.array([0.45, 0.55, 0.45, 0.55, 0.45, 0.55])
        with_hysteresis = apply_hysteresis(probabilities, high=0.7, low=0.3)
        single_threshold = probabilities >= 0.5
        assert single_threshold.sum() == 3, "閾値 1 本なら 3 回成立する"
        assert with_hysteresis.sum() == 0, "ヒステリシスなら 1 度も成立に入らない"

    def test_rise_requires_crossing_high(self) -> None:
        probabilities = np.array([0.1, 0.6, 0.8, 0.5, 0.2])
        binary = apply_hysteresis(probabilities, high=0.7, low=0.3)
        assert list(binary) == [False, False, True, True, False]

    def test_equal_thresholds_are_rejected(self) -> None:
        with pytest.raises(BridgeError, match="high > low"):
            detect_change_points(np.array([0.1, 0.9]), high=0.5, low=0.5)


class TestBocpd:
    def test_detects_a_clean_step(self) -> None:
        observations = np.array([True] * 50 + [False] * 50)
        detected = BocpdDetector().detect(observations)
        assert len(detected) == 1
        assert detected[0].index == 50
        assert detected[0].direction == "fall"

    def test_detects_multiple_changes(self) -> None:
        observations = np.array([True] * 30 + [False] * 30 + [True] * 30)
        detected = BocpdDetector().detect(observations)
        assert [c.index for c in detected] == [30, 60]
        assert [c.direction for c in detected] == ["fall", "rise"]

    def test_constant_series_has_no_change_points(self) -> None:
        assert BocpdDetector().detect(np.array([True] * 100)) == []

    def test_fast_flicker_produces_no_change_points(self) -> None:
        """1 標本ごとの往復は変化点にならない（区間が成立しないため）。"""
        observations = np.array([i % 2 == 0 for i in range(100)])
        assert BocpdDetector().detect(observations) == []

    def test_detection_is_robust_to_the_hazard_rate(self) -> None:
        """ベイズ因子で判定するのでハザード率を変えても同じ変化点が出る。

        絶対閾値を使うと「適切な閾値がハザード率に依存する」という扱いにくさが出る。
        """
        observations = np.array([True] * 50 + [False] * 50)
        for hazard in (1 / 60, 1 / 120, 1 / 300):
            detected = BocpdDetector(hazard=hazard).detect(observations)
            assert [c.index for c in detected] == [50], f"hazard={hazard} で結果が変わった"

    def test_empty_observations_are_rejected(self) -> None:
        with pytest.raises(BridgeError, match="at least one observation"):
            BocpdDetector().run_length_posterior(np.array([], dtype=bool))

    def test_bayes_factor_must_exceed_one(self) -> None:
        """事前だけで通る基準は基準でない。"""
        with pytest.raises(ValueError, match=r"must exceed 1\.0"):
            BocpdDetector().detect(np.array([True, False]), min_bayes_factor=0.5)

    def test_change_probability_is_data_dependent(self) -> None:
        """変化点で事後確率が事前（ハザード率）を大きく上回る。

        ここが BOCPD の核心である。実装の初版は変化点項に継続中の run の予測確率を使って
        いたため、正規化で打ち消されて事後確率がハザード率に張り付いていた。
        """
        detector = BocpdDetector(hazard=1 / 120)
        posterior = detector.run_length_posterior(np.array([True] * 50 + [False] * 50))
        change_probability = posterior[1:, 0]
        at_change = float(change_probability[50])
        baseline = float(change_probability[25])
        assert at_change / baseline > 10, (
            f"変化点の事後確率がデータに反応していない: {at_change} vs {baseline}"
        )


class TestEventCalculusLoading:
    def test_rules_are_read_from_the_lp_file(self, rules: EventCalculusRules) -> None:
        """規則の内容は ``.lp`` が正（Python 側で組み立てない）。"""
        assert ("start_cmd", "running") in rules.initiates
        assert ("stop_cmd", "running") in rules.terminates
        assert ("fault", "faulted") in rules.initiates

    def test_inverse_mapping_lists_candidates(self, rules: EventCalculusRules) -> None:
        """Running の終了候補は StopCmd と Fault の 2 つ（設計書 §5.3 手順 3）。"""
        assert rules.candidates_for_fall("running") == ("fault", "stop_cmd")

    def test_empty_rules_are_rejected(self) -> None:
        with pytest.raises(BridgeError, match=r"no initiates/terminates"):
            load_event_calculus("% only comments\nfluent_type(x).\n")


class TestEventLifting:
    def _probe_outputs(
        self, world: SynthWorld, fluents: tuple[Fluent, ...]
    ) -> tuple[dict[str, np.ndarray], dict[str, str]]:
        latents = world.latents("mid")
        outputs: dict[str, np.ndarray] = {}
        probe_ids: dict[str, str] = {}
        for fluent in fluents:
            labels = world.oracle.labels_for(fluent)
            if labels.all() or not labels.any():
                continue
            probe = LinearProbe(dim=LATENT_DIM, seed=3).fit(latents, labels, epochs=400)
            probe.attach_calibrator(TemperatureScaler().fit(probe.logits(latents), labels))
            outputs[fluent.value] = probe.predict(latents).probabilities
            probe_ids[fluent.value] = (
                f"https://sbp.example/site/siteA/probe/{fluent.value.lower()}-v1"
            )
        return outputs, probe_ids

    def test_lifts_the_stop_event(self, world: SynthWorld, rules: EventCalculusRules) -> None:
        """真値の停止時刻に対応する事象が昇格する。"""
        outputs, probe_ids = self._probe_outputs(
            world, (Fluent.RUNNING, Fluent.VIBRATION_ANOMALY, Fluent.FAULTED)
        )
        events = lift_events(
            subject=PUMP,
            probe_outputs=outputs,
            timestamps=world.oracle.timestamps,
            rules=rules,
            settings=BridgeSettings(),
            probe_ids=probe_ids,
        )
        truth_stop = world.oracle.stop_event_at
        assert truth_stop is not None
        running_falls = [
            e for e in events if e.fluent == "running" and e.valid_time.start == truth_stop
        ]
        assert len(running_falls) == 1, f"停止時刻 {truth_stop} の事象が昇格していない"

    def test_inverse_mapping_favours_fault_when_vibration_is_anomalous(
        self, world: SynthWorld, rules: EventCalculusRules
    ) -> None:
        """設計書 §14: 振動異常を伴う停止は Fault が優勢になる。

        これが「逆写像が候補を尤度で重み付ける」ことの実際の意味である。
        """
        outputs, probe_ids = self._probe_outputs(
            world, (Fluent.RUNNING, Fluent.VIBRATION_ANOMALY, Fluent.FAULTED)
        )
        events = lift_events(
            subject=PUMP,
            probe_outputs=outputs,
            timestamps=world.oracle.timestamps,
            rules=rules,
            settings=BridgeSettings(),
            probe_ids=probe_ids,
        )
        truth_stop = world.oracle.stop_event_at
        assert truth_stop is not None
        assert world.oracle.stop_cause == "Fault", "この合成世界の停止原因は Fault のはず"

        event = next(
            e for e in events if e.fluent == "running" and e.valid_time.start == truth_stop
        )
        pmf = event.inferred_event_type.pmf
        assert pmf["fault"] > pmf["stop_cmd"], (
            f"故障停止なのに StopCmd が優勢: fault={pmf['fault']:.2f} "
            f"stop_cmd={pmf['stop_cmd']:.2f}"
        )
        assert "other" in pmf, "候補のどれでもない余地（設計書 §5.8 の other）が無い"

    def test_every_lifted_event_carries_uncertainty(
        self, world: SynthWorld, rules: EventCalculusRules
    ) -> None:
        """**点時刻のイベントを出さない**（bridge-worldmodel ルール）。"""
        outputs, probe_ids = self._probe_outputs(world, (Fluent.RUNNING, Fluent.FAULTED))
        events = lift_events(
            subject=PUMP,
            probe_outputs=outputs,
            timestamps=world.oracle.timestamps,
            rules=rules,
            settings=BridgeSettings(),
            probe_ids=probe_ids,
        )
        assert events, "事象が 1 つも昇格していない"
        for event in events:
            assert event.valid_time.uncertainty_s > 0.0, (
                f"{event.fluent} の事象が不確実幅を持っていない"
            )

    def test_lifted_events_are_inferred(self, world: SynthWorld, rules: EventCalculusRules) -> None:
        """不変条件 3: プローブ由来の出力は必ず ``inferred``。"""
        outputs, probe_ids = self._probe_outputs(world, (Fluent.RUNNING,))
        events = lift_events(
            subject=PUMP,
            probe_outputs=outputs,
            timestamps=world.oracle.timestamps,
            rules=rules,
            settings=BridgeSettings(),
            probe_ids=probe_ids,
        )
        envelope = events[0].to_envelope(
            site_id="siteA",
            event_type_iri="https://sbp.example/ent#StopCmd",
            ontology_version=VERSION,
        )
        assert envelope.modality is Modality.INFERRED
        assert envelope.valid_time.uncertainty_s > 0.0

    def test_evidence_records_the_probes(
        self, world: SynthWorld, rules: EventCalculusRules
    ) -> None:
        """根拠となったプローブが証拠に残る（生スコアも含む。不変条件 7）。"""
        outputs, probe_ids = self._probe_outputs(world, (Fluent.RUNNING, Fluent.FAULTED))
        events = lift_events(
            subject=PUMP,
            probe_outputs=outputs,
            timestamps=world.oracle.timestamps,
            rules=rules,
            settings=BridgeSettings(),
            probe_ids=probe_ids,
            wm_version="https://sbp.example/site/siteA/wm/synth-1.0",
        )
        evidence = events[0].evidence
        assert evidence, "証拠が無い"
        assert all(e.kind == "probe" for e in evidence)
        assert all(e.raw_score is not None for e in evidence), "生スコアが残っていない"
        assert all(e.wm_version is not None for e in evidence), "WM 版が残っていない"

    def test_mismatched_lengths_are_rejected(self, rules: EventCalculusRules) -> None:
        with pytest.raises(BridgeError, match="samples but"):
            lift_events(
                subject=PUMP,
                probe_outputs={"Running": np.array([0.9, 0.1])},
                timestamps=_timestamps(5),
                rules=rules,
                settings=BridgeSettings(),
                probe_ids={"Running": "p"},
            )

    def test_no_probe_outputs_is_rejected(self, rules: EventCalculusRules) -> None:
        with pytest.raises(BridgeError, match="without probe outputs"):
            lift_events(
                subject=PUMP,
                probe_outputs={},
                timestamps=_timestamps(5),
                rules=rules,
                settings=BridgeSettings(),
                probe_ids={},
            )

    def test_min_duration_suppresses_short_excursions(self, rules: EventCalculusRules) -> None:
        """最小持続時間を満たさない変化は捨てる（設計書 §5.3 手順 2）。"""
        # 1 標本だけ高くなる系列（10 秒 < 既定の最小持続 20 秒）
        probabilities = np.full(60, 0.1)
        probabilities[30] = 0.95
        events = lift_events(
            subject=PUMP,
            probe_outputs={"Running": probabilities},
            timestamps=_timestamps(60),
            rules=rules,
            settings=BridgeSettings(min_duration_s=60.0),
            probe_ids={"Running": "https://sbp.example/site/siteA/probe/running-v1"},
        )
        assert events == [], "1 標本の跳ね上がりが事象になってしまった"

    def test_sustained_change_is_lifted(self, rules: EventCalculusRules) -> None:
        """最小持続を満たす変化は昇格する（抑制が強すぎないことの確認）。"""
        probabilities = np.concatenate([np.full(30, 0.1), np.full(30, 0.95)])
        events = lift_events(
            subject=PUMP,
            probe_outputs={"Running": probabilities},
            timestamps=_timestamps(60),
            rules=rules,
            settings=BridgeSettings(min_duration_s=60.0),
            probe_ids={"Running": "https://sbp.example/site/siteA/probe/running-v1"},
        )
        assert len(events) == 1
        assert events[0].fluent == "running"
        assert events[0].inferred_event_type.pmf["start_cmd"] > 0.5

    def test_the_envelope_carries_the_state_probability_not_the_event_confidence(
        self, rules: EventCalculusRules
    ) -> None:
        """イベントの確率は「変化後にフルーエントが成立している確率」（ADR-0026）。

        下流（信念更新・照合）は ``PhysicalEvent/<フルーエント>`` の確率を
        フルーエントの確率として読む。
        停止（fall）で高い確率を載せると、停止が「稼働の証拠」として数えられる。
        """
        probabilities = np.concatenate([np.full(30, 0.05), np.full(30, 0.95), np.full(30, 0.05)])
        events = lift_events(
            subject=PUMP,
            probe_outputs={"Running": probabilities},
            timestamps=_timestamps(90),
            rules=rules,
            settings=BridgeSettings(min_duration_s=60.0),
            probe_ids={"Running": "https://sbp.example/site/siteA/probe/running-v1"},
        )
        assert len(events) == 2
        rise, fall = (
            e.to_envelope(
                site_id="siteA",
                event_type_iri="sbp-core:PhysicalEvent/ent:Running",
                ontology_version="0.6.0",
            )
            for e in events
        )
        assert rise.belief.p > 0.8  # type: ignore[union-attr]
        assert fall.belief.p < 0.2  # type: ignore[union-attr]
