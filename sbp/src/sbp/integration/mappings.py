"""写像 DSL（ADR-0006、設計書 §7.2）。

業務システムのスキーマ（圏 S）からオントロジー（圏 O）への写像 `F: S→O` を YAML で宣言し、
ここで解釈する。設計書 §7.2 が要求する 3 つの保存性を**宣言の一部**として持ち、契約テストで
機械検査できる形にしている。

- **キーの保存**: 業務側の主キーが ``external_id`` に 1 対 1 で写る。
- **必須制約の保存**: 必須列が欠けた行は写像を失敗させる（不完全な ``Record`` を黙って作らない）。
- **多重度の保存**: ``one_to_one`` 宣言の写像が 1 行から 2 件以上を作らない。

写像の版（``version``）を上げるのは業務スキーマの意味が変わったときである。列の追加だけなら
写像は変えなくてよい（ドリフト検知が知らせるが、写像は壊れない）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sbp.core.errors import MappingError
from sbp.core.logging import get_logger
from sbp.core.records import Record
from sbp.core.time import ensure_utc

if TYPE_CHECKING:
    from sbp.integration.connectors import RawRecord

__all__ = ["Claim", "Mapping", "MappingRegistry", "MappingResult", "SubjectReference"]

log = get_logger(__name__)

_PREFIXES: Final[dict[str, str]] = {
    "sbp": "https://sbp.example/core#",
    "ent": "https://sbp.example/ent#",
}
_TEMPLATE_FIELD: Final = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def expand_curie(value: str) -> str:
    """CURIE（``ent:WorkOrder``）を完全 IRI に展開する。

    写像宣言では可読性のため CURIE を書き、コードに渡る時点で IRI にする。IRI を
    直書きしないという不変条件 5 は、**写像宣言がオントロジーの語彙を参照している**ことで
    満たされる（宣言の語彙は ``make ontology-check`` が検査対象にできる）。

    Args:
        value: CURIE または完全 IRI。

    Raises:
        MappingError: 未知のプレフィクスを使った場合。
    """
    if value.startswith(("http://", "https://", "urn:")):
        return value
    prefix, _, local = value.partition(":")
    if not local or prefix not in _PREFIXES:
        raise MappingError(f"unknown prefix in {value!r}; known prefixes: {sorted(_PREFIXES)}")
    return f"{_PREFIXES[prefix]}{local}"


class SubjectReference(BaseModel):
    """``is_about`` の対象を指す業務側の参照。

    解決（参照文字列 → 実体 IRI）はこの型の責務ではなく
    :mod:`sbp.integration.resolver` が行う。写像は「どの列が何の参照か」だけを宣言する。

    Attributes:
        column: 参照が入っている列名。
        kind: 参照の種別（``asset`` / ``material`` / ``location``）。解決器が使う。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    column: str
    kind: Literal["asset", "material", "location"]


class _KeySpec(BaseModel):
    """キーの保存の宣言。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    columns: tuple[str, ...] = Field(min_length=1)
    external_id: str
    """``{column}`` のテンプレート。業務側の主キーから ``external_id`` を組む。"""

    @model_validator(mode="after")
    def _template_uses_only_key_columns(self) -> Self:
        """テンプレートが参照する列が ``columns`` に含まれることを検証する。

        キー列以外を ``external_id`` に混ぜるとキーの保存が崩れる（主キーが同じでも
        ``external_id`` が変わりうる）。
        """
        used = set(_TEMPLATE_FIELD.findall(self.external_id))
        unknown = used - set(self.columns)
        if unknown:
            raise ValueError(
                f"external_id template references non-key columns {sorted(unknown)}; "
                "this breaks key preservation (design §7.2)"
            )
        if not used:
            raise ValueError("external_id template must reference at least one key column")
        return self


class _TimeSpec(BaseModel):
    """時刻列の宣言。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recorded_at: str
    """記録時刻（計上日時）が入っている列。"""
    occurred_at: str | None = None
    """「実際に起きた」時刻が入っている列。無ければ ``None``。"""


