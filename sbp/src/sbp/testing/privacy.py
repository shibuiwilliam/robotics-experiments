"""潜在復元攻撃の雛形（不変条件 9 の検証、M7-06）。

不変条件 9 は「生映像・音声を永続化しない。永続化は潜在と記号のみ」と言う。これは**潜在が
生観測より安全だという仮定**に立っている。その仮定は検証しなければ信用できない——潜在が線形に
生観測へ戻せるなら、生を捨てた意味はほとんど無い。

ここは攻撃者側に立つ道具を置く。``sbp.testing`` に置くのは不変条件 10（真値は testing から
しか読まない）のため——攻撃は真値（復元したい元の値、個人のラベル）を必要とするので、本体の層
には置けない。

**攻撃を 2 種類に分ける。**

1. **属性復元**（``attribute_recovery``）: 潜在から連続値の観測を回帰で戻す。
2. **同定**（``identity_linkage``）: 別の時間帯の潜在同士を最近傍で突き合わせ、同じ個体を
   当てられるかを測る。こちらが**個人情報としての危険**である。

**陽性対照を必ず取るのが要点である。** 「何も復元できませんでした」という結果は、潜在が安全
だという証拠にも、攻撃の実装が壊れているという証拠にもなる。区別するには、**復元できるべきもの
が復元できること**を同じ攻撃で確かめる必要がある。``PrivacyAudit.is_conclusive`` が偽なら、
その監査結果は「安全」ではなく「測れていない」である。

現状の限界（正直に記す）: 攻撃者は線形回帰と最近傍しか使わない。深い復号器を学習させれば
もっと戻せる可能性があり、ここで安全と出ても「線形には戻せない」までしか言えない。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from sbp.core.errors import SbpError
from sbp.core.logging import get_logger

__all__ = [
    "AttackResult",
    "PrivacyAudit",
    "attribute_recovery",
    "identity_linkage",
]

log = get_logger(__name__)


class PrivacyAuditError(SbpError):
    """攻撃を実行できない（形状の不一致、標本不足）。"""


@dataclass(frozen=True, slots=True)
class AttackResult:
    """攻撃 1 件の結果。

    Attributes:
        name: 攻撃の名前。
        score: 成功度（0〜1 に正規化。1 が完全復元）。
        chance: 当て推量の水準（この値を超えないなら復元できていない）。
        samples: 使った標本数。
        detail: 人が読む説明。
    """

    name: str
    score: float
    chance: float
    samples: int
    detail: str

    @property
    def lift(self) -> float:
        """当て推量を超えた分。0 以下なら復元できていない。"""
        if self.chance >= 1.0:
            return 0.0
        return max(0.0, (self.score - self.chance) / (1.0 - self.chance))

    @property
    def recovered(self) -> bool:
        """復元できたと言えるか（当て推量を明確に超えたか）。"""
        return self.lift > 0.2


@dataclass(frozen=True, slots=True)
class PrivacyAudit:
    """監査結果（陽性対照つき）。

    Attributes:
        control: 陽性対照——**復元できるべき**攻撃の結果。これが失敗していたら監査は無効。
        target: 本命——**復元できてはいけない**攻撃の結果。
    """

    control: AttackResult
    target: AttackResult

    @property
    def is_conclusive(self) -> bool:
        """結論を出せる監査か。

        陽性対照が成功していなければ、本命が失敗していても「安全」とは言えない——攻撃の実装や
        標本が壊れていた可能性と区別できない。
        """
        return self.control.recovered

    @property
    def leaks(self) -> bool:
        """漏れているか。結論を出せない監査では常に真を返す（安全側に倒す）。"""
        if not self.is_conclusive:
            return True
        return self.target.recovered

    def explain(self) -> str:
        """人が読む判定。"""
        if not self.is_conclusive:
            return (
                f"監査不成立: 陽性対照「{self.control.name}」が復元に失敗した"
                f"（lift {self.control.lift:.3f}）。攻撃の実装か標本が壊れている可能性があり、"
                f"本命「{self.target.name}」の失敗を安全の証拠にできない"
            )
        if self.target.recovered:
            return (
                f"漏洩: 「{self.target.name}」が lift {self.target.lift:.3f} で復元に成功した"
                f"（{self.target.detail}）。潜在から戻せるので、生を捨てても保護になっていない"
            )
        return (
            f"漏洩なし（線形・最近傍の範囲で）: 陽性対照「{self.control.name}」は "
            f"lift {self.control.lift:.3f} で成功し、本命「{self.target.name}」は "
            f"lift {self.target.lift:.3f} で失敗した。より強い攻撃者は想定していない"
        )


def attribute_recovery(
    latents: NDArray[np.float64],
    targets: NDArray[np.float64],
    *,
    name: str,
    seed: int,
    holdout: float = 0.3,
) -> AttackResult:
    """潜在から連続値の属性を線形回帰で復元する。

    学習と評価を分ける。同じ標本で測ると、次元が標本数に近いとき常に完全復元に見える
    （リッジでも過適合する）——それは漏洩ではなく測り方の誤りである。

    Args:
        latents: 潜在ベクトル ``(n, d)``。
        targets: 復元したい値 ``(n,)``。真値なので ``sbp.testing`` 内からのみ渡せる。
        name: 攻撃の名前。
        seed: 分割の乱数種。
        holdout: 評価に回す割合。

    Returns:
        ``score`` は決定係数 R²（0 未満は 0 に丸める）、``chance`` は 0（平均予測の R²）。

    Raises:
        PrivacyAuditError: 標本が足りない、または形状が合わない。
    """
    latents = np.asarray(latents, dtype=np.float64)
    targets = np.asarray(targets, dtype=np.float64).reshape(-1)
    if latents.ndim != 2 or latents.shape[0] != targets.shape[0]:
        raise PrivacyAuditError(
            f"latents {latents.shape} and targets {targets.shape} do not line up"
        )
    n_total = latents.shape[0]
    n_test = round(n_total * holdout)
    if n_test < 5 or n_total - n_test < latents.shape[1] + 2:
        raise PrivacyAuditError(
            f"need more samples: {n_total} rows for a {latents.shape[1]}-dim latent leaves too "
            "little to fit and evaluate separately; an in-sample score would look like a leak"
        )

    order = np.random.default_rng(seed).permutation(n_total)
    test_idx, train_idx = order[:n_test], order[n_test:]
    x_train = np.column_stack([latents[train_idx], np.ones(train_idx.size)])
    x_test = np.column_stack([latents[test_idx], np.ones(test_idx.size)])
    y_train, y_test = targets[train_idx], targets[test_idx]

    ridge = 1e-6 * np.eye(x_train.shape[1])
    weights = np.linalg.solve(x_train.T @ x_train + ridge, x_train.T @ y_train)
    predicted = x_test @ weights

    residual = float(np.sum((y_test - predicted) ** 2))
    # 当て推量は「学習集合の平均を答える」こと。評価集合の平均を使うと攻撃者に情報を与えてしまう
    baseline = float(np.sum((y_test - float(np.mean(y_train))) ** 2))
    r_squared = 0.0 if baseline <= 0.0 else max(0.0, 1.0 - residual / baseline)

    log.debug("testing.attribute_recovery", attack=name, r2=r_squared, samples=n_total)
    return AttackResult(
        name=name,
        score=r_squared,
        chance=0.0,
        samples=n_total,
        detail=f"線形回帰の holdout R² = {r_squared:.3f}（{n_test} 件で評価）",
    )


def identity_linkage(
    latents_a: NDArray[np.float64],
    latents_b: NDArray[np.float64],
    *,
    name: str = "identity linkage",
) -> AttackResult:
    """別の時間帯の潜在同士を最近傍で突き合わせ、同じ個体を当てられるかを測る。

    ``latents_a[i]`` と ``latents_b[i]`` が同じ個体である前提で、``a`` の各行に最も近い ``b`` の
    行が本当に同じ添字かを数える。**これが当たると、外観埋め込みを保存していなくても個体を
    追跡できる**——不変条件 9 が守りたいのはこの経路である。

    Args:
        latents_a: 時間帯 A の潜在 ``(n, d)``。
        latents_b: 時間帯 B の潜在 ``(n, d)``。行の順序は A と対応していること。
        name: 攻撃の名前。

    Returns:
        ``score`` は top-1 一致率、``chance`` は ``1/n``。

    Raises:
        PrivacyAuditError: 形状が合わない、または個体が 2 つ未満。
    """
    latents_a = np.asarray(latents_a, dtype=np.float64)
    latents_b = np.asarray(latents_b, dtype=np.float64)
    if latents_a.shape != latents_b.shape:
        raise PrivacyAuditError(
            f"the two windows must hold the same individuals in the same order, got "
            f"{latents_a.shape} and {latents_b.shape}"
        )
    n_entities = latents_a.shape[0]
    if n_entities < 2:
        raise PrivacyAuditError("linkage needs at least 2 individuals to be meaningful")

    # cosine 距離（潜在索引が HNSW/cosine なので、攻撃者も同じ尺度を使える）
    norm_a = latents_a / np.maximum(np.linalg.norm(latents_a, axis=1, keepdims=True), 1e-12)
    norm_b = latents_b / np.maximum(np.linalg.norm(latents_b, axis=1, keepdims=True), 1e-12)
    similarity = norm_a @ norm_b.T
    matched = np.argmax(similarity, axis=1)
    hit_rate = float(np.mean(matched == np.arange(n_entities)))

    log.debug("testing.identity_linkage", attack=name, top1=hit_rate, entities=n_entities)
    return AttackResult(
        name=name,
        score=hit_rate,
        chance=1.0 / n_entities,
        samples=n_entities,
        detail=f"cosine 最近傍の top-1 一致率 = {hit_rate:.3f}（{n_entities} 個体）",
    )
