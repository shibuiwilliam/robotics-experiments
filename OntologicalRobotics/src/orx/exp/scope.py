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


def scope_section(
    scopes: list[str], agent_status: str = "未実行（live計測・OPENAI_API_KEY要）"
) -> str:
    """レポート用の「計測射程」節を生成する。

    scopes: このレポートが含む計測カテゴリ（CEILING/ABLATION/AGENT）。
    """
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
    if AGENT in scopes:
        lines.append(
            "> **注意**: 本レポートのエージェント条件は実LLM/実埋め込みを要するため "
            f"{agent_status}。stub の数値は**ハーネス検証用で意味を持たない**"
            "（H1–H7 のエージェント検証は未実施）。"
        )
        lines.append("")
    return "\n".join(lines)


def stub_warning(metric: str = "正答率・p値") -> str:
    """stub 入力に対する統計値が無意味であることの警告（C2）。"""
    return (
        f"> **警告（C2）**: stub（決定的ダミー）LLM/埋め込みに対する{metric}は"
        "ランダム同等で**科学的に無意味**。H4/H6/H7 の根拠として引用しないこと。"
        "本計測は live（実モデル）でのみ行う。"
    )
