"""S7 条件別リファレンスソルバ（ADR-014: 情報境界をシグネチャで強制）。

- OR-full: 所有(記号)×最終目撃(時空間)×視覚署名(ベクトル)の三系統融合。低マージンは確認(X5)。
- OR-vec : ベクトルのみ。所有関係を扱えず種プロトタイプ最近傍（owner非依存）→ 瓜二つで誤配送。
- OR-sym : 記号(所有＋最終目撃ゾーン)のみ・視覚署名なし → ID無し瓜二つを判別できず確認委譲。
- B0     : 情報経路なし。所有を解けず任意配送（誰のものかに答えられない）。
"""

from __future__ import annotations

import numpy as np

from orx.exp.suites.s7_ownership.grounding import cos
from orx.exp.suites.s7_ownership.model import S7Episode, S7World
from orx.oracle.scenarios.s7 import ESCALATE

CONDITIONS = ["OR-full", "OR-vec", "OR-sym", "B0"]


def decide(
    condition: str,
    request_owner: str,
    episode: S7Episode,
    world: S7World,
    b0_rng: np.random.Generator,
) -> int:
    """配送対象の物体インデックスを返す（ESCALATE=確認委譲）。"""
    objs = episode.objects
    ledger = episode.ledger
    if condition == "B0":
        return int(b0_rng.integers(0, len(objs)))  # 所有を解けない → 任意
    if condition == "OR-vec":
        scores = [cos(o.embedding, episode.prototype) for o in objs]  # owner非依存クエリ
        return max(range(len(objs)), key=lambda i: scores[i])
    if condition == "OR-sym":
        zone = ledger.last_seen_zone[request_owner]
        cands = [i for i, o in enumerate(objs) if o.zone == zone]
        return cands[0] if len(cands) == 1 else ESCALATE  # 署名なしで瓜二つ判別不能
    if condition == "OR-full":
        zone = ledger.last_seen_zone[request_owner]
        note = ledger.note_embedding[request_owner]
        cands = [i for i, o in enumerate(objs) if o.zone == zone] or list(range(len(objs)))
        sims = sorted(((cos(objs[i].embedding, note), i) for i in cands), reverse=True)
        top1, best = sims[0]
        top2 = sims[1][0] if len(sims) > 1 else -1.0
        if top1 - top2 < world.margin_threshold:
            return ESCALATE  # 確信不足 → 確認行動(X5)で誤配送を回避
        return best
    raise ValueError(f"未知の条件 {condition!r}")
