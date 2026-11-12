"""説明サービスの単体テスト（設計書 §8.8）。

重点:

1. **説明は両側を挙げる** — 記号側も潜在側も空の説明は作れない。
2. **寄与率は信念更新から取る（再計算しない）** — 説明と信念が食い違わないように。
3. **当時の版を持つ** — 版の無い説明は監査に使えない。
4. **監査隔離で除外した記録も説明に出る** — 隔離が働いたことを後から確認できる。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sbp.core import vocab
from sbp.core.errors import ReasoningError
from sbp.core.records import Record
from sbp.core.time import Interval
from sbp.reasoning.audit import AuditMask
from sbp.reasoning.belief import BeliefUpdate, SourceContribution
from sbp.reasoning.explain import (
    Explanation,
    LatentSide,
    SymbolicSide,
    explain_belief,
    explain_discrepancy,
)
from sbp.reasoning.reconciliation import (
    Discrepancy,
    DiscrepancyKind,
    Severity,
)

T0 = datetime(2026, 9, 12, 9, 41, 30, tzinfo=UTC)
PUMP = "https://sbp.example/site/siteA/asset/P-101"
ASSERTION = "https://sbp.example/site/siteA/assertion/a-1"
PROBE = "https://sbp.example/site/siteA/probe/running-v3"
VERSION = vocab.ONTOLOGY_VERSION


@pytest.fixture
def mes_record() -> Record:
    return Record(
        record_type=vocab.ent.OperationLog,
        source=vocab.ent.MesSource,
        external_id="OL-0912-A",
        is_about=PUMP,
        recorded_at=T0,
        occurred_at=T0,
        payload={"running": 1},
        ontology_version=VERSION,
    )


@pytest.fixture
def update(mes_record: Record) -> BeliefUpdate:
    """設計書 §14 の事後（観測が支配、0.13 付近）。"""
    mask = AuditMask.auditing_record(mes_record, reason="不一致 D2 の監査")
    return BeliefUpdate(
        fluent="running",
        entity=PUMP,
        at=T0,
        posterior=0.1302,
        prior=0.94,
        contribution=SourceContribution(observed=2.31, record=1.34, plan=0.97),
        excluded_records=1,
        audit_note=mask.provenance_note(),
    )


@pytest.fixture
def discrepancy() -> Discrepancy:
    return Discrepancy(
        kind=DiscrepancyKind.STATE,
        subjects=(PUMP,),
        assertions=(vocab.ent.Running,),
        evidence=("record:OL-0912-A",),
        detected_at=T0 + timedelta(minutes=4),
        interval=Interval(T0, T0 + timedelta(minutes=14)),
        severity=Severity(
            score=0.62, value_at_stake=8_450_000.0, confidence=0.87, persistence_s=840.0
        ),
        detector="kind2",
        ontology_version=VERSION,
    )


class TestExplanationStructure:
    def test_an_explanation_without_grounds_is_rejected(self) -> None:
        with pytest.raises(ReasoningError, match="cites no evidence on either side"):
            Explanation(
                subject=ASSERTION,
                as_of=T0,
                symbolic=SymbolicSide(),
                latent=LatentSide(),
                contribution=None,
                dominant=None,
                posterior=0.5,
                ontology_version=VERSION,
            )

    def test_probe_outputs_must_line_up_with_probes(self) -> None:
        """どのプローブがどの値を出したか分からない説明は根拠にならない。"""
        with pytest.raises(ReasoningError, match="which probe produced which value"):
            LatentSide(probes=(PROBE,), probe_outputs=(0.1, 0.2))

    def test_narrative_does_not_replace_the_graph(
        self, update: BeliefUpdate, mes_record: Record
    ) -> None:
        """文章化は上に載るだけ。根拠の集合は変わらない。"""
        base = explain_belief(
            update,
            subject=ASSERTION,
            probes=(PROBE,),
            probe_outputs=(0.08,),
            records=[mes_record],
            ontology_version=VERSION,
        )
        narrated = base.with_narrative("09:41 に停止したと判断した。MES は稼働と記録している。")
        assert narrated.symbolic == base.symbolic
        assert narrated.latent == base.latent
        assert narrated.contribution == base.contribution
        assert "09:41" in narrated.narrative
        assert base.narrative == ""


class TestExplainBelief:
    def test_it_reuses_the_contribution_from_the_update(
        self, update: BeliefUpdate, mes_record: Record
    ) -> None:
        explanation = explain_belief(
            update,
            subject=ASSERTION,
            probes=(PROBE,),
            probe_outputs=(0.08,),
            records=[mes_record],
            rules=("stopped_after_stop_cmd",),
            wm_version="https://sbp.example/model/koopman-v1",
            ontology_version=VERSION,
        )
        assert explanation.contribution is update.contribution
        assert explanation.dominant == "observed"
        assert explanation.posterior == pytest.approx(0.1302)
        assert explanation.as_of == T0
        assert explanation.is_reproducible

    def test_excluded_records_are_visible(self, update: BeliefUpdate, mes_record: Record) -> None:
        """不変条件 6: 隔離が働いたことを説明から確認できる。"""
        explanation = explain_belief(
            update,
            subject=ASSERTION,
            probes=(PROBE,),
            probe_outputs=(0.08,),
            excluded_records=[mes_record],
            ontology_version=VERSION,
        )
        assert explanation.symbolic.excluded_records == (str(mes_record.record_id),)
        assert "excluded" in explanation.audit_note
        assert "D2" in explanation.audit_note

    def test_the_latent_side_names_the_model_version(self, update: BeliefUpdate) -> None:
        """「当時のモデル」を特定できなければ説明を再構成できない。"""
        explanation = explain_belief(
            update,
            subject=ASSERTION,
            probes=(PROBE,),
            probe_outputs=(0.08,),
            wm_version="https://sbp.example/model/koopman-v1",
            attribution={"vibration_rms": 0.7, "motor_current": 0.2},
            ontology_version=VERSION,
        )
        assert explanation.latent.wm_version == "https://sbp.example/model/koopman-v1"
        assert explanation.latent.attribution["vibration_rms"] == pytest.approx(0.7)


class TestExplainDiscrepancy:
    def test_it_defaults_the_rule_to_the_detector(self, discrepancy: Discrepancy) -> None:
        explanation = explain_discrepancy(discrepancy, probes=(PROBE,), probe_outputs=(0.08,))
        assert explanation.symbolic.rules == ("kind2",)
        assert explanation.symbolic.records == ("record:OL-0912-A",)
        assert explanation.posterior == pytest.approx(0.62)

    def test_the_observation_window_matches_the_interval(self, discrepancy: Discrepancy) -> None:
        explanation = explain_discrepancy(discrepancy, probes=(PROBE,), probe_outputs=(0.08,))
        assert explanation.latent.observation_window is not None
        start, end = explanation.latent.observation_window
        assert start == discrepancy.interval.start.isoformat()
        assert end.startswith("2026-09-12T09:55")

    def test_the_contribution_comes_from_the_belief_update(
        self, discrepancy: Discrepancy, update: BeliefUpdate
    ) -> None:
        explanation = explain_discrepancy(
            discrepancy, update=update, probes=(PROBE,), probe_outputs=(0.08,)
        )
        assert explanation.dominant == "observed"
        assert explanation.contribution is not None
        assert explanation.contribution.record == pytest.approx(1.34)

    def test_without_a_belief_update_there_is_no_contribution(
        self, discrepancy: Discrepancy
    ) -> None:
        """寄与率を持たない不一致（規範違反など）で嘘の寄与率を作らない。"""
        explanation = explain_discrepancy(discrepancy, probes=(PROBE,), probe_outputs=(0.08,))
        assert explanation.contribution is None
        assert explanation.dominant is None

    def test_a_norm_discrepancy_cites_its_norm(self) -> None:
        norm_discrepancy = Discrepancy(
            kind=DiscrepancyKind.NORM,
            subjects=(PUMP,),
            evidence=("https://sbp.example/doc/maintenance-standard",),
            detected_at=T0,
            interval=Interval(T0, T0 + timedelta(hours=1)),
            severity=Severity(
                score=0.7, value_at_stake=1.0, confidence=0.9, persistence_s=3600.0, norm_weight=1.0
            ),
            detector="kind5:inspection_interval",
            ontology_version=VERSION,
        )
        explanation = explain_discrepancy(norm_discrepancy, norms=("https://sbp.example/norm/n-1",))
        assert explanation.symbolic.norms == ("https://sbp.example/norm/n-1",)
        # 規範違反は記号側だけで説明が成立する（潜在側が空でも通る）
        assert explanation.latent.is_empty
