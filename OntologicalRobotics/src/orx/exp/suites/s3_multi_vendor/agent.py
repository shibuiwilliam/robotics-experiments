"""S3 エージェント条件（agent 射程・live, R-B）。

**射程の注記**: S3 の決定的版は能力台帳のオンライン較正＋故障時再割当（T6）まで含む。本 agent 版は
それとは別に、「**共通オントロジーで正規化した語彙横断の能力契約表を実 LLM に与えると、
基準ベンダー語彙のみの場合より適格な機体を選べるか**（H1/H3 の agent 射程）」を、静的な
割当タスクで測る（オンライン較正・故障追従は決定的版が担う）。

- OR-full-llm: **全ベンダー**の能力契約を共通スキーマで正規化した表（machine_id・vendor・
  宣言可搬・信頼度）→ 工程要求（重量・素材）に適う機体を横断選定できる。
- B1-llm:      共通オントロジー無し → 他ベンダーの能力フィールドを翻訳できず、**基準ベンダー**の
  機体しか候補にできない（段取り替えで重い品種に基準ベンダーが届かないと失敗）。

真の能力プロファイル（true_payload_kg / true_material_success）は**プロンプトに渡さない**
（採点は oracle.allocation_correct が真値で行う＝漏洩なし）。
"""

from __future__ import annotations

import json

from orx.agent.toolloop import AgentRunResult
from orx.common.providers import LLMClient
from orx.exp.agent_tools import ANSWER_RULES, build_agent
from orx.exp.suites.s3_multi_vendor.model import ProductSpec, S3World

AGENT_CONDITIONS = ["OR-full-llm", "B1-llm"]


def _machine_rows(world: S3World, reference_only: bool) -> list[dict]:
    """能力契約を共通スキーマで正規化（宣言値のみ・真プロファイルは出さない）。"""
    rows: list[dict] = []
    for m in world.machines:
        if reference_only and m.vendor != world.reference_vendor:
            continue
        rows.append(
            {
                "machine_id": m.machine_id,
                "vendor": m.vendor,
                "declared_payload_kg": m.declared_payload_kg,
                "reliability": m.prior_success,
            }
        )
    return rows


def _task_rows(tasks: list[tuple[str, ProductSpec]]) -> list[dict]:
    return [
        {"task": tid, "product": p.name, "weight_kg": p.weight_kg, "material": p.material}
        for tid, p in tasks
    ]


def parse_alloc_answer(answer: str, valid_machines: set[str]) -> dict[str, str | None]:
    """`task0=m_a1,...` を {task: machine_id|None} に。未知機体は None。"""
    out: dict[str, str | None] = {}
    for part in (answer or "").strip().split(","):
        if "=" not in part:
            continue
        tid, _, mid = part.partition("=")
        tid = tid.strip()
        mid = mid.strip()
        if not tid:
            continue
        out[tid] = mid if mid in valid_machines else None
    return out


def _answer_rules() -> str:
    return (
        "回答形式: 各工程を `taskN=machine_id`（割り当てる機体）で表す。カンマ区切り・task は"
        "与えられた順で1行に。適格機体が無ければ `taskN=none`。\n" + ANSWER_RULES
    )


def _or_prompt(world: S3World, tasks: list[tuple[str, ProductSpec]]) -> str:
    return (
        "あなたは多ベンダー製造ラインの工程割当エージェント。共通オントロジーで正規化された"
        "**全ベンダー**の能力契約（宣言可搬重量・信頼度）が横断で読める。\n"
        f"能力契約（全機体）: {json.dumps(_machine_rows(world, False), ensure_ascii=False)}\n"
        f"工程（割り当てる品種）: {json.dumps(_task_rows(tasks), ensure_ascii=False)}\n"
        "各工程について、宣言可搬重量が品種重量**以上**の機体の中から信頼度が最大のものを選べ"
        "（ベンダーを跨いでよい）。\n" + _answer_rules()
    )


def _b1_prompt(world: S3World, tasks: list[tuple[str, ProductSpec]]) -> str:
    return (
        "あなたは多ベンダー製造ラインの工程割当エージェント。共通オントロジーが無く、他ベンダーの"
        f"能力フィールドは語彙が異なり翻訳できないため、基準ベンダー（{world.reference_vendor}）の"
        "機体しか能力契約を解せない。\n"
        "能力契約（基準ベンダーのみ）: "
        + json.dumps(_machine_rows(world, True), ensure_ascii=False)
        + "\n"
        f"工程（割り当てる品種）: {json.dumps(_task_rows(tasks), ensure_ascii=False)}\n"
        "各工程について、宣言可搬重量が品種重量以上の機体から信頼度最大を選べ。"
        "基準ベンダーに適格機体が無ければ none。\n" + _answer_rules()
    )


def allocate_tasks_llm(
    condition: str,
    tasks: list[tuple[str, ProductSpec]],
    world: S3World,
    llm: LLMClient,
) -> tuple[dict[str, str | None], AgentRunResult]:
    """工程列を1コールで割り当て {task_id: machine_id|None} と実行統計を返す。"""
    prompt = _or_prompt(world, tasks) if condition == "OR-full-llm" else _b1_prompt(world, tasks)
    agent = build_agent(llm, prompt, [])
    rr = agent.run("各工程を適切な機体へ割り当ててください。")
    valid = {m.machine_id for m in world.machines}
    alloc = parse_alloc_answer(rr.answer, valid)
    for tid, _p in tasks:
        alloc.setdefault(tid, None)
    return alloc, rr
