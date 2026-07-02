"""S7 エージェント条件（agent 射程・live, R-B）。

**射程の注記**: S7 の H2/H4/H6（記号×時空間×ベクトルの三系統融合）の機構検証は決定的
アブレーション（OR-vec/OR-sym/B0）が既に担っている。本 agent 版はそれとは別に、
「**双対表現の蒸留情報（最終目撃ゾーン＋特徴メモ署名との視覚類似度）を実 LLM が使って
ID 無し瓜二つの所有物を正しく配送し、確信不足では委譲できるか**」を測る。

接地（署名→類似度）は知覚層が決定的に行い、**エージェントは蒸留済みの類似度＋ゾーンから
推論する**（真の所有者 true_owner は渡さない＝漏洩なし）:
- OR-full-llm: 各入居者の最終目撃ゾーン＋候補物体への note 署名コサイン類似度＋確認マージン規則。
- B0-llm:      物体の現在ゾーンのみ（署名類似度・所有台帳なし）→ 瓜二つを判別できず推測。

全入居者を1コールでまとめて配送割当（`resident=objN` 形式）し、`delivery_outcome` で採点する。
LLM はプロバイダIF経由のみ（PROJECT.md §5.2-5: 世界を知る窓はツール/蒸留情報のみ）。
"""

from __future__ import annotations

import json

from orx.agent.toolloop import AgentRunResult
from orx.common.providers import LLMClient
from orx.exp.agent_tools import ANSWER_RULES, build_agent
from orx.exp.suites.s7_ownership.grounding import cos
from orx.exp.suites.s7_ownership.model import S7Episode, S7World
from orx.oracle.scenarios.s7 import ESCALATE

AGENT_CONDITIONS = ["OR-full-llm", "OR-full-llm-guarded", "B0-llm"]

_ESCALATE_TOKEN = "ESCALATE"


def _obj_id(i: int) -> str:
    return f"obj{i}"


def parse_owner_answer(answer: str, n_objects: int) -> dict[str, int]:
    """`入居者=objN,...` を {resident: index|ESCALATE} に。未知/不正は ESCALATE に丸める。"""
    out: dict[str, int] = {}
    for part in (answer or "").strip().split(","):
        if "=" not in part:
            continue
        res, _, target = part.partition("=")
        res = res.strip()
        target = target.strip()
        if not res:
            continue
        if target.upper().startswith(_ESCALATE_TOKEN):
            out[res] = ESCALATE
            continue
        idx = _parse_obj_index(target)
        out[res] = idx if (idx is not None and 0 <= idx < n_objects) else ESCALATE
    return out


def _parse_obj_index(token: str) -> int | None:
    t = token.lower()
    if t.startswith("obj"):
        t = t[3:]
    try:
        return int(t)
    except ValueError:
        return None


def _answer_rules() -> str:
    return (
        "回答形式: 各入居者を `入居者=objN`（配送する物体）または `入居者=ESCALATE`"
        "（人間確認へ委譲）で表す。カンマ区切り・入居者は与えられた順で1行に。\n" + ANSWER_RULES
    )


def _or_table(episode: S7Episode, world: S7World) -> list[dict]:
    """各入居者の候補物体（最終目撃ゾーン一致）への note 署名コサイン類似度（降順）。"""
    objs = episode.objects
    ledger = episode.ledger
    rows: list[dict] = []
    for r in world.residents:
        zone = ledger.last_seen_zone[r]
        note = ledger.note_embedding[r]
        cands = [i for i, o in enumerate(objs) if o.zone == zone] or list(range(len(objs)))
        sims = sorted(
            ((round(cos(objs[i].embedding, note), 4), _obj_id(i)) for i in cands),
            reverse=True,
        )
        rows.append(
            {
                "resident": r,
                "last_seen_zone": zone,
                "candidates": [{"obj": oid, "similarity": s} for s, oid in sims],
            }
        )
    return rows


def _or_prompt(episode: S7Episode, world: S7World) -> str:
    table = _or_table(episode, world)
    return (
        "あなたは介護施設の配送エージェント。各入居者の所有物（ID無し小物）を、台帳の特徴メモ"
        "署名との視覚類似度（記号×ベクトル×時空間の蒸留）から同定する。物体はIDが無いため "
        "obj0.. の連番で参照する。\n"
        f"確認規則: 候補の類似度 top1−top2 マージンが {world.margin_threshold} 未満なら、"
        "誤配送回避のため ESCALATE（人間確認, X5）にする。所有は観測不可能な情報的関係なので"
        "類似度が拮抗する瓜二つは推測せず委譲するのが安全。\n"
        f"入居者ごとの候補（similarity 降順・最終目撃ゾーン付き）:\n"
        f"{json.dumps(table, ensure_ascii=False)}\n"
        "各入居者に最も類似する obj を割り当て、マージン不足なら ESCALATE。\n" + _answer_rules()
    )


