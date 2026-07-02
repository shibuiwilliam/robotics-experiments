"""計測射程（measurement scope）の明示分離（IMPROVEMENT.md C1）。

ORX の数値は3カテゴリに厳密分離して報告する。決定的上限や機構アブレーションを
仮説（H1–H7）の確認として提示しない。エージェント検証は実LLM/実埋め込みを要し、
オフライン（stub）では**未実行**である。
"""

from __future__ import annotations

CEILING = "ceiling"  # 表現上限: 決定的リファレンスソルバ／期待SPARQL-SQL（LLM不要）
ABLATION = "ablation"  # 機構アブレーション: anchoring/belief on↔off, OR-sym/OR-vec 等
AGENT = "agent"  # エージェント検証: 実LLM/実埋め込み（live・通常オフラインでは未実行）

_LABEL = {
    CEILING: "表現上限 (ceiling, 決定的・LLM不要)",
    ABLATION: "機構アブレーション (ablation)",
    AGENT: "エージェント検証 (agent, 実LLM/実埋め込み)",
}

_MEANS = {
    CEILING: "オントロジーが当該クエリを**表現・解決できる**ことの上限証明。"
    "「エージェントが使って勝つ」という仮説本体ではない。",
    ABLATION: "機構（同一性解決・信念調停・双対表現など）の有無による差。"
    "決定的経路での機構の寄与を示すが、エージェントレベルの仮説検証ではない。",
    AGENT: "実LLM/実埋め込みでの条件間比較。**仮説 H1–H7 の本体**。",
}


def agent_status_for(mode: str) -> str:
    """プロバイダモードからエージェント射程の実行状況ラベルを決める。

    live（openai）/ cache 再生では agent 射程は**実行済み**であり、stub では未実行。
    レポート見出しがデータ実体と矛盾しないようにするための単一の真実源。
    """
    if mode == "openai":
        return "実行済み（live・実LLM/実埋め込み）"
    if mode == "cache":
        return "実行済み（cache 再生・記録済みlive応答）"
    return "未実行（live計測・OPENAI_API_KEY要）"


def scope_section(
    scopes: list[str], agent_status: str = "未実行（live計測・OPENAI_API_KEY要）"
) -> str:
    """レポート用の「計測射程」節を生成する。

    scopes: このレポートが含む計測カテゴリ（CEILING/ABLATION/AGENT）。
    agent_status: エージェント射程の実行状況（`agent_status_for(mode)` で生成）。
    「実行済み」を含むときは live/cache、含まないときは未実行（stub）として注記を切り替える。
    """
    executed = "実行済み" in agent_status
    lines = [
        "## 計測射程 (Measurement scope)",
        "",
        "> 数値は以下のカテゴリに属する。**ceiling/ablation の数値は仮説確認ではない。**",
        "",
        "| カテゴリ | 本レポートでの該当 | 意味 |",
        "|----------|--------------------|------|",
    ]
    for cat in (CEILING, ABLATION, AGENT):
        present = "✓ 含む" if cat in scopes else "—"
        if cat == AGENT and AGENT in scopes:
            present = f"✓ **{agent_status}**"
        lines.append(f"| {_LABEL[cat]} | {present} | {_MEANS[cat]} |")
    lines.append("")
    if AGENT in scopes and executed:
        lines.append(
            "> **注記**: 本レポートのエージェント条件は実LLM/実埋め込みで "
            f"{agent_status}。数値は**仮説 H1–H7 のエージェントレベル検証の実測値**であり、"
            "ceiling（決定的上限）とは射程が異なる。temperature 0・全応答キャッシュで再現可能。"
        )
        lines.append("")
    elif AGENT in scopes:
        lines.append(
            "> **注意**: 本レポートのエージェント条件は実LLM/実埋め込みを要するため "
            f"{agent_status}。stub の数値は**ハーネス検証用で意味を持たない**"
            "（H1–H7 のエージェント検証は未実施）。"
        )
        lines.append("")
    return "\n".join(lines)


def loop_note(loop: str) -> str:
    """閉ループ（キネティック層）の反実仮想リプレイ非適用を明示する注記（IMPROVEMENT.md §3.1）。

    loop="closed": アクションが物理履歴を変えるため「1 記録→多重リプレイ」は使えず、
    条件ごとに独立ロールアウトし seed-paired 検定で比較する。loop="open"（従来）は注記不要。
    """
    if loop != "closed":
        return ""
    return (
        "> **計測ループ（closed）**: 本シナリオはエージェントのアクションが物理状態を変える"
        "**閉ループ**である。条件ごとに物理履歴が分岐するため反実仮想リプレイ（単一記録・多重"
        "リプレイ）は適用せず、各条件を独立ロールアウトし **seed-paired 検定**（McNemar/Wilcoxon、"
        "S1–S7 と同方式）で比較する。"
    )


def stub_warning(metric: str = "正答率・p値") -> str:
    """stub 入力に対する統計値が無意味であることの警告（C2）。"""
    return (
        f"> **警告（C2）**: stub（決定的ダミー）LLM/埋め込みに対する{metric}は"
        "ランダム同等で**科学的に無意味**。H4/H6/H7 の根拠として引用しないこと。"
        "本計測は live（実モデル）でのみ行う。"
    )
