"""業務統合層の単体テスト（設計書 §7.1〜§7.4）。

モックは SQLite なので外部 I/O を持たない（``make test`` はネットワーク不要）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sbp.core import vocab
from sbp.core.errors import IntegrationError
from sbp.core.modality import Modality
from sbp.core.records import Record
from sbp.integration import (
    IngestionPipeline,
    MappingRegistry,
    MasterDataResolver,
    ReliabilityRegistry,
)
from sbp.integration.mock import MockBusinessSystem
from sbp.integration.mock.business_system import build_default_scenario
from sbp.integration.reliability import ConfusionMatrix, ReliabilityModel, TimeOffsetProfile

REPO_ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
VERSION = vocab.ONTOLOGY_VERSION


@pytest.fixture(scope="module")
def mappings() -> MappingRegistry:
    return MappingRegistry.from_directory(REPO_ROOT / "mappings")


@pytest.fixture(scope="module")
def reliability() -> ReliabilityRegistry:
    return ReliabilityRegistry.from_directory(REPO_ROOT / "reliability")


@pytest.fixture
def resolver() -> MasterDataResolver:
    """P-101 と SKU-77 を知っているマスタ。"""
    r = MasterDataResolver(site_id="siteA")
    r.register("asset", "P-101")
    r.register("material", "SKU-77")
    return r


@pytest.fixture
def pipeline(
    mappings: MappingRegistry, resolver: MasterDataResolver, reliability: ReliabilityRegistry
) -> IngestionPipeline:
    return IngestionPipeline(
        site_id="siteA",
        mappings=mappings,
        resolver=resolver,
        reliability=reliability,
        ontology_version=VERSION,
    )


class TestMockBusinessSystem:
    async def test_scenario_produces_rows(self) -> None:
        """台本が業務側の主張を作る。"""
        with MockBusinessSystem(table="operation_log") as mock:
            applied = mock.apply(build_default_scenario(T0))
            assert applied == 1
            rows = await mock.pull()
            assert len(rows) == 1
            assert rows[0].fields["asset_tag"] == "P-101"
            assert rows[0].fields["running"] == 1, "MES は稼働中と主張している"

    async def test_pull_since_returns_only_newer_rows(self) -> None:
        """差分取り込み（CDC 相当）。"""
        with MockBusinessSystem(table="work_order") as mock:
            mock.apply(build_default_scenario(T0))
            assert len(await mock.pull()) == 1
            assert len(await mock.pull(since=T0 + timedelta(days=2))) == 0

    async def test_upsert_models_a_business_side_correction(self) -> None:
        """業務システム側の訂正（記録の書き換え）を再現できる。"""
        with MockBusinessSystem(table="operation_log") as mock:
            mock.apply(build_default_scenario(T0))
            mock.upsert(
                {
                    "log_id": "OL-0912-A",
                    "asset_tag": "P-101",
                    "running": 0,  # 訂正: 実は停止していた
                    "shift": "shift1",
                    "logged_at": T0.isoformat().replace("+00:00", "Z"),
                    "recorded_at": (T0 + timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
                }
            )
            rows = await mock.pull()
            assert len(rows) == 1, "主キーが同じなので行は増えない"
            assert rows[0].fields["running"] == 0

    async def test_fingerprint_changes_when_schema_changes(self) -> None:
        with MockBusinessSystem(table="work_order") as mock:
            before = (await mock.fingerprints())[0]
            assert before.digest() == (await mock.fingerprints())[0].digest(), "指紋は安定"

    def test_unknown_table_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown table"):
            MockBusinessSystem(table="nonexistent")


class TestIngestion:
    async def test_full_path_resolves_subject_and_emits_event(
        self, pipeline: IngestionPipeline
    ) -> None:
        """RawRecord → 写像 → is_about 解決 → イベント まで通る。"""
        with MockBusinessSystem(table="operation_log") as mock:
            mock.apply(build_default_scenario(T0))
            rows = list(await mock.pull())

        result = pipeline.ingest_rows(rows)
        assert result.failed == []
        assert len(result.records) == 1
        record = result.records[0]
        assert record.is_about == "https://sbp.example/site/siteA/asset/P-101"
        assert record.reliability_profile is not None, "信頼性モデルが紐づいている"

        assert len(result.events) == 1
        topic, event = result.events[0]
        assert topic == "siteA.integration.business"
        assert event.subject == record.is_about
        assert event.modality is Modality.OBSERVED, "記録が存在することは観測された事実"
        assert len(event.evidence) == 1
        assert event.evidence[0].kind == "record"

    async def test_unresolved_reference_is_kept_not_dropped(
        self, mappings: MappingRegistry, reliability: ReliabilityRegistry
    ) -> None:
        """integration ルール: 未解決は捨てずに残す。"""
        empty_master = MasterDataResolver(site_id="siteA")  # 何も知らない
        pipeline = IngestionPipeline(
            site_id="siteA",
            mappings=mappings,
            resolver=empty_master,
            reliability=reliability,
            ontology_version=VERSION,
        )
        with MockBusinessSystem(table="operation_log") as mock:
            mock.apply(build_default_scenario(T0))
            rows = list(await mock.pull())

        result = pipeline.ingest_rows(rows)
        assert len(result.records) == 1, "記録は捨てない"
        assert not result.records[0].is_resolved
        assert len(result.unresolved) == 1
        assert result.unresolved[0].raw_reference == "P-101"
        assert result.unresolved[0].reason == "not_found"
        assert len(result.events) == 1, "未解決でもイベントは出す（存在不一致の材料）"
        assert result.events[0][1].subject.startswith("urn:sbp:unresolved:")

    async def test_mapping_failure_does_not_stop_other_rows(
        self, pipeline: IngestionPipeline
    ) -> None:
        """1 行の誤りで取り込み全体を止めない。"""
        with MockBusinessSystem(table="operation_log") as mock:
            mock.apply(build_default_scenario(T0))
            mock.upsert(
                {
                    "log_id": "OL-BROKEN",
                    "asset_tag": "P-101",
                    "running": 1,
                    "shift": "shift1",
                    "logged_at": T0.isoformat().replace("+00:00", "Z"),
                    "recorded_at": "not-a-date",
                }
            )
            rows = list(await mock.pull())

        result = pipeline.ingest_rows(rows)
        assert len(result.records) == 1, "正常な行は取り込まれる"
        assert len(result.failed) == 1
        assert "cannot parse" in result.failed[0][1]

    async def test_drift_detection_reports_schema_change(self, pipeline: IngestionPipeline) -> None:
        """スキーマ変更は例外ではなくイベントとして出る（設計書 §7.2）。"""
        with MockBusinessSystem(table="work_order") as mock:
            assert await pipeline.check_drift(mock) == [], "初回は変更なし"
            assert await pipeline.check_drift(mock) == [], "同じスキーマなら変更なし"

    async def test_unknown_source_has_no_reliability_model(
        self, mappings: MappingRegistry, resolver: MasterDataResolver
    ) -> None:
        """未知の情報源を既定で信頼しない（設計書 §7.3）。"""
        empty = ReliabilityRegistry({})
        pipeline = IngestionPipeline(
            site_id="siteA",
            mappings=mappings,
            resolver=resolver,
            reliability=empty,
            ontology_version=VERSION,
        )
        with MockBusinessSystem(table="operation_log") as mock:
            mock.apply(build_default_scenario(T0))
            rows = list(await mock.pull())
        with pytest.raises(IntegrationError, match="no reliability model"):
            pipeline.ingest_rows(rows)


class TestReliabilityModel:
    def test_time_offset_percentile_matches_design_profile(
        self, reliability: ReliabilityRegistry
    ) -> None:
        """MES の時刻ずれは ±5 分程度（設計書 §3.6）。"""
        model = reliability.get(vocab.ent.MesSource, vocab.ent.OperationLog)
        p95 = model.time_offset.percentile_s(0.95)
        assert 250 <= p95 <= 400, f"MES の 95 パーセンタイルが想定外: {p95} 秒"

    def test_cmms_lag_is_much_larger_than_mes(self, reliability: ReliabilityRegistry) -> None:
        """CMMS の完了記録は MES より大きく遅れる（遅延記帳）。"""
        mes = reliability.get(vocab.ent.MesSource, vocab.ent.OperationLog)
        cmms = reliability.get(vocab.ent.CmmsSource, vocab.ent.WorkOrder)
        assert cmms.time_offset.percentile_s(0.95) > mes.time_offset.percentile_s(0.95) * 10

    def test_implausible_lag_is_detected(self, reliability: ReliabilityRegistry) -> None:
        """許容プロファイルを超えるずれは時間不一致（類型 3）の候補になる。"""
        model = reliability.get(vocab.ent.MesSource, vocab.ent.OperationLog)
        assert not model.time_offset.is_implausible(timedelta(minutes=2))
        assert model.time_offset.is_implausible(timedelta(hours=2))

    def test_log_likelihood_ratio_favours_truth(self, reliability: ReliabilityRegistry) -> None:
        """記録が「真」と言ったとき、尤度比は真を支持する方向に働く。"""
        model = reliability.get(vocab.ent.MesSource, vocab.ent.OperationLog)
        assert model.confusion is not None
        ratio = model.confusion.log_likelihood_ratio(record_says=True)
        assert ratio > 0, "記録が真と言えば真である方が尤もらしい"

    def test_mes_template_entry_weakens_the_evidence(
        self, reliability: ReliabilityRegistry
    ) -> None:
        """MES のテンプレ入力（高い false_positive）は証拠を弱くする。

        これが §14 で「観測が記録を上回る」理由である。CMMS より MES の尤度比が小さい。
        """
        mes = reliability.get(vocab.ent.MesSource, vocab.ent.OperationLog)
        cmms = reliability.get(vocab.ent.CmmsSource, vocab.ent.WorkOrder)
        assert mes.confusion is not None
        assert cmms.confusion is not None
        assert mes.confusion.log_likelihood_ratio(
            record_says=True
        ) < cmms.confusion.log_likelihood_ratio(record_says=True)

    def test_uninformative_confusion_matrix_is_rejected(self) -> None:
        """状態について何も語らない行列は登録できない。"""
        with pytest.raises(ValueError, match="carries no information"):
            ConfusionMatrix(true_positive=0.5, false_positive=0.5)

    def test_numeric_likelihood_peaks_at_the_true_value(
        self, reliability: ReliabilityRegistry
    ) -> None:
        model = reliability.get(vocab.ent.ErpSource, vocab.ent.SparePart)
        exact = model.log_likelihood_numeric(recorded=3.0, state=3.0)
        off = model.log_likelihood_numeric(recorded=3.0, state=0.0)
        assert exact > off, "記録値に近い真値ほど尤もらしい"

    def test_missing_numeric_model_raises(self, reliability: ReliabilityRegistry) -> None:
        model = reliability.get(vocab.ent.MesSource, vocab.ent.OperationLog)
        with pytest.raises(IntegrationError, match="no numeric error model"):
            model.log_likelihood_numeric(recorded=1.0, state=1.0)

    def test_update_from_resolution_moves_the_model(self) -> None:
        """解決済み不一致から継続推定する（設計書 §8.2）。"""
        model = ReliabilityModel(
            source="ent:MesSource",
            record_type="ent:OperationLog",
            time_offset=TimeOffsetProfile(mean_s=0.0, stddev_s=100.0),
            confusion=ConfusionMatrix(true_positive=0.9, false_positive=0.1),
        )
        # 物理が正しかった（記録が誤っていた）→ 誤記率が上がる
        updated = model.update_from_resolution(outcome="physical_correct", observed_lag_s=600.0)
        assert updated.confusion is not None
        assert model.confusion is not None
        assert updated.confusion.false_positive > model.confusion.false_positive
        assert updated.time_offset.mean_s > model.time_offset.mean_s
        assert updated.observations == 1
        assert model.observations == 0, "元のモデルは変わらない（frozen）"

    def test_updates_shrink_as_observations_accumulate(self) -> None:
        """観測が増えるほど 1 件の解決による変化が小さくなる。"""
        model = ReliabilityModel(
            source="ent:MesSource",
            record_type="ent:OperationLog",
            confusion=ConfusionMatrix(true_positive=0.9, false_positive=0.1),
        )
        first = model.update_from_resolution(outcome="record_correct", observed_lag_s=None)
        assert first.confusion is not None
        first_delta = first.confusion.true_positive - 0.9

        many = model
        for _ in range(50):
            many = many.update_from_resolution(outcome="record_correct", observed_lag_s=None)
        later = many.update_from_resolution(outcome="record_correct", observed_lag_s=None)
        assert later.confusion is not None and many.confusion is not None
        later_delta = later.confusion.true_positive - many.confusion.true_positive
        assert later_delta < first_delta


class TestSystematicBias:
    def test_month_end_batch_explains_a_long_lag(self, reliability: ReliabilityRegistry) -> None:
        """月末一括計上は長い遅れを説明できる（抑制の根拠になる）。"""
        model = reliability.get(vocab.ent.CmmsSource, vocab.ent.WorkOrder)
        record = Record(
            record_type=vocab.ent.WorkOrder,
            source=vocab.ent.CmmsSource,
            external_id="WO-1",
            recorded_at=T0 + timedelta(days=20),
            occurred_at=T0,
            ontology_version=VERSION,
        )
        bias = model.explains_as_systematic(record)
        assert bias is not None
        assert bias.kind == "month_end_batch"

    def test_unexplained_lag_returns_none(self, reliability: ReliabilityRegistry) -> None:
        """説明できない遅れは抑制しない（不一致として扱う）。"""
        model = reliability.get(vocab.ent.CmmsSource, vocab.ent.WorkOrder)
        record = Record(
            record_type=vocab.ent.WorkOrder,
            source=vocab.ent.CmmsSource,
            external_id="WO-2",
            recorded_at=T0 + timedelta(days=200),
            occurred_at=T0,
            ontology_version=VERSION,
        )
        assert model.explains_as_systematic(record) is None