def _b0_prompt(episode: S7Episode, world: S7World) -> str:
    obj_zone = [{"obj": _obj_id(i), "zone": o.zone} for i, o in enumerate(episode.objects)]
    return (
        "あなたは介護施設の配送エージェント。各入居者へ所有物を配送するが、所有関係や視覚署名の"
        "情報は無く、物体の現在ゾーンしか分からない。物体は obj0.. の連番で参照する。\n"
        f"物体（obj=ゾーン）: {json.dumps(obj_zone, ensure_ascii=False)}\n"
        f"入居者の居室: {json.dumps(world.rooms, ensure_ascii=False)}\n"
        f"入居者の最終目撃ゾーン: {json.dumps(episode.ledger.last_seen_zone, ensure_ascii=False)}\n"
        "各入居者へ配送する obj を選べ（手掛かりが足りなければ ESCALATE）。\n" + _answer_rules()
    )


def _resident_margins(episode: S7Episode, world: S7World) -> dict[str, float]:
    """各入居者の確信度マージン（候補 note 類似度の top1−top2）を蒸留情報から決定的に算出する。

    決定的 OR-full の確認規則（reference.decide）と同一の量。**真の所有者 true_owner は使わない**
    （観測 embedding と台帳 note 署名のコサインのみ＝漏洩なし）。単一候補は top2=-1.0 で確信扱い。
    """
    objs = episode.objects
    ledger = episode.ledger
    out: dict[str, float] = {}
    for r in world.residents:
        zone = ledger.last_seen_zone[r]
        note = ledger.note_embedding[r]
        cands = [i for i, o in enumerate(objs) if o.zone == zone] or list(range(len(objs)))
        sims = sorted((cos(objs[i].embedding, note) for i in cands), reverse=True)
        top1 = sims[0]
        top2 = sims[1] if len(sims) > 1 else -1.0
        out[r] = top1 - top2
    return out


def apply_margin_guard(
    decisions: dict[str, int], episode: S7Episode, world: S7World
) -> dict[str, int]:
    """**ツール側の安全ガード**: 確信度マージン < 閾値 の入居者は LLM の決定に関わらず ESCALATE へ。

    決定的 OR-full の「低マージンは確認(X5)へ委譲」規則を、プロンプト依存ではなく**システムが
    ハード強制**する（S7 mixed 所見への対処）。高マージン（署名が明確）の判別は LLM に委ねる
    ＝エージェントの裁量を残しつつ、危険な低マージン配送だけを機械的に拒否する安全ツール。
    蒸留マージンのみ使用（真値非参照）。
    """
    margins = _resident_margins(episode, world)
    guarded = dict(decisions)
    for r in world.residents:
        if margins.get(r, 0.0) < world.margin_threshold:
            guarded[r] = ESCALATE
    return guarded


def decide_episode_llm(
    condition: str,
    episode: S7Episode,
    world: S7World,
    llm: LLMClient,
) -> tuple[dict[str, int], AgentRunResult]:
    """1 エピソードの全入居者の配送決定 {resident: index|ESCALATE} と実行統計を返す。

    `OR-full-llm-guarded` は **OR-full-llm と同一プロンプト**（＝キャッシュヒット・$0）で LLM 決定を
    得た後、`apply_margin_guard` で低マージン配送をツール側で ESCALATE に上書きする。
    """
    use_or = condition.startswith("OR-full-llm")
    prompt = _or_prompt(episode, world) if use_or else _b0_prompt(episode, world)
    agent = build_agent(llm, prompt, [])
    rr = agent.run("各入居者へ配送する物体を割り当ててください。")
    decisions = parse_owner_answer(rr.answer, len(episode.objects))
    # 未回答の入居者は安全側で ESCALATE（誤配送より委譲）。
    for r in world.residents:
        decisions.setdefault(r, ESCALATE)
    if condition == "OR-full-llm-guarded":
        decisions = apply_margin_guard(decisions, episode, world)
    return decisions, rr
