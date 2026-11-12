"""記号条件付けベクトルの生成（設計書 §4.4、不変条件 6）。

> オントロジー実例グラフの k-hop 近傍（型・現在の信念・PlanItem・Norm）を GNN で埋め込み、
> ``context`` として f に入力する。

**マスクが必須引数である理由。** 条件付けベクトルは「いまこの実体について何を信じているか」を
WM に教える経路である。ある記録を監査するための推定で、その記録から派生した信念を条件付けに
入れてしまえば、記録が推定を動かし、動いた推定が記録を支持する——不変条件 6 が禁じている循環
そのものになる。後から差し込む設計にすると、マスクを忘れた呼び出しが必ず残るため、
:func:`build` は ``mask`` をキーワード必須引数として取る（``no_audit()`` を明示的に渡させる）。

**GNN ではなく決定的な特徴化にしている。** 設計書は k-hop 近傍の GNN 埋め込みを挙げるが、
初期実装では「型・信念・計画・規範」を固定長の特徴として決定的に並べる。理由は 3 つある。

1. 条件付けの**効き方を検証できる**。学習した埋め込みでは、条件付けが遷移を変えたときに
   「何が効いたのか」が追えない。説明サービスが潜在側の根拠を出せなくなる。
2. 条件付けの学習には「条件付きで予測が改善した」ことを示すデータが必要で、それは
   設計書 §4.5 の段階 2 の仕事である（段階 1 の自己教師事前学習より後）。
3. 決定的なら **as-of 再構成**ができる。当時の信念から当時の条件付けを再現できなければ、
   「なぜそう予測したのか」を後から説明できない（設計書 §8.8）。

GNN 埋め込みに移る判断は、決定的な特徴化で予測が改善しないことを測ってから行う（ADR 必須）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from sbp.core.errors import WorldModelError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from numpy.typing import NDArray

    from sbp.core.audit import AuditMask
    from sbp.core.records import Norm, PlanItem, Record

__all__ = ["ConditioningSpec", "EntityContext", "build"]

# ``sbp.worldmodel`` からは ``build_context`` という名前で公開する（最上位の名前空間で
# ``build`` だけでは何を作るのか分からないため）。実装名は設計書 §4.4 の表記に合わせている。

log = get_logger(__name__)

_HASH_BUCKETS = 8
"""型 IRI を落とし込むハッシュ特徴の幅。型の数が増えても次元が変わらないようにする。"""


@dataclass(frozen=True, slots=True)
class ConditioningSpec:
    """条件付けベクトルの構成（次元の意味を固定する）。

    次元の意味を宣言で持つのが要点である。無名の特徴ベクトルにすると、学習済みモデルと
    特徴生成コードの版が食い違ったときに**黙って別の意味の数値が入る**。

    Attributes:
        fluents: 信念を載せるフルーエント型 IRI（この順で並ぶ）。
        latent_dim: 出力ベクトルの長さ（WM の潜在次元に合わせる）。
        include_plan: 計画の遵守率と残り時間を載せるか。
        include_norms: 適用中の規範の重みを載せるか。
    """

    fluents: tuple[str, ...]
    latent_dim: int
    include_plan: bool = True
    include_norms: bool = True

    def __post_init__(self) -> None:
        """次元が足りるかを検証する。"""
        if not self.fluents:
            raise WorldModelError("a conditioning spec must name at least one fluent type")
        if self.latent_dim < self.feature_dim:
            raise WorldModelError(
                f"latent_dim {self.latent_dim} is smaller than the {self.feature_dim} features "
                "this spec produces; widen the latent or narrow the spec rather than truncating "
                "(silently dropped features change what the model is conditioned on)"
            )

    @property
    def feature_dim(self) -> int:
        """生成される特徴の数（ゼロ埋め前）。"""
        total = len(self.fluents) + _HASH_BUCKETS
        if self.include_plan:
            total += 2
        if self.include_norms:
            total += 1
        return total

    def describe(self) -> tuple[str, ...]:
        """各次元の意味（説明サービスと回帰検査に使う）。"""
        names = [f"belief:{fluent}" for fluent in self.fluents]
        names.extend(f"type_hash:{index}" for index in range(_HASH_BUCKETS))
        if self.include_plan:
            names.extend(["plan:adherence", "plan:remaining_fraction"])
        if self.include_norms:
            names.append("norm:max_weight")
        names.extend(f"pad:{index}" for index in range(self.latent_dim - self.feature_dim))
        return tuple(names)


@dataclass(frozen=True, slots=True)
class EntityContext:
    """1 実体の条件付けベクトルとその来歴。

    Attributes:
        entity: 対象実体の IRI。
        vector: 条件付けベクトル。
        spec: 生成に使った構成。
        audit_note: 監査隔離の来歴メモ（不変条件 6 の検証に使う）。
        excluded: 隔離で除外した記録の数。
        features: 次元名 → 値（説明サービス用）。
    """

    entity: str
    vector: NDArray[np.float64]
    spec: ConditioningSpec
    audit_note: str
    excluded: int = 0
    features: dict[str, float] = field(default_factory=dict)

    @property
    def is_isolated(self) -> bool:
        """監査隔離が実際に働いたか。"""
        return self.excluded > 0


def _type_hash(entity_type: str) -> NDArray[np.float64]:
    """型 IRI を固定幅のハッシュ特徴にする。

    one-hot にしないのは、型が増えるたびに次元が変わり、学習済みモデルが使えなくなるため。
    ハッシュなら衝突は起きるが次元は不変で、オントロジーの MINOR 追加でモデルを捨てずに済む。
    """
    digest = hashlib.sha256(entity_type.encode("utf-8")).digest()
    buckets = np.zeros(_HASH_BUCKETS, dtype=np.float64)
    for index in range(_HASH_BUCKETS):
        buckets[index] = digest[index] / 255.0
    return buckets


def build(
    entity: str,
    *,
    spec: ConditioningSpec,
    mask: AuditMask,
    entity_type: str = "",
    beliefs: Mapping[str, float] | None = None,
    plan: PlanItem | None = None,
    norms: Sequence[Norm] = (),
    records: Sequence[Record] = (),
    at: datetime | None = None,
) -> EntityContext:
    """実体の記号条件付けベクトルを作る（不変条件 6）。

    ``mask`` は必須。監査対象が無ければ :func:`sbp.core.audit.no_audit` を明示的に渡す。

    信念は呼び出し側（``reasoning``）が渡す。ここで知識グラフを読まないのは、層依存
    （``worldmodel`` は ``knowledge`` を読めるが ``reasoning`` は読めない方向）に加えて、
    **as-of 再構成**のためである。当時の信念を渡せば当時の条件付けが再現できる。

    Args:
        entity: 対象実体の IRI。
        spec: 条件付けの構成。
        mask: 監査隔離マスク。
        entity_type: 実体の型 IRI（ハッシュ特徴に入る）。
        beliefs: フルーエント型 IRI → 確信度。
        plan: 当該時刻に有効な計画項目。
        norms: 適用中の規範。
        records: 条件付けに使おうとしている記録（マスクで除外されたものを数える）。
        at: 計画の残り時間を計算する基準時刻。``None`` なら残り時間は 0。

    Returns:
        条件付けベクトルと来歴。

    Raises:
        WorldModelError: 信念が確率でない場合。
    """
    masked = mask.filter_records(list(records))
    known = dict(beliefs or {})
    for fluent, value in known.items():
        if not 0.0 <= value <= 1.0:
            raise WorldModelError(
                f"belief about {fluent} must be a probability in [0, 1], got {value}"
            )

    values: list[float] = [known.get(fluent, 0.5) for fluent in spec.fluents]
    values.extend(_type_hash(entity_type or entity))

    if spec.include_plan:
        if plan is None:
            values.extend([0.0, 0.0])
        else:
            values.append(plan.adherence_prior)
            values.append(_remaining_fraction(plan, at))

    if spec.include_norms:
        weights = [norm.severity.weight for norm in norms]
        values.append(max(weights) if weights else 0.0)

    vector = np.zeros(spec.latent_dim, dtype=np.float64)
    vector[: len(values)] = values
    names = spec.describe()
    log.debug(
        "worldmodel.context_built",
        entity=entity,
        features=len(values),
        latent_dim=spec.latent_dim,
        excluded_records=masked.exclusion_count,
    )
    return EntityContext(
        entity=entity,
        vector=vector,
        spec=spec,
        audit_note=mask.provenance_note(),
        excluded=masked.exclusion_count,
        features=dict(zip(names, vector.tolist(), strict=True)),
    )


def _remaining_fraction(plan: PlanItem, at: datetime | None) -> float:
    """計画区間の残り割合（0 なら終了、1 なら未着手）。

    割合にするのは、計画の長さがまちまちでも同じ尺度で条件付けできるようにするためである。
    絶対秒数を入れると、長い計画と短い計画で特徴のスケールが桁違いになる。
    """
    if at is None:
        return 0.0
    interval = plan.planned_interval
    total = interval.duration
    if total is None or total.total_seconds() <= 0.0:
        return 0.0
    remaining = (interval.upper_bound - at).total_seconds()
    return float(min(1.0, max(0.0, remaining / total.total_seconds())))
