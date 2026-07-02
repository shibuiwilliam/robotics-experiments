"""S4 エージェント条件（agent 射程・live, R-B）。

**射程の注記**: 同一性解決(anchoring)と信念調停(belief)の機構オン/オフは決定的アブレーション
（OR-no-identity/OR-no-belief/OR-sym）が既に担う。本 agent 版はその中の **H2/H4「外観（視覚署名）
による ID 無し同定」軸**に焦点を当て、実 LLM が観測を台帳資産へ対応付ける質を測る:
- OR-full-llm: 各観測の**位置近接＋視覚署名コサイン**の蒸留候補表で対応付け（署名で曖昧解消）。
- OR-sym-llm:  **位置近接のみ**（署名なし）→ 位置が曖昧だと誤対応する。

信念調停（確信度加重）は両条件とも決定的に適用し、**対応付けの質の差**が異常見逃しに波及する
ことを観測する（mis-anchor → 異常観測が誤集約 → 真の異常を見逃す）。真値（true_asset /
true_anomaly）は**プロンプトに渡さない**（採点は oracle/scorer 由来のヘルパで真値照合）。
"""

from __future__ import annotations

import json

from orx.agent.toolloop import AgentRunResult
from orx.common.providers import LLMClient
from orx.exp.agent_tools import ANSWER_RULES, build_agent
from orx.exp.suites.s4_inspection.grounding import cos, neg_distance
from orx.exp.suites.s4_inspection.model import S4Episode, S4World

AGENT_CONDITIONS = ["OR-full-llm", "OR-sym-llm"]


def _obs_id(i: int) -> str:
    return f"obs{i}"


def parse_anchor_answer(answer: str, n_obs: int, asset_ids: set[str]) -> dict[int, str | None]:
    """`obs0=V-205,...` を {obs_index: asset_id|None} に。未知資産は None。"""
    out: dict[int, str | None] = {}
    for part in (answer or "").strip().split(","):
        if "=" not in part:
            continue
        oid, _, aid = part.partition("=")
        oid = oid.strip().lower()
        aid = aid.strip()
        if oid.startswith("obs"):
            oid = oid[3:]
        try:
            idx = int(oid)
        except ValueError:
            continue
        if 0 <= idx < n_obs:
            out[idx] = aid if aid in asset_ids else None
    return out


def _candidates(episode: S4Episode, idx: int, full: bool) -> list[dict]:
    obs = episode.observations[idx]
    rows: list[dict] = []
    for entry in episode.ledger:
        nd = round(neg_distance(obs.pos, entry.ledger_pos), 4)
        row: dict = {"asset": entry.asset_id, "neg_dist": nd}
        if full:
            row["sig_cos"] = round(cos(obs.signature, entry.ref_signature), 4)
        rows.append(row)
    rows.sort(key=lambda r: r.get("sig_cos", 0.0) if full else r["neg_dist"], reverse=True)
    return rows


def _obs_table(episode: S4Episode, full: bool) -> list[dict]:
    table: list[dict] = []
    for i, obs in enumerate(episode.observations):
        table.append(
            {
                "obs": _obs_id(i),
                "viewpoint": obs.viewpoint,
                "candidates": _candidates(episode, i, full),
            }
        )
    return table


def _answer_rules() -> str:
    return (
        "回答形式: 各観測を `obsN=asset_id`（対応付ける台帳資産）で表す。カンマ区切り・obs は"
        "与えられた順で1行に。\n" + ANSWER_RULES
    )


def _or_prompt(episode: S4Episode) -> str:
    return (
        "あなたはプラント点検の同一化エージェント。台帳資産（P&ID個体）と、ID無しの知覚観測を"
        "対応付ける。各観測には台帳各資産への**位置近接(neg_dist, 大きいほど近い)**と"
        "**視覚署名コサイン(sig_cos, 大きいほど一致)**の蒸留候補が与えられる。\n"
        "位置はノイズで曖昧になりうるので、**視覚署名(sig_cos)を主たる手掛かり**に同定せよ"
        "（据付時の参照署名との一致）。\n"
        "観測（候補は sig_cos 降順）: "
        + json.dumps(_obs_table(episode, True), ensure_ascii=False)
        + "\n"
        "各観測を最も一致する資産へ対応付けよ。\n" + _answer_rules()
    )


def _sym_prompt(episode: S4Episode) -> str:
    return (
        "あなたはプラント点検の同一化エージェント。台帳資産とID無しの知覚観測を対応付ける。"
        "視覚署名は利用できず、各観測には台帳各資産への**位置近接(neg_dist, 大きいほど近い)**"
        "のみが与えられる。\n"
        "観測（候補は neg_dist 降順）: "
        + json.dumps(_obs_table(episode, False), ensure_ascii=False)
        + "\n"
        "各観測を最も位置が近い資産へ対応付けよ。\n" + _answer_rules()
    )


def anchor_llm(
    condition: str, episode: S4Episode, world: S4World, llm: LLMClient
) -> tuple[dict[int, str | None], AgentRunResult]:
    """各観測の対応付け {obs_index: asset_id|None} と実行統計を返す。"""
    prompt = _or_prompt(episode) if condition == "OR-full-llm" else _sym_prompt(episode)
    agent = build_agent(llm, prompt, [])
    rr = agent.run("各観測を台帳資産へ対応付けてください。")
    asset_ids = {a.asset_id for a in world.assets}
    anchors = parse_anchor_answer(rr.answer, len(episode.observations), asset_ids)
    for i in range(len(episode.observations)):
        anchors.setdefault(i, None)
    return anchors, rr
