"""ゲート G7 の統合テスト（docs/TASK.md M7-07）。

検収内容: **プローブ版を上げて再接地 → ``as_of`` で旧信念が再構成でき、新しい ``tx`` で新信念が
載ることを確認する。**

ゲート G7 の条件:

- G6 維持
- M7-07 緑
- runbooks に従って第三者（別セッション）が再接地を実行できる

**このテストが確かめているのは二時制の存在理由そのものである。** 再接地は「過去の信念を訂正
する」操作ではない。``valid_from`` は過去のまま、``tx_from`` だけが今になる——だから
``as_of=再接地前`` で旧版の判断が、``as_of=再接地後`` で新版の判断が返る。両方が返らなければ、
版を上げるたびに過去の説明が失われることになり、監査が成立しない。

真値は ``sbp.testing`` 経由でのみ参照する（不変条件 10）。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from sbp import knowledge
from sbp.bridge import LinearProbe, TemperatureScaler
from sbp.core import vocab
from sbp.core.assertion import Assertion
from sbp.core.belief import BooleanBelief
from sbp.core.errors import RegroundingError
from sbp.core.metrics import MetricSample, metric, prometheus_exposition
from sbp.core.modality import Modality
from sbp.core.time import Interval, now
from sbp.knowledge import (
    CompatibilityMatrix,
    DiffEntry,
    RegroundingTrigger,
    plan_regrounding,
    summarise_regrounding,
)
from sbp.testing import Fluent, SynthConfig, SynthWorld
from sbp.worldmodel import KoopmanConfig, KoopmanModel

if TYPE_CHECKING:
    from sbp.knowledge import AssertionStore

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

REPO_ROOT = Path(__file__).resolve().parents[2]
PUMP = "https://sbp.example/site/siteA/asset/P-101"
VERSION = vocab.ONTOLOGY_VERSION
WM_VERSION = "https://sbp.example/model/koopman/1.0.0"
PROBE_V1 = "probe:running/1.0.0"
PROBE_V2 = "probe:running/2.0.0"


@pytest.fixture(scope="module")
def world() -> SynthWorld:
    return SynthWorld(
        config=SynthConfig(
            start=now().replace(microsecond=0) - timedelta(hours=4),
            duration=timedelta(hours=4),
            cycles=4,
        ),
        seed=2026,
    )


@pytest.fixture(scope="module")
def latents(world: SynthWorld) -> np.ndarray:
    cut = int(len(world.timestamps) * 0.6)
    model = KoopmanModel.untrained(
        model_id="https://sbp.example/model/koopman",
        version="1.0.0",
        domain=vocab.ent.Pump,
        channels=world.channels,
        config=KoopmanConfig(delays=8),
    ).fit(world.observations[:cut], seed=2026)
    return model.encode_series(world.observations)


@pytest.fixture(scope="module")
def two_probe_versions(world: SynthWorld, latents: np.ndarray) -> dict[str, Any]:
    """同じ概念に対する 2 つのプローブ版（学習量だけが違う）。

    **潜在は同じ**（WM 版は変わっていない）。プローブだけが変わる状況を作る——これが
    ``RegroundingTrigger.PROBE_VERSION`` であり、潜在の再計算は不要である。

    **両方に較正器を付ける。** 未較正のプローブは ``predict()`` が拒否する（不変条件 7: 生スコアを
    信念にしない）。「較正していない旧版」は作れないので、違いは学習量（30 エポック vs 500）で
    付ける——これは現実の版上げに近い（同じ構成で学習を伸ばした版に差し替える）。
    """
    labels = world.oracle.labels_for(Fluent.RUNNING)
    cut = int(len(labels) * 0.6)

    # v1: 学習を早めに打ち切った版（確信度が鈍い）。較正器は付ける（不変条件 7）
    v1 = LinearProbe(dim=latents.shape[1], seed=2026)
    v1.fit(latents[:cut], labels[:cut], epochs=30)
    v1.attach_calibrator(TemperatureScaler().fit(v1.logits(latents[:cut]), labels[:cut]))

    # 新版は十分に学習し較正もかけている
    v2 = LinearProbe(dim=latents.shape[1], seed=2026)
    v2.fit(latents[:cut], labels[:cut], epochs=500)
    v2.attach_calibrator(TemperatureScaler().fit(v2.logits(latents[:cut]), labels[:cut]))

    return {
        "v1": v1,
        "v2": v2,
        "cut": cut,
        "p1": v1.predict(latents).probabilities,
        "p2": v2.predict(latents).probabilities,
        "metrics_v1": v1.evaluate(latents[cut:], labels[cut:], alignment_residual=0.0),
        "metrics_v2": v2.evaluate(latents[cut:], labels[cut:], alignment_residual=0.0),
    }


class TestG7ProbeVersions:
    """再接地する理由（新版が本当に良くなっている）を先に示す。"""

    def test_the_new_version_is_better_calibrated(self, two_probe_versions: dict[str, Any]) -> None:
        """ECE が改善していること。**これが再接地の理由になる。**"""
        before = two_probe_versions["metrics_v1"]
        after = two_probe_versions["metrics_v2"]
        assert after.ece <= before.ece, (
            f"新版の ECE {after.ece:.4f} が旧版 {before.ece:.4f} より悪い。"
            "改善していない版で再接地する理由が無い"
        )

    def test_the_two_versions_disagree_somewhere(self, two_probe_versions: dict[str, Any]) -> None:
        """差が無ければ再接地しても何も起きない（テストが何も確かめられない）。"""
        difference = np.abs(two_probe_versions["p1"] - two_probe_versions["p2"])
        assert difference.max() > 0.1, "2 つの版の出力が実質同じ"


class TestG7Regrounding:
    """設計書 §6.8 の全段階を通す。"""

    @pytest.fixture
    def plan(self, world: SynthWorld):
        """段階 2: スコープとコスト見積り。"""
        return plan_regrounding(
            trigger=RegroundingTrigger.PROBE_VERSION,
            reason="Running プローブを v2 に昇格（ECE が改善）",
            entities=[PUMP],
            fluents=[vocab.ent.Running],
            interval=Interval(world.timestamps[0], world.timestamps[-1]),
            from_version=PROBE_V1,
            to_version=PROBE_V2,
        )

    def test_the_estimate_says_latents_are_reused(self, plan: Any) -> None:
        """プローブ版の昇格では潜在を作り直さない（1 本足すたびに全走査しない）。"""
        assert not plan.estimate.recompute_latents
        assert "潜在は再利用" in plan.estimate.explain()

    def test_the_old_belief_survives_the_regrounding(
        self,
        plan: Any,
        world: SynthWorld,
        two_probe_versions: dict[str, Any],
        assertion_store: AssertionStore,
    ) -> None:
        """**G7 の中心。** ``as_of`` で旧信念が再構成でき、新 ``tx`` で新信念が載る。"""
        predicate = vocab.ent.Running
        sample_indices = list(range(0, two_probe_versions["cut"], 120))

        # ---- 旧版で信念を記録する（過去の取引時刻）----
        old_tx = now().replace(microsecond=0) - timedelta(hours=1)
        old_ids = []
        for index in sample_indices:
            assertion = Assertion(
                subject=PUMP,
                predicate=predicate,
                object=True,
                valid_from=world.timestamps[index],
                tx_from=old_tx,
                modality=Modality.INFERRED,
                belief=BooleanBelief(p=_clamp(two_probe_versions["p1"][index])),
                provenance=f"probe:{PROBE_V1}",
                ontology_version=VERSION,
            )
            assertion_store.assert_(assertion)
            old_ids.append(assertion.assertion_id)

        # ---- 再接地: 旧文を閉じ、新版の判断を新しい tx_from で追加する ----
        reground_at = now().replace(microsecond=0)
        diffs: list[DiffEntry] = []
        for position, index in enumerate(sample_indices):
            # 旧文は **閉じるだけ**（削除しない。不変条件 2）
            assertion_store.retract(old_ids[position], at=reground_at)
            new_probability = _clamp(two_probe_versions["p2"][index])
            assertion_store.assert_(
                Assertion(
                    subject=PUMP,
                    predicate=predicate,
                    # valid_from は **過去のまま**。tx_from だけが今になる
                    object=True,
                    valid_from=world.timestamps[index],
                    tx_from=reground_at,
                    modality=Modality.INFERRED,
                    belief=BooleanBelief(p=new_probability),
                    provenance=f"probe:{PROBE_V2}",
                    ontology_version=VERSION,
                )
            )
            diffs.append(
                DiffEntry(
                    subject=PUMP,
                    predicate=predicate,
                    valid_from=world.timestamps[index],
                    old_confidence=_clamp(two_probe_versions["p1"][index]),
                    new_confidence=new_probability,
                )
            )

        # ---- 旧信念が as_of で再構成できる ----
        for index in sample_indices:
            valid_at = world.timestamps[index] + timedelta(seconds=1)
            before = assertion_store.query(
                subject=PUMP,
                predicate=predicate,
                as_of=reground_at - timedelta(seconds=1),
                valid_at=valid_at,
            )
            after = assertion_store.query(
                subject=PUMP,
                predicate=predicate,
                as_of=reground_at + timedelta(seconds=1),
                valid_at=valid_at,
            )
            assert before, f"再接地前の信念が失われた（valid_at={valid_at.isoformat()}）"
            assert after, f"再接地後の信念が載っていない（valid_at={valid_at.isoformat()}）"
            assert before[0].provenance.endswith(PROBE_V1)
            assert after[0].provenance.endswith(PROBE_V2)

        # ---- 差分レポート ----
        report = summarise_regrounding(
            plan,
            diffs=diffs,
            reassessed=len(sample_indices),
            started_at=reground_at,
            finished_at=reground_at + timedelta(seconds=3),
            closed_assertions=len(old_ids),
        )
        markdown = report.markdown()
        assert f"`tx_to` を閉じた {len(old_ids):,} 件" in markdown
        assert "削除は 0 件" in markdown
        assert report.needs_review or not report.entries

    def test_the_plan_requires_a_reason(self, world: SynthWorld) -> None:
        """理由が無いと差分が「改善」か「不具合」かを判断できない。"""
        with pytest.raises(RegroundingError, match="must state why it runs"):
            plan_regrounding(
                trigger=RegroundingTrigger.PROBE_VERSION,
                reason="",
                entities=[PUMP],
                fluents=[vocab.ent.Running],
                interval=Interval(world.timestamps[0], world.timestamps[-1]),
                from_version=PROBE_V1,
                to_version=PROBE_V2,
            )


class TestG7VersionLifecycle:
    """設計書 §12.4: 4 つの版を互換性行列で管理する。"""

    def test_an_unverified_combination_is_refused(self) -> None:
        matrix = CompatibilityMatrix()
        matrix.record(ontology=VERSION, world_model=WM_VERSION, probe="1.0.0", mapping="1.0.0")
        with pytest.raises(RegroundingError, match="has not been verified"):
            matrix.require_verified(
                ontology=VERSION, world_model=WM_VERSION, probe="2.0.0", mapping="1.0.0"
            )

    def test_the_promoted_combination_is_recorded(self) -> None:
        matrix = CompatibilityMatrix()
        matrix.record(ontology=VERSION, world_model=WM_VERSION, probe="2.0.0", mapping="1.0.0")
        matrix.require_verified(
            ontology=VERSION, world_model=WM_VERSION, probe="2.0.0", mapping="1.0.0"
        )

    def test_impact_analysis_lists_what_to_reverify(self) -> None:
        matrix = CompatibilityMatrix()
        for probe in ("1.0.0", "2.0.0"):
            matrix.record(ontology=VERSION, world_model=WM_VERSION, probe=probe, mapping="1.0.0")
        assert len(matrix.affected_by(world_model=WM_VERSION)) == 2


class TestG7Observability:
    """設計書 §12.3 の指標が実測値から出せること。"""

    def test_the_probe_metrics_become_samples(self, two_probe_versions: dict[str, Any]) -> None:
        after = two_probe_versions["metrics_v2"]
        samples = [
            MetricSample(
                name=metric("probe_auroc"),
                value=after.auroc,
                at=now(),
                labels={"probe": PROBE_V2, "wm_version": WM_VERSION},
            ),
            MetricSample(
                name=metric("probe_ece"),
                value=after.ece,
                at=now(),
                labels={"probe": PROBE_V2, "wm_version": WM_VERSION},
            ),
        ]
        text = prometheus_exposition(samples)
        assert "sbp_grounding_probe_auroc_ratio" in text
        assert "sbp_grounding_probe_ece_ratio" in text
        assert f'probe="{PROBE_V2}"' in text

    def test_the_regrounding_duration_is_a_metric(self) -> None:
        sample = MetricSample(
            name=metric("regrounding_duration"), value=3.0, at=now(), labels={"trigger": "probe"}
        )
        assert sample.as_row()["domain"] == "operations"
        assert "sbp_operations_regrounding_duration_seconds" in prometheus_exposition([sample])


class TestG7Runbooks:
    """ゲート G7 の条件 3: runbook に従って第三者が実行できること。

    「実行できる」ことはテストでは直接確かめられない。確かめられるのは**手順が揃っていて、
    コマンドが現在のリポジトリと食い違っていないこと**である。
    """

    def test_the_regrounding_runbook_exists_and_covers_the_steps(self) -> None:
        body = (REPO_ROOT / "docs/runbooks/regrounding.md").read_text(encoding="utf-8")
        for heading in (
            "前提の確認",
            "影響分析",
            "計画とコスト見積り",
            "実行",
            "差分レポートの確認",
        ):
            assert heading in body, f"runbook に「{heading}」の節が無い"
        assert "as-of" in body
        assert "失敗時" in body

    def test_every_required_runbook_is_present(self) -> None:
        """M7-05: 再接地 / 版昇格 / 障害対応 / バックアップ・リストア。"""
        for name in (
            "regrounding.md",
            "version-promotion.md",
            "incident-response.md",
            "backup-restore.md",
            "local-setup.md",
        ):
            assert (REPO_ROOT / "docs/runbooks" / name).is_file(), f"{name} が無い"

    def test_the_runbooks_reference_real_apis(self) -> None:
        """手順に書いてある関数が実在すること（古い手順を残さない）。"""
        body = (REPO_ROOT / "docs/runbooks/regrounding.md").read_text(encoding="utf-8")
        for symbol in ("plan_regrounding", "summarise_regrounding", "CompatibilityMatrix"):
            assert symbol in body

        for symbol in ("plan_regrounding", "summarise_regrounding", "CompatibilityMatrix"):
            assert hasattr(knowledge, symbol), f"{symbol} が sbp.knowledge に無い"

    def test_the_incident_runbook_forbids_kill_nine(self) -> None:
        """``kill -9`` は処理中のイベントを捨て、オフセットだけが進む。"""
        body = (REPO_ROOT / "docs/runbooks/incident-response.md").read_text(encoding="utf-8")
        assert "kill -9" in body
        assert "使わない" in body

    def test_the_backup_runbook_states_what_cannot_be_recovered(self) -> None:
        """知識グラフだけが取り返せない——優先順位がそこから決まる。"""
        body = (REPO_ROOT / "docs/runbooks/backup-restore.md").read_text(encoding="utf-8")
        assert "取り返せない" in body
        assert "as-of" in body


def _clamp(probability: float) -> float:
    """確信度を ``(0, 1)`` に収める（``BooleanBelief`` は両端を許さない）。"""
    return float(min(0.99, max(0.01, probability)))
