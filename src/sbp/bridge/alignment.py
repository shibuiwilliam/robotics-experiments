"""整合残差（設計書 §0.3 テーゼ 3、§4.6、§5.2）。

中核テーゼ 3「接地は分類ではなく写像の対である」の数値的な中身がこれである。

- **A**（抽象化写像）: 潜在 z → 記号状態（プローブ出力を束ねたもの）
- **f**（潜在ダイナミクス）: z_t → z_{t+1}（WM が学ぶ。M2 では合成世界が与える）
- **T**（記号遷移）: 記号状態 → 記号状態（事象計算 ``ontology/process/*.lp`` から導く）

整合残差は ``‖A(f(z,a)) − T(A(z),a)‖``、つまり「潜在を進めてから記号化した結果」と
「記号化してから記号的に進めた結果」のずれである。両者が一致する（可換である）とき、
潜在と記号が同じ世界を語っていることになる。

**なぜ AUROC だけでは足りないか**: プローブは各時点を正しく分類しながら、時間的に整合しない
系列を出しうる（例: Running が 1 標本ごとに真偽を往復する）。静的精度は高いが、記号遷移が
許さない振る舞いである。そういうプローブを active にすると信念更新の遷移事前と衝突し、
事後が暴れる。だから昇格ゲートの第 3 条件に整合残差を置いている（CLAUDE.md の禁止事項
「静的精度だけでプローブを active にする」はこれを指す）。
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Final

import numpy as np

from sbp.core.errors import BridgeError
from sbp.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from numpy.typing import NDArray

__all__ = [
    "AlignmentReport",
    "SymbolicTransition",
    "alignment_residual",
    "equipment_maintenance_transition",
]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SymbolicTransition:
    """記号遷移 T（事象計算から導く）。

    「いま成立しているフルーエントの集合」から「次の時点で成立しうるフルーエントの集合」への
    写像。事象が無ければ慣性により状態は変わらない（事象計算の基本規則）。

    Attributes:
        exclusive_pairs: 排他フルーエントの対（同時に成立できない）。
        max_simultaneous_changes: 1 刻みで変化しうるフルーエントの数の上限。慣性の強さを
            表す。設備の状態が 1 刻みで複数同時に変わるのは、事象が同時に複数起きた場合だけで
            稀である。
    """

    exclusive_pairs: tuple[tuple[str, str], ...] = ()
    max_simultaneous_changes: int = 1

    def allows(self, before: Mapping[str, bool], after: Mapping[str, bool]) -> bool:
        """この遷移が記号的に許されるか。

        Args:
            before: 遷移前の記号状態（フルーエント → 成立）。
            after: 遷移後の記号状態。
        """
        # 排他制約を見る。遷移後に排他フルーエントが同時成立していたら許されない。
        for a, b in self.exclusive_pairs:
            if after.get(a, False) and after.get(b, False):
                return False
        # 慣性: 1 刻みで変化する数が上限以内であること
        changes = sum(1 for k in after if before.get(k, False) != after.get(k, False))
        return changes <= self.max_simultaneous_changes

    def predict(self, before: Mapping[str, bool]) -> dict[str, bool]:
        """事象が無い場合の次状態（慣性）。

        Args:
            before: 遷移前の記号状態。
        """
        return dict(before)


def equipment_maintenance_transition() -> SymbolicTransition:
    """設備保全ドメインの記号遷移（``ontology/process/equipment-maintenance.lp`` に対応）。

    排他は ``Running`` と ``UnderMaintenance``（TTL の ``mutuallyExclusiveWith`` と一致）。
    """
    return SymbolicTransition(
        exclusive_pairs=(("Running", "UnderMaintenance"),),
        max_simultaneous_changes=2,  # 停止と故障開始は同時に起きうる
    )


@dataclass(frozen=True, slots=True)
class AlignmentReport:
    """整合残差の内訳。

    Attributes:
        residual: 正規化した残差（0 が完全に整合、1 が全く整合しない）。
        violations: 記号遷移が許さない遷移の数。
        transitions: 評価した遷移の総数。
        exclusive_violations: 排他フルーエントの同時成立の数。
        flicker_rate: ちらつき率（1 刻みで真偽が往復した割合）。
    """

    residual: float
    violations: int
    transitions: int
    exclusive_violations: int
    flicker_rate: float

    @property
    def is_aligned(self) -> bool:
        """実用上整合していると見なせるか（残差が十分小さい）。"""
        return self.residual < 0.05


_FLICKER_WINDOW: Final = 3
"""ちらつき判定の窓（この幅で真偽が往復したらちらつきとみなす）。"""


def alignment_residual(
    symbolic_states: Sequence[Mapping[str, bool]],
    transition: SymbolicTransition,
) -> AlignmentReport:
    """記号状態の系列から整合残差を計算する。

    ここでの ``symbolic_states`` は ``A(z_t)``、つまりプローブ出力を閾値で記号化した系列である。
    合成世界では ``f`` が真の物理なので、系列そのものが ``A(f(z_t))`` を含んでいる。したがって
    「隣接する記号状態の遷移が T に許されるか」を数えることで残差が求まる。

    実 WM（M4）では ``f`` をモデルが与えるため、同じ関数に「モデルが進めた潜在から作った
    記号状態」を渡せば同じ意味の残差になる。

    Args:
        symbolic_states: 時系列順の記号状態。
        transition: 記号遷移 T。

    Returns:
        残差の内訳。

    Raises:
        BridgeError: 状態が 2 つ未満の場合（遷移が評価できない）。
    """
    if len(symbolic_states) < 2:
        raise BridgeError(
            f"alignment residual needs at least 2 states to evaluate a transition, "
            f"got {len(symbolic_states)}"
        )

    violations = 0
    exclusive = 0
    for before, after in pairwise(symbolic_states):
        if not transition.allows(before, after):
            violations += 1
        for a, b in transition.exclusive_pairs:
            if after.get(a, False) and after.get(b, False):
                exclusive += 1

    transitions = len(symbolic_states) - 1
    flicker = _flicker_rate(symbolic_states)

    # 残差は「許されない遷移の割合」と「ちらつき率」の合成。ちらつきを別に数えるのは、
    # 1 刻みの往復が max_simultaneous_changes では捕まらないことがあるため
    # （1 つのフルーエントが往復するだけなら変化数は 1 で、慣性の上限を超えない）。
    residual = float(np.clip(violations / transitions + flicker, 0.0, 1.0))

    log.debug(
        "bridge.alignment_evaluated",
        residual=residual,
        violations=violations,
        transitions=transitions,
        flicker_rate=flicker,
    )
    return AlignmentReport(
        residual=residual,
        violations=violations,
        transitions=transitions,
        exclusive_violations=exclusive,
        flicker_rate=flicker,
    )


def _flicker_rate(states: Sequence[Mapping[str, bool]]) -> float:
    """ちらつき率（短い窓で真偽が往復した割合）。

    事象昇格のヒステリシスと最小持続時間（設計書 §5.3）が防ぐべき現象そのもの。プローブの
    段階で測ることで、昇格の前に「この潜在では安定した記号化ができない」と分かる。
    """
    if len(states) < _FLICKER_WINDOW:
        return 0.0
    keys = set().union(*(set(s.keys()) for s in states))
    flickers = 0
    checks = 0
    for key in keys:
        series = [bool(s.get(key, False)) for s in states]
        for i in range(len(series) - 2):
            checks += 1
            # A → not A → A のパターン
            if series[i] != series[i + 1] and series[i + 1] != series[i + 2]:
                flickers += 1
    return 0.0 if checks == 0 else flickers / checks


def symbolize(
    probe_outputs: Mapping[str, NDArray[np.float64]], *, threshold: float = 0.5
) -> list[dict[str, bool]]:
    """プローブ出力の束を記号状態の系列にする（抽象化写像 A）。

    Args:
        probe_outputs: フルーエント名 → 成立確率の列。
        threshold: 記号化の閾値。

    Returns:
        時系列順の記号状態。

    Raises:
        BridgeError: 出力の長さが揃っていない場合。
    """
    if not probe_outputs:
        raise BridgeError("cannot symbolize an empty set of probe outputs")
    lengths = {len(v) for v in probe_outputs.values()}
    if len(lengths) != 1:
        raise BridgeError(f"probe outputs have mismatched lengths: {sorted(lengths)}")
    count = lengths.pop()
    return [
        {name: bool(values[i] >= threshold) for name, values in probe_outputs.items()}
        for i in range(count)
    ]