class _ClaimSpec(BaseModel):
    """記録が主張するフルーエントの宣言（ADR-0016 の問い 2）。

    記録には「存在する」と「何かを主張する」の 2 つの事実がある。前者は写像だけで決まるが、
    後者は「どの列がどのフルーエントの真偽を言っているか」を知らないと取り出せない
    （``EventEnvelope`` は記録の中身を運ばないので、消費側では復元できない。所見 115）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fluent: str
    """主張の対象のフルーエント型（CURIE で宣言し IRI に展開される）。"""
    field: str
    """真偽が入っている ``Record.payload`` のキー（``fields`` の値側）。"""

    @model_validator(mode="after")
    def _fluent_exists(self) -> Self:
        """フルーエントを展開し、オントロジーに実在することを検証する。

        綴り違いを通すと、主張が永久にどのフルーエントにも当たらず、照合の記録列が静かに空になる。
        """
        from sbp.core import vocab  # noqa: PLC0415 — 生成物の読み込みを宣言の検証時だけにする

        iri = expand_curie(self.fluent)
        if iri not in vocab.FLUENT_TYPES:
            raise ValueError(f"claim fluent {self.fluent!r} is not a FluentType in the ontology")
        object.__setattr__(self, "fluent", iri)
        return self


@dataclass(frozen=True, slots=True)
class Claim:
    """記録 1 件が 1 つのフルーエントについて主張していること。

    Attributes:
        fluent: フルーエント型 IRI。
        says: 記録が主張する真偽（信頼性で割り引く前の生の主張）。
    """

    fluent: str
    says: bool


_TRUE_WORDS: Final = frozenset({"true", "1", "yes"})
_FALSE_WORDS: Final = frozenset({"false", "0", "no"})


def _read_bool(value: object) -> bool | None:
    """業務側の真偽の表現を読む。読めなければ ``None``。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return {1: True, 0: False}.get(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    return None


class Mapping(BaseModel):
    """写像 1 本の宣言（``mappings/<name>.yaml``）。

    Attributes:
        mapping: 写像の名前（ファイル名と一致させる）。
        version: 写像の版。業務スキーマの意味が変わったら上げる。
        source: 情報源 IRI（CURIE で宣言し IRI に展開される）。
        table: 業務システム側のテーブル名。
        record_type: 生成する ``Record`` の型 IRI。
        key: キーの保存の宣言。
        subject_ref: ``is_about`` の対象を指す参照。
        required: 必須列。欠けたら写像を失敗させる。
        cardinality: 多重度。``one_to_one`` は 1 行から 1 件。
        time: 時刻列の対応。
        fields: ``Record.payload`` に移す列（業務側の列名 → payload のキー）。
        claims: 記録が主張するフルーエント（ADR-0016）。無ければ「存在する」イベントだけを出す。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mapping: str
    version: str
    source: str
    table: str
    record_type: str
    key: _KeySpec
    subject_ref: SubjectReference | None = None
    required: tuple[str, ...] = ()
    cardinality: Literal["one_to_one"] = "one_to_one"
    time: _TimeSpec
    fields: dict[str, str] = Field(default_factory=dict)
    claims: tuple[_ClaimSpec, ...] = ()

    @model_validator(mode="after")
    def _claims_read_mapped_fields(self) -> Self:
        """主張が読むキーは ``fields`` で payload に移されていなければならない。"""
        mapped = set(self.fields.values())
        for claim in self.claims:
            if claim.field not in mapped:
                raise ValueError(
                    f"claim on {claim.fluent} reads {claim.field!r}, which is not in `fields`"
                )
        return self

    def claims_of(self, record: Record) -> list[Claim]:
        """記録の主張を取り出す。値が ``None`` の主張は「何も言っていない」として飛ばす。

        Args:
            record: この写像で作った記録。

        Raises:
            MappingError: 真偽として読めない値（推測で真偽に寄せない）。
        """
        found: list[Claim] = []
        for claim in self.claims:
            value = record.payload.get(claim.field)
            if value is None:
                continue
            says = _read_bool(value)
            if says is None:
                raise MappingError(
                    f"mapping {self.mapping!r}: cannot read {claim.field}={value!r} as a boolean "
                    f"claim on {claim.fluent} (record {record.external_id!r})"
                )
            found.append(Claim(fluent=claim.fluent, says=says))
        return found

    @model_validator(mode="after")
    def _expand_iris(self) -> Self:
        """CURIE を IRI に展開する（検証時に 1 度だけ行う）。"""
        object.__setattr__(self, "source", expand_curie(self.source))
        object.__setattr__(self, "record_type", expand_curie(self.record_type))
        return self

    @model_validator(mode="after")
    def _key_columns_are_required(self) -> Self:
        """キー列は必須列に含まれていなければならない。

        キーが欠けた行を通すと ``external_id`` が組めず、キーの保存が成立しない。
        """
        missing = set(self.key.columns) - set(self.required)
        if missing:
            raise ValueError(
                f"key columns {sorted(missing)} must also be listed in `required`; "
                "otherwise a row without a key could pass the mapping (design §7.2)"
            )
        return self

    def column_for(self, payload_key: str) -> str | None:
        """``Record.payload`` のキーから業務側の列名を引く（写像の逆向き）。

        記録訂正の書き戻し（設計書 §7.6）に必要になる。``fields`` は 1 列 1 キーの対応なので
        逆引きできるが、同じキーに複数列が対応する写像を将来許す場合はここで曖昧になる——
        そのときは逆向きを宣言で持たせる（ADR-0006 の未決事項 Δ_F）。

        Args:
            payload_key: ``Record.payload`` のキー。

        Returns:
            対応する業務側の列名。無ければ ``None``。
        """
        for column, key in self.fields.items():
            if key == payload_key:
                return column
        return None

    def external_id_for(self, fields: dict[str, Any]) -> str:
        """業務側の行から ``external_id`` を組む。

        Args:
            fields: 業務側の列名 → 値。

        Raises:
            MappingError: キー列が欠けている場合。
        """
        try:
            return _TEMPLATE_FIELD.sub(lambda m: str(fields[m.group(1)]), self.key.external_id)
        except KeyError as exc:
            raise MappingError(
                f"mapping {self.mapping!r}: key column {exc.args[0]!r} is missing from the row"
            ) from exc

    def apply(self, raw: RawRecord, *, ontology_version: str) -> MappingResult:
        """業務側の 1 行を :class:`~sbp.core.records.Record` に写す。

        Args:
            raw: 取り込んだ生の行。
            ontology_version: 取り込み時のオントロジー版。

        Returns:
            写像の結果（``Record`` と、解決が必要な参照）。

        Raises:
            MappingError: テーブルが一致しない、必須列が欠けている、時刻が解釈できない場合。
        """
        if raw.table != self.table:
            raise MappingError(
                f"mapping {self.mapping!r} is for table {self.table!r}, got {raw.table!r}"
            )
        missing = [c for c in self.required if raw.fields.get(c) is None]
        if missing:
            raise MappingError(
                f"mapping {self.mapping!r}: required columns {missing} are missing or null "
                f"in row {raw.external_id!r} (required-constraint preservation, design §7.2)"
            )

        recorded_at = self._parse_time(raw.fields, self.time.recorded_at, required=True)
        occurred_at = (
            self._parse_time(raw.fields, self.time.occurred_at, required=False)
            if self.time.occurred_at
            else None
        )
        assert recorded_at is not None

        payload = {target: raw.fields.get(column) for column, target in self.fields.items()}
        record = Record(
            record_type=self.record_type,
            source=self.source,
            external_id=self.external_id_for(raw.fields),
            is_about=None,  # 解決は resolver の責務
            recorded_at=recorded_at,
            occurred_at=occurred_at,
            payload=payload,
            ontology_version=ontology_version,
        )
        raw_reference = (
            str(raw.fields[self.subject_ref.column])
            if self.subject_ref is not None and raw.fields.get(self.subject_ref.column) is not None
            else None
        )
        return MappingResult(
            record=record,
            subject_ref=self.subject_ref,
            raw_reference=raw_reference,
            mapping_version=self.version,
        )

    def _parse_time(
        self, fields: dict[str, Any], column: str, *, required: bool
    ) -> datetime | None:
        value = fields.get(column)
        if value is None:
            if required:
                raise MappingError(
                    f"mapping {self.mapping!r}: time column {column!r} is required but null"
                )
            return None
        if isinstance(value, datetime):
            return ensure_utc(value)
        try:
            return ensure_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
        except ValueError as exc:
            raise MappingError(
                f"mapping {self.mapping!r}: cannot parse {column!r}={value!r} as a datetime"
            ) from exc


class MappingResult(BaseModel):
    """写像 1 行の結果。

    Attributes:
        record: 写した記録（``is_about`` は未解決のまま）。
        subject_ref: 解決すべき参照の宣言（無ければ ``None``）。
        raw_reference: 業務側の生の参照文字列（無ければ ``None``）。
        mapping_version: 適用した写像の版。来歴に残す。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record: Record
    subject_ref: SubjectReference | None
    raw_reference: str | None
    mapping_version: str


class MappingRegistry:
    """``mappings/*.yaml`` を読み込んで保持する。"""

    def __init__(self, mappings: dict[str, Mapping]) -> None:
        """Args:
        mappings: 写像名 → 写像。
        """
        self._mappings = mappings

    @classmethod
    def from_directory(cls, directory: Path) -> MappingRegistry:
        """ディレクトリ内の全写像を読み込む。

        Args:
            directory: ``mappings/`` のパス。

        Raises:
            MappingError: YAML が壊れている、写像名がファイル名と一致しない場合。
        """
        mappings: dict[str, Mapping] = {}
        for path in sorted(directory.glob("*.yaml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            try:
                mapping = Mapping.model_validate(payload)
            except Exception as exc:
                raise MappingError(f"invalid mapping {path.name}: {exc}") from exc
            if mapping.mapping != path.stem:
                raise MappingError(
                    f"mapping name {mapping.mapping!r} does not match file name {path.stem!r}"
                )
            mappings[mapping.mapping] = mapping
        if not mappings:
            raise MappingError(f"no mappings found under {directory}")
        log.debug("mappings.loaded", count=len(mappings), names=sorted(mappings))
        return cls(mappings)

    def get(self, name: str) -> Mapping:
        """名前で写像を取り出す。

        Args:
            name: 写像名。

        Raises:
            MappingError: 未知の写像名。
        """
        try:
            return self._mappings[name]
        except KeyError as exc:
            raise MappingError(
                f"unknown mapping {name!r}; known: {sorted(self._mappings)}"
            ) from exc

    def for_table(self, table: str) -> Mapping:
        """テーブル名で写像を引く。

        Args:
            table: 業務システム側のテーブル名。

        Raises:
            MappingError: 該当する写像が無い、または複数ある場合。
        """
        found = [m for m in self._mappings.values() if m.table == table]
        if len(found) != 1:
            raise MappingError(
                f"expected exactly one mapping for table {table!r}, found {len(found)}"
            )
        return found[0]

    def for_record_type(self, record_type: str) -> Mapping | None:
        """記録型で写像を引く（書き戻しの宛先テーブルを求めるのに使う）。

        該当が複数あるときは ``None`` を返す。どのテーブルに書くか決められないまま業務
        システムを触らないためで、呼び出し側は訂正提案を作らずに記録に残す。

        Args:
            record_type: 記録型 IRI。

        Returns:
            一意に決まる写像。無い・複数あるなら ``None``。
        """
        found = [m for m in self._mappings.values() if m.record_type == record_type]
        if len(found) != 1:
            log.info(
                "mappings.record_type_not_uniquely_mapped",
                record_type=record_type,
                candidates=len(found),
            )
            return None
        return found[0]

    def names(self) -> tuple[str, ...]:
        """登録済みの写像名。"""
        return tuple(sorted(self._mappings))
