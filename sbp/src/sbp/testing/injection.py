"""既知の不一致を注入する合成ハーネス（ゲート G3、docs/TASK.md M3-09）。

照合エンジンの精度は「どれだけ起票したか」では測れない。**正解が分かっている食い違いを
作り込んで**、検出できたか（再現率）と、作り込んでいないものを起票しなかったか（精度）を
測る必要がある。このモジュールはその正解を持つ——**真値なので不変条件 10 によりここ以外から
読めない**（``sbp.testing`` の外から import すると lint が落ちる）。

注入する 3 種は現場で実際に起きるものに対応させている:

- ``state_mismatch``（類型 2）: 停止しているのに MES が稼働と記録している。テンプレ入力が
  残る、シフト単位で丸める、といった運用で日常的に起きる。設計書 §14 の D2 そのもの。
- ``late_posting``（類型 3）: 作業実績の計上が許容プロファイルを大きく超えて遅れる。
- ``ghost_inventory``（類型 1）: ERP の在庫に残っているが物理的には無い。

**クリーンな記録も同時に作る**のが要点である。注入したものだけを入力にすると精度は常に 1.0
になり、「何でも起票するエンジン」を検出できない。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from sbp.core import vocab
from sbp.core.ids import site_iri
from sbp.core.records import Record
from sbp.core.time import Interval
from sbp.testing.synth import Fluent

if TYPE_CHECKING:
    from datetime import datetime

    from sbp.testing.synth import SynthWorld

__all__ = ["InjectedDiscrepancy", "InjectionKind", "InjectionPlan", "inject_discrepancies"]

_SITE = "siteA"
_PUMP_LOCAL = "P-101"
_CLEAN_PUMP_LOCAL = "P-102"
_PART_LOCAL = "SP-77"
_CLEAN_PART_LOCAL = "SP-78"


class InjectionKind(StrEnum):
    """注入する不一致の種類（五類型のうち合成で作れるもの）。"""

    STATE_MISMATCH = "state_mismatch"
    """類型 2: 記録と物理観測が別の状態を主張する。"""
    LATE_POSTING = "late_posting"
    """類型 3: 計上が許容プロファイルを超えて遅れる。"""
    GHOST_INVENTORY = "ghost_inventory"
    """類型 1: 記録にあるが物理に無い。"""

    @property
    def expected_kind(self) -> int:
        """この注入が発火させるべき不一致類型の番号。"""
        return {"ghost_inventory": 1, "state_mismatch": 2, "late_posting": 3}[self.value]


@dataclass(frozen=True, slots=True)
class InjectedDiscrepancy:
    """注入した 1 件の不一致（正解）。

    Attributes:
        kind: 注入の種類。
        entity: 対象実体の IRI。
        interval: 不一致が成立しているべき区間。
        record_iri: 原因になった記録の IRI（証拠の照合に使う）。
        note: 何を作り込んだか（テストの失敗メッセージに出す）。
    """

    kind: InjectionKind
    entity: str
    interval: Interval
    record_iri: str
    note: str

    def matches(self, *, entity: str, kind: int, interval: Interval) -> bool:
        """検出された不一致がこの注入に当たるか。

        同一実体・同一類型・区間が重なれば当たりとする。区間の端が多少ずれるのは許す
        ——プローブの応答遅れと持続条件 τ の分だけ必ずずれるためで、そこを厳密に合わせる
        要求にすると「検出できたのに外れ」になる。

        Args:
            entity: 検出された不一致の対象実体。
            kind: 検出された類型の番号。
            interval: 検出された区間。
        """
        return (
            entity == self.entity
            and kind == self.kind.expected_kind
            and interval.overlaps(self.interval)
        )


@dataclass(frozen=True, slots=True)
class InjectionPlan:
    """注入の結果（記録と正解の組）。

    Attributes:
        records: 業務記録（注入されたもの + クリーンなもの）。``IRI → 記録``。
        injected: 注入した不一致の正解。
        clean_entities: 不一致を作り込んでいない実体の IRI（精度の分母に効く）。
        pump: 不一致を作り込んだポンプの IRI。
        part: 不一致を作り込んだ予備部品の IRI。
    """

    records: dict[str, Record]
    injected: tuple[InjectedDiscrepancy, ...]
    clean_entities: tuple[str, ...]
    pump: str
    part: str

    def expected_count(self, kind: InjectionKind) -> int:
        """ある種類の注入件数。"""
        return sum(1 for item in self.injected if item.kind is kind)


def _record(
    *,
    record_type: str,
    source: str,
    external_id: str,
    subject: str,
    occurred_at: datetime,
    recorded_at: datetime,
    payload: dict[str, object],
    ontology_version: str,
) -> tuple[str, Record]:
    """記録と、その IRI を作る。"""
    record = Record(
        record_type=record_type,
        source=source,
        external_id=external_id,
        is_about=subject,
        recorded_at=recorded_at,
        occurred_at=occurred_at,
        payload=payload,
        ontology_version=ontology_version,
    )
    return site_iri(_SITE, "record", external_id), record


def inject_discrepancies(
    world: SynthWorld,
    *,
    ontology_version: str | None = None,
) -> InjectionPlan:
    """合成世界に既知の不一致を注入する。

    真値（``world.oracle``）を見て「停止している区間」を選び、そこに稼働と主張する記録を
    置く。真値を見るのはこのモジュールが ``sbp.testing`` にあるから許される（不変条件 10）。

    Args:
        world: 合成世界。
        ontology_version: 記録に入れるオントロジー版（既定は現行版）。

    Returns:
        記録と正解の組。

    Raises:
        ValueError: 合成世界に停止区間が無い（``stop_at=None`` で作られた）場合。
    """
    version = ontology_version or vocab.ONTOLOGY_VERSION
    pump = site_iri(_SITE, "asset", _PUMP_LOCAL)
    clean_pump = site_iri(_SITE, "asset", _CLEAN_PUMP_LOCAL)
    part = site_iri(_SITE, "part", _PART_LOCAL)
    clean_part = site_iri(_SITE, "part", _CLEAN_PART_LOCAL)

    running_intervals = world.true_intervals(Fluent.RUNNING)
    if not running_intervals:
        raise ValueError("the synthetic world never runs; nothing to contradict")
    last_running = running_intervals[-1]
    stopped_from = last_running.upper_bound
    final_instant = world.timestamps[-1]
    if stopped_from >= final_instant:
        raise ValueError(
            "the synthetic world has no stopped interval at the end; build it with stop_at set"
        )
    stopped = Interval(stopped_from, final_instant)

    records: dict[str, Record] = {}
    injected: list[InjectedDiscrepancy] = []

    # ---- 注入 1: 状態不一致（設計書 §14 の D2）----
    # 停止しているのに MES が「稼働」と記録している。occurred_at は停止直後に置く。
    iri, record = _record(
        record_type=vocab.ent.OperationLog,
        source=vocab.ent.MesSource,
        external_id="OL-INJ-1",
        subject=pump,
        occurred_at=stopped.start,
        recorded_at=stopped.start + timedelta(minutes=5),
        payload={"running": 1, "shift": "day"},
        ontology_version=version,
    )
    records[iri] = record
    injected.append(
        InjectedDiscrepancy(
            kind=InjectionKind.STATE_MISMATCH,
            entity=pump,
            interval=stopped,
            record_iri=iri,
            note="MES は停止後も稼働と記録している（テンプレ入力の残り）",
        )
    )

    # ---- 注入 2: 時間不一致 ----
    # MES の作業完了が許容プロファイル（±5 分が 2σ）を大きく超えて 6 時間遅れて計上される。
    # 要点: ずれているのは「いつ起きたと記録しているか」である。計上が遅れただけで
    # occurred_at が正しい記録は時間不一致ではない（それは信頼性モデルの time_offset が
    # 織り込む正常な遅れである）。類型 3 は**記録が主張する発生時刻**が許容を超えてずれる場合。
    # 20 時間ずらす。MES の既知の系統誤差「シフト境界での丸め」（規模 8 時間）で説明できる
    # 範囲を超える値にしている——8 時間以内にすると、抑制規則が正しく働いて起票されない
    # （それは誤検出ではなく設計通りの抑制であり、注入としては不適切な題材になる）。
    misreported_at = stopped.start + timedelta(hours=20)
    iri, record = _record(
        record_type=vocab.ent.OperationLog,
        source=vocab.ent.MesSource,
        external_id="OL-INJ-2",
        subject=pump,
        occurred_at=misreported_at,
        recorded_at=misreported_at,
        payload={"running": 0, "shift": "day"},
        ontology_version=version,
    )
    records[iri] = record
    injected.append(
        InjectedDiscrepancy(
            kind=InjectionKind.LATE_POSTING,
            entity=pump,
            interval=Interval(stopped.start, misreported_at),
            record_iri=iri,
            note="MES が停止時刻を 20 時間後として記録している（シフト丸めでは説明できない）",
        )
    )

    # ---- 注入 3: 存在不一致（幽霊在庫）----
    # ERP は予備品が在ると記録しているが、物理観測では見つからない。
    iri, record = _record(
        record_type=vocab.ent.SparePart,
        source=vocab.ent.ErpSource,
        external_id="SP-INJ-1",
        subject=part,
        occurred_at=world.timestamps[0],
        recorded_at=world.timestamps[0] + timedelta(minutes=30),
        payload={"quantity": 4, "present": 1},
        ontology_version=version,
    )
    records[iri] = record
    injected.append(
        InjectedDiscrepancy(
            kind=InjectionKind.GHOST_INVENTORY,
            entity=part,
            interval=Interval(world.timestamps[0], final_instant),
            record_iri=iri,
            note="ERP の在庫に残っているが棚に無い（幽霊在庫）",
        )
    )

    # ---- クリーンな記録（精度の分母）----
    # 同じ時間帯に、物理と一致している記録を置く。これらで起票されたら誤検出である。
    clean_iri, clean_record = _record(
        record_type=vocab.ent.OperationLog,
        source=vocab.ent.MesSource,
        external_id="OL-CLEAN-1",
        subject=clean_pump,
        occurred_at=stopped.start,
        recorded_at=stopped.start + timedelta(minutes=2),
        payload={"running": 0, "shift": "day"},
        ontology_version=version,
    )
    records[clean_iri] = clean_record

    clean_part_iri, clean_part_record = _record(
        record_type=vocab.ent.SparePart,
        source=vocab.ent.ErpSource,
        external_id="SP-CLEAN-1",
        subject=clean_part,
        occurred_at=world.timestamps[0],
        recorded_at=world.timestamps[0] + timedelta(minutes=10),
        payload={"quantity": 2, "present": 1},
        ontology_version=version,
    )
    records[clean_part_iri] = clean_part_record

    return InjectionPlan(
        records=records,
        injected=tuple(injected),
        clean_entities=(clean_pump, clean_part),
        pump=pump,
        part=part,
    )
