"""契約テスト: 写像の不変条件（設計書 §7.2、ADR-0006）。

検査する 3 つの保存性:

- **キーの保存** — 業務側の主キーが ``external_id`` に 1 対 1 で写る。
- **必須制約の保存** — 必須列が欠けた行は写像が失敗する（不完全な ``Record`` を作らない）。
- **多重度の保存** — ``one_to_one`` 宣言の写像が 1 行から 2 件以上を作らない。

ファイル名は integration ルールの ``tests/contract/test_mapping_<name>.py`` に従う。
全写像を対象にするため、個別名ではなくレジストリを回す。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from sbp.core import vocab
from sbp.core.errors import MappingError
from sbp.integration.connectors import RawRecord
from sbp.integration.mappings import Mapping, MappingRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
MAPPINGS_DIR = REPO_ROOT / "mappings"
VERSION = "0.2.0"
T0 = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def registry() -> MappingRegistry:
    """``mappings/`` の全写像。"""
    return MappingRegistry.from_directory(MAPPINGS_DIR)


def _sample_row(mapping: Mapping, **overrides: Any) -> RawRecord:
    """写像の必須列をすべて埋めた最小の行を作る。"""
    fields: dict[str, Any] = {}
    for column in mapping.required:
        if column in (mapping.time.recorded_at, mapping.time.occurred_at):
            fields[column] = T0.isoformat().replace("+00:00", "Z")
        else:
            fields[column] = f"val-{column}"
    if mapping.time.occurred_at:
        fields.setdefault(mapping.time.occurred_at, T0.isoformat().replace("+00:00", "Z"))
    fields.update(overrides)
    external = mapping.external_id_for(fields)
    return RawRecord(
        source=mapping.source,
        table=mapping.table,
        external_id=external,
        fields=fields,
        fetched_at=T0,
    )


class TestAllMappingsLoad:
    def test_the_expected_mappings_are_declared(self, registry: MappingRegistry) -> None:
        """設計書 §14 の 4 つの業務情報源と、設備台帳（ADR-0016）に対応する写像がある。"""
        assert registry.names() == (
            "asset_register",
            "maintenance_plan",
            "operation_log",
            "spare_part_stock",
            "work_order",
        )

    def test_every_mapping_applies_to_its_sample_row(self, registry: MappingRegistry) -> None:
        """宣言どおりの行は写像を通る。"""
        for name in registry.names():
            mapping = registry.get(name)
            result = mapping.apply(_sample_row(mapping), ontology_version=VERSION)
            assert result.record.record_type == mapping.record_type
            assert result.record.source == mapping.source
            assert result.mapping_version == mapping.version

    def test_mapping_name_matches_file_name(self, registry: MappingRegistry) -> None:
        for name in registry.names():
            assert registry.get(name).mapping == name


class TestKeyPreservation:
    """キーの保存: 主キーが異なる行は ``external_id`` も異なる。"""

    def test_distinct_keys_give_distinct_external_ids(self, registry: MappingRegistry) -> None:
        for name in registry.names():
            mapping = registry.get(name)
            first_key = mapping.key.columns[0]
            a = mapping.apply(_sample_row(mapping, **{first_key: "K-1"}), ontology_version=VERSION)
            b = mapping.apply(_sample_row(mapping, **{first_key: "K-2"}), ontology_version=VERSION)
            assert a.record.external_id != b.record.external_id, (
                f"{name}: different keys collapsed into the same external_id"
            )

    def test_same_key_gives_same_external_id(self, registry: MappingRegistry) -> None:
        """同じ主キーなら、他の列が変わっても ``external_id`` は同じ（冪等な取り込み）。"""
        mapping = registry.get("work_order")
        a = mapping.apply(
            _sample_row(mapping, wo_number="WO-1", status="Open"), ontology_version=VERSION
        )
        b = mapping.apply(
            _sample_row(mapping, wo_number="WO-1", status="Completed"), ontology_version=VERSION
        )
        assert a.record.external_id == b.record.external_id

    def test_composite_key_uses_all_columns(self, registry: MappingRegistry) -> None:
        """複合キーはすべての列が ``external_id`` に反映される。"""
        mapping = registry.get("spare_part_stock")
        assert len(mapping.key.columns) == 2
        base = mapping.apply(
            _sample_row(mapping, sku="SKU-77", location="WH-A"), ontology_version=VERSION
        )
        other_location = mapping.apply(
            _sample_row(mapping, sku="SKU-77", location="WH-B"), ontology_version=VERSION
        )
        assert base.record.external_id != other_location.record.external_id

    def test_declaration_rejects_non_key_columns_in_template(self) -> None:
        """宣言の段階でキーの保存を壊す設定を弾く。"""
        with pytest.raises(ValueError, match="non-key columns"):
            Mapping.model_validate(
                {
                    "mapping": "bad",
                    "version": "1.0.0",
                    "source": "ent:MesSource",
                    "table": "t",
                    "record_type": "ent:OperationLog",
                    "key": {"columns": ["id"], "external_id": "{id}-{status}"},
                    "required": ["id", "recorded_at"],
                    "time": {"recorded_at": "recorded_at"},
                }
            )


class TestRequiredConstraintPreservation:
    """必須制約の保存: 必須列が欠けたら写像が失敗する。"""

    def test_missing_required_column_fails(self, registry: MappingRegistry) -> None:
        for name in registry.names():
            mapping = registry.get(name)
            for column in mapping.required:
                row = _sample_row(mapping)
                broken_fields = dict(row.fields)
                broken_fields[column] = None
                broken = RawRecord(
                    source=row.source,
                    table=row.table,
                    external_id=row.external_id,
                    fields=broken_fields,
                    fetched_at=row.fetched_at,
                )
                with pytest.raises(MappingError, match="required columns"):
                    mapping.apply(broken, ontology_version=VERSION)

    def test_key_columns_must_be_required(self) -> None:
        """宣言の段階で、キー列が必須でない設定を弾く。"""
        with pytest.raises(ValueError, match="must also be listed in `required`"):
            Mapping.model_validate(
                {
                    "mapping": "bad",
                    "version": "1.0.0",
                    "source": "ent:MesSource",
                    "table": "t",
                    "record_type": "ent:OperationLog",
                    "key": {"columns": ["id"], "external_id": "{id}"},
                    "required": ["recorded_at"],  # id が無い
                    "time": {"recorded_at": "recorded_at"},
                }
            )

    def test_optional_columns_may_be_absent(self, registry: MappingRegistry) -> None:
        """必須でない列は欠けていても通る（列の追加・削除に耐える）。"""
        mapping = registry.get("work_order")
        row = _sample_row(mapping)
        fields = {k: v for k, v in row.fields.items() if k in mapping.required}
        trimmed = RawRecord(
            source=row.source,
            table=row.table,
            external_id=row.external_id,
            fields=fields,
            fetched_at=row.fetched_at,
        )
        result = mapping.apply(trimmed, ontology_version=VERSION)
        assert result.record.external_id == row.external_id


class TestCardinalityPreservation:
    """多重度の保存: 1 行から 1 件。"""

    def test_all_mappings_are_one_to_one(self, registry: MappingRegistry) -> None:
        for name in registry.names():
            assert registry.get(name).cardinality == "one_to_one"

    def test_one_row_yields_exactly_one_record(self, registry: MappingRegistry) -> None:
        for name in registry.names():
            mapping = registry.get(name)
            result = mapping.apply(_sample_row(mapping), ontology_version=VERSION)
            # MappingResult は 1 件の Record を持つ型であり、複数を返す経路が無い
            assert result.record is not None


class TestTimeHandling:
    def test_recorded_and_occurred_are_distinguished(self, registry: MappingRegistry) -> None:
        """記録時刻と発生時刻を分けて持つ（時間不一致の材料）。"""
        mapping = registry.get("work_order")
        row = _sample_row(
            mapping,
            recorded_at="2026-09-13T11:00:00Z",
            completed_at="2026-09-12T17:00:00Z",
        )
        record = mapping.apply(row, ontology_version=VERSION).record
        assert record.recorded_at.isoformat() == "2026-09-13T11:00:00+00:00"
        assert record.occurred_at is not None
        lag = record.reporting_lag
        assert lag is not None
        assert lag.total_seconds() == pytest.approx(18 * 3600), "18 時間の遅延記帳"

    def test_unparseable_time_fails(self, registry: MappingRegistry) -> None:
        mapping = registry.get("operation_log")
        row = _sample_row(mapping, recorded_at="not-a-date")
        with pytest.raises(MappingError, match="cannot parse"):
            mapping.apply(row, ontology_version=VERSION)

    def test_wrong_table_fails(self, registry: MappingRegistry) -> None:
        mapping = registry.get("operation_log")
        row = _sample_row(mapping)
        wrong = RawRecord(
            source=row.source,
            table="work_order",
            external_id=row.external_id,
            fields=row.fields,
            fetched_at=row.fetched_at,
        )
        with pytest.raises(MappingError, match="is for table"):
            mapping.apply(wrong, ontology_version=VERSION)


class TestVocabularyReferences:
    def test_record_types_exist_in_the_ontology(self, registry: MappingRegistry) -> None:
        """写像が参照する記録型がオントロジーに存在する（不変条件 5 の趣旨）。"""
        for name in registry.names():
            mapping = registry.get(name)
            assert mapping.record_type in vocab.ALL_IRIS, (
                f"{name}: record_type {mapping.record_type} is not in the ontology"
            )

    def test_sources_exist_in_the_ontology(self, registry: MappingRegistry) -> None:
        for name in registry.names():
            assert registry.get(name).source in vocab.ALL_IRIS
