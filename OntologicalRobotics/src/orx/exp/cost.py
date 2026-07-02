"""live 計測のコスト概算（R-1 プリフライト）。

`mode=openai` で課金実行する前に、期待される LLM/埋め込み呼び出し数とトークン量・
USD レンジを **実APIを叩かずに** 見積もる。CLAUDE.md §6（大量記録は事前にユーザー承認）
を運用可能にするための装置。

設計:
- 作業単位（質問数・スキーマ数・クエリ数）は各タスクの**決定的ジェネレータ**を
  オフライン（物理記録なし・stub）で呼んで**実数**を数える。数えられない場合のみ
  保守的な定数にフォールバックし、その旨を assumptions に明示する。
- トークン/価格は透明な前提（assumptions）として保持し、CLI から上書きできる。
  概算は「桁を間違えない」ことが目的であり、正確な請求額ではない（前提を必ず印字する）。
- モデル名はコンフィグから解決された値をそのまま用いる（ハードコードしない, §6）。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from orx.common.config import WorldConfig, load_config
from orx.common.paths import repo_root
from orx.common.schemas import StrictModel
from orx.common.seeding import SeedTree
from orx.exp.runner import ExperimentConfig

OPENAI_API_KEY_ENV = "OPENAI_API_KEY"


class PreflightError(RuntimeError):
    """live 計測の事前条件が満たされていない（実行前に検出する）。"""


def _experiment_mode(config: ExperimentConfig) -> str:
    """実験コンフィグのプロバイダモードを解決する（task セクションの provider）。"""
    section = getattr(config, config.task, None)
    provider = getattr(section, "provider", None) if section is not None else None
    return provider.mode if provider is not None else "stub"


def preflight_live(config: ExperimentConfig) -> None:
    """`mode=openai` の実験を課金実行する前の事前検証。

    - openai モードなのに OPENAI_API_KEY が無ければ、最初のAPI呼び出しを待たずに
      実行可能なエラーで即座に止める（CLAUDE.md §6: 事故防止）。
    - stub/cache モードでは何もしない（オフライン経路は常に通る）。
    """
    mode = _experiment_mode(config)
    if mode != "openai":
        return
    section = getattr(config, config.task, None)
    provider = getattr(section, "provider", None) if section is not None else None
    if provider is not None:
        _assert_no_placeholder_model(provider)
    if not os.environ.get(OPENAI_API_KEY_ENV):
        raise PreflightError(
            f"provider.mode=openai ですが環境変数 {OPENAI_API_KEY_ENV} が未設定です。\n"
            "  - 課金実行には API キーとコスト承認が必要です（CLAUDE.md §6）。\n"
            "  - `.env` に OPENAI_API_KEY を設定するか、オフライン検証は "
            "provider.mode を stub/cache にしてください。\n"
            "  - 概算は `orx exp estimate <config>` で確認できます。"
        )


def _assert_no_placeholder_model(provider: object) -> None:
    """第2モデル再現テンプレ等の**プレースホルダ番兵**（`SET-...`）のまま課金実行するのを遮断する。

    モデル名を捏造せず、ユーザーが実在の日付付きスナップショットへ更新するよう促す（CLAUDE.md）。
    """
    from orx.common.providers import ProviderConfig, placeholder_models

    if not isinstance(provider, ProviderConfig):
        return
    holders = placeholder_models(provider)
    if holders:
        raise PreflightError(
            f"provider.mode=openai ですがモデル名がプレースホルダのままです: {holders}。\n"
            "  - 第2モデル再現などのテンプレは、組織で使う**正確な日付付きスナップショット名**へ\n"
            "    更新してから課金実行してください（モデル名の捏造は禁止・CLAUDE.md）。\n"
            "  - 概算（実APIなし）は `make exp-estimate-m2` で確認できます。"
        )


class TokenModel(StrictModel):
    """トークン/価格の前提（すべて上書き可能・印字される）。"""

    prompt_tokens_per_call: int = 1500  # ツール結果・履歴込みの平均入力
    completion_tokens_per_call: int = 160  # 平均出力（最終回答＋ツール引数）
    embedding_tokens_per_text: int = 200  # SOP文書・クエリ1件あたり
    tool_turns_with_tools: float = 3.0  # ツール使用条件の平均ターン数（<= max_turns 6）
    tool_turns_no_tools: float = 1.0  # B0（ツール無し・1ショット）
    scenario_turns_per_episode: float = 6.0  # シナリオ閉ループ 1 エピソードの平均 LLM 呼出数
    low_factor: float = 0.6  # レンジ下限係数
    high_factor: float = 1.8  # レンジ上限係数
    # 価格（USD / 100万トークン）。既定は保守的なプレースホルダ。実モデルの価格に
    # 合わせて --price-in/--price-out/--price-embed で上書きすること。
    price_in_per_mtok: float = 0.60
    price_out_per_mtok: float = 2.40
    price_embed_per_mtok: float = 0.02


class CostEstimate(StrictModel):
    task: str
    name: str
    mode: str
    llm_model: str
    embedding_model: str
    seeds: int
    units: dict[str, int]  # 作業単位（"questions"/"schemas"/"queries"/"docs" 等）
    units_exact: bool  # True=実ジェネレータで数えた / False=定数フォールバック
    agent_conditions: list[str]  # 実LLM/埋め込みを要する条件
    llm_calls: int
    embedding_calls: int
    est_prompt_tokens: int
    est_completion_tokens: int
    est_embedding_tokens: int
    est_total_tokens: int
    usd_low: float
    usd_mid: float
    usd_high: float
    assumptions: dict[str, float]
    notes: list[str]


# ----------------------------------------------------------------- unit counts


def _t2_questions_per_seed(world: WorldConfig, seed: int) -> int:
    """T2 の質問数を WMS から決定的に数える（物理記録不要・final_truth非依存）。

    内訳: Q1=instructions, Q2=未紐づけopen受注, Q3+Q4=2×ゾーン, Q5+Q6=2。
    質問**数**は box_zone の値に依存しないため記録なしで厳密に数えられる。
    """
    from orx.business.db import generate_wms

    with tempfile.TemporaryDirectory() as tmp:
        record = generate_wms(world, SeedTree(seed), Path(tmp) / "wms.sqlite")
    instructed = {i["order_id"] for i in record.instructions}
    open_unlinked = sum(
        1 for o in record.orders if str(o["status"]) == "open" and o["order_id"] not in instructed
    )
    return len(record.instructions) + open_unlinked + 2 * len(world.zones) + 2


def _t7_units_per_seed(world: WorldConfig, seed: int) -> tuple[int, int]:
    """T7 の (クエリ数, 文書数) を WMS+SOP から決定的に数える。"""
    from orx.business.db import generate_wms
    from orx.business.sop import generate_sops
    from orx.exp.suites import t7 as t7_suite

    with tempfile.TemporaryDirectory() as tmp:
        record = generate_wms(world, SeedTree(seed), Path(tmp) / "wms.sqlite")
    docs = generate_sops(record.skus, SeedTree(seed))
    queries = t7_suite.generate_queries(record, docs)
    return len(queries), len(docs)


# ----------------------------------------------------------------- estimators


def _estimate_t2(
    config: ExperimentConfig, world: WorldConfig, tm: TokenModel
) -> tuple[dict[str, int], bool, list[str], int, int]:
    """returns (units, exact, agent_conditions, llm_calls, embedding_calls=0)."""
    notes: list[str] = []
    try:
        per_seed = sum(_t2_questions_per_seed(world, s) for s in config.seeds)
        exact = True
    except Exception as exc:  # 生成失敗時は保守的定数（17問/世界）
        per_seed = 17 * len(config.seeds)
        exact = False
        notes.append(f"質問数を生成できず定数17/世界で概算（{type(exc).__name__}）")
    agent_conditions = [c for c in config.conditions if c != "OR-reference"]
    calls = 0
    for c in agent_conditions:
        turns = tm.tool_turns_no_tools if c == "B0" else tm.tool_turns_with_tools
        calls += round(per_seed * turns)
    return {"questions": per_seed}, exact, agent_conditions, calls, 0


def _estimate_t5(
    config: ExperimentConfig, tm: TokenModel
) -> tuple[dict[str, int], bool, list[str], int, int]:
    assert config.t5 is not None
    n_schemas = len(config.seeds) * config.t5.n_schemas_per_seed
    agent_conditions = [c for c in config.conditions if c == "llm"]
    # llm 支援は 1 スキーマ = 1 マッピング生成呼び出し
    calls = n_schemas * len(agent_conditions)
    notes: list[str] = []
    if not agent_conditions:
        notes.append("llm 条件が無いため LLM 呼び出しは 0（heuristic/handwritten はオフライン）")
    return {"schemas": n_schemas}, True, agent_conditions, calls, 0


def _estimate_t7(
    config: ExperimentConfig, world: WorldConfig, tm: TokenModel
) -> tuple[dict[str, int], bool, list[str], int, int]:
    notes: list[str] = []
    agent_conditions = [c for c in config.conditions if c == "vector-rag"]
    try:
        total_queries = 0
        docs_per_seed = 0
        for s in config.seeds:
            q, d = _t7_units_per_seed(world, s)
            total_queries += q
            docs_per_seed = d  # 文書数はシード非依存の設計だが最後の値を保持
        exact = True
    except Exception as exc:
        total_queries = 12 * len(config.seeds)
        docs_per_seed = 0
        exact = False
        notes.append(f"クエリ/文書数を生成できず定数で概算（{type(exc).__name__}）")
    # 埋め込み呼び出し: vector-rag はクエリ＋文書を埋め込む（文書はテキストキャッシュで
    # シード間共有されうるが、保守的に seed×docs を上限として数える）。
    embedding_calls = 0
    if agent_conditions:
        embedding_calls = total_queries + docs_per_seed * len(config.seeds)
    return (
        {"queries": total_queries, "docs": docs_per_seed},
        exact,
        agent_conditions,
        0,
        embedding_calls,
    )


def estimate_experiment(
    config: ExperimentConfig, token_model: TokenModel | None = None
) -> CostEstimate:
    """実験コンフィグからコスト概算を作る（実APIを叩かない）。"""
    tm = token_model or TokenModel()
    world = load_config(repo_root() / config.world_config, WorldConfig)
    provider = getattr(config, config.task, None) and getattr(config, config.task).provider
    if provider is None:
        from orx.common.providers import ProviderConfig

        provider = ProviderConfig()

    if config.task == "t2":
        units, exact, agent_conds, llm_calls, emb_calls = _estimate_t2(config, world, tm)
    elif config.task == "t5":
        units, exact, agent_conds, llm_calls, emb_calls = _estimate_t5(config, tm)
    elif config.task == "t7":
        units, exact, agent_conds, llm_calls, emb_calls = _estimate_t7(config, world, tm)
    else:
        # T1/T3/T4 は決定的（LLM/埋め込み不要）— 課金ゼロ。
        units, exact, agent_conds, llm_calls, emb_calls = ({}, True, [], 0, 0)

    prompt_tokens = llm_calls * tm.prompt_tokens_per_call
    completion_tokens = llm_calls * tm.completion_tokens_per_call
    embedding_tokens = emb_calls * tm.embedding_tokens_per_text
    total_tokens = prompt_tokens + completion_tokens + embedding_tokens

    def usd(factor: float) -> float:
        cost = (
            (prompt_tokens * tm.price_in_per_mtok)
            + (completion_tokens * tm.price_out_per_mtok)
            + (embedding_tokens * tm.price_embed_per_mtok)
        ) / 1_000_000.0
        return round(cost * factor, 4)

    notes: list[str] = []
    if config.task in ("t2", "t5", "t7"):
        # 個別 notes を再取得（estimator 関数内 notes は破棄したので主要点のみ補足）
        if not agent_conds:
            notes.append("実LLM/埋め込みを要する条件が無い（このコンフィグは課金ゼロ）。")
    else:
        notes.append(f"task={config.task} は決定的（LLM不要）。課金は発生しない。")
    if not exact:
        notes.append("作業単位は概算（定数フォールバック）。")
    notes.append("トークン/価格は前提値。実モデルの単価に合わせて --price-* で上書き可。")

    return CostEstimate(
        task=config.task,
        name=config.name,
        mode=provider.mode,
        llm_model=provider.llm_model,
        embedding_model=provider.text_embedding_model,
        seeds=len(config.seeds),
        units=units,
        units_exact=exact,
        agent_conditions=agent_conds,
        llm_calls=llm_calls,
        embedding_calls=emb_calls,
        est_prompt_tokens=prompt_tokens,
        est_completion_tokens=completion_tokens,
        est_embedding_tokens=embedding_tokens,
        est_total_tokens=total_tokens,
        usd_low=usd(tm.low_factor),
        usd_mid=usd(1.0),
        usd_high=usd(tm.high_factor),
        assumptions={
            "prompt_tokens_per_call": tm.prompt_tokens_per_call,
            "completion_tokens_per_call": tm.completion_tokens_per_call,
            "embedding_tokens_per_text": tm.embedding_tokens_per_text,
            "tool_turns_with_tools": tm.tool_turns_with_tools,
            "tool_turns_no_tools": tm.tool_turns_no_tools,
            "price_in_per_mtok": tm.price_in_per_mtok,
            "price_out_per_mtok": tm.price_out_per_mtok,
            "price_embed_per_mtok": tm.price_embed_per_mtok,
        },
        notes=notes,
    )


def estimate_scenario(config: object, token_model: TokenModel | None = None) -> CostEstimate:
    """シナリオ実験コンフィグ（agent/live）のコスト概算（実APIを叩かない）。

    agent 条件（`*-llm`）× シード を 1 エピソードずつ走らせ、各エピソードが平均
    `scenario_turns_per_episode` 回の LLM 呼び出し（act-perceive 反復・<= max_turns）を要すると
    仮定して桁を見積もる。前提は印字され、CLI から上書きできる。決定的条件は課金ゼロ。
    """
    from orx.exp.scenario import ScenarioExperimentConfig

    if not isinstance(config, ScenarioExperimentConfig):
        raise TypeError("estimate_scenario には ScenarioExperimentConfig が必要")
    tm = token_model or TokenModel()
    from orx.common.providers import ProviderConfig

    provider = config.provider or ProviderConfig()
    # agent（実LLM）条件: 名前に "llm" を含む（"OR-full-llm-guarded" 等の派生も拾う・上限見積り）。
    agent_conds = [c for c in config.conditions if "llm" in c.lower()]
    episodes = len(agent_conds) * len(config.seeds)
    llm_calls = round(episodes * tm.scenario_turns_per_episode)

    prompt_tokens = llm_calls * tm.prompt_tokens_per_call
    completion_tokens = llm_calls * tm.completion_tokens_per_call
    total_tokens = prompt_tokens + completion_tokens

    def usd(factor: float) -> float:
        cost = (
            prompt_tokens * tm.price_in_per_mtok + completion_tokens * tm.price_out_per_mtok
        ) / 1_000_000.0
        return round(cost * factor, 4)

    notes = [
        "シナリオ閉ループの概算（桁の確認用・正確な請求額ではない）。",
        "agent 条件のみ課金（決定的条件は LLM 不使用・$0）。",
        "act-perceive のターン数は前提値。実測キャッシュ後に置換すれば精緻化できる。",
    ]
    if not agent_conds:
        notes.append("このコンフィグに `*-llm` 条件が無い（課金ゼロ）。")

    return CostEstimate(
        task=f"scenario:{config.scenario}",
        name=config.name,
        mode=provider.mode,
        llm_model=provider.llm_model,
        embedding_model=provider.text_embedding_model,
        seeds=len(config.seeds),
        units={"episodes": episodes},
        units_exact=True,
        agent_conditions=agent_conds,
        llm_calls=llm_calls,
        embedding_calls=0,
        est_prompt_tokens=prompt_tokens,
        est_completion_tokens=completion_tokens,
        est_embedding_tokens=0,
        est_total_tokens=total_tokens,
        usd_low=usd(tm.low_factor),
        usd_mid=usd(1.0),
        usd_high=usd(tm.high_factor),
        assumptions={
            "scenario_turns_per_episode": tm.scenario_turns_per_episode,
            "prompt_tokens_per_call": tm.prompt_tokens_per_call,
            "completion_tokens_per_call": tm.completion_tokens_per_call,
            "price_in_per_mtok": tm.price_in_per_mtok,
            "price_out_per_mtok": tm.price_out_per_mtok,
        },
        notes=notes,
    )


def render_estimate(est: CostEstimate) -> list[str]:
    """CLI 表示用の行（人間可読・承認判断に必要な数値を網羅）。"""
    lines = [
        f"コスト概算: {est.name}（task={est.task}, mode={est.mode}）",
        f"  モデル: LLM={est.llm_model} / 埋め込み={est.embedding_model}",
        f"  シード数: {est.seeds} / 作業単位: {est.units} "
        f"({'実数' if est.units_exact else '概算'})",
        f"  実LLM/埋め込みを要する条件: {est.agent_conditions or '（なし）'}",
        f"  LLM 呼び出し: 約 {est.llm_calls:,} 回 / 埋め込み: 約 {est.embedding_calls:,} 回",
        f"  推定トークン: 入力 {est.est_prompt_tokens:,} + 出力 "
        f"{est.est_completion_tokens:,} + 埋め込み {est.est_embedding_tokens:,} "
        f"= 合計 約 {est.est_total_tokens:,}",
        f"  推定費用: ${est.usd_low} 〜 ${est.usd_high}（中央 ${est.usd_mid}）",
        "  前提（上書き可）: " + ", ".join(f"{k}={v}" for k, v in est.assumptions.items()),
    ]
    for note in est.notes:
        lines.append(f"  注: {note}")
    return lines
