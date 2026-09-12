"""ダッシュボード（C13）：乖離台帳・ε の推移・将来違反の予兆・WHAT-IF 実行（poc_plan.md 5.6）。

`make dashboard`（`streamlit run src/gtwm/ui/app.py`）で起動する。

簡略化（このセッション、docs/status.md に明記）：
- 「ε の推移」は本来は同一エピソード内の時系列（分単位の移動窓）を指すが、現状の
  `gtwm ground run` は1エピソードにつきホライズンごとに1つの集計値しか出さない
  （`grounding/ground_run.py` 参照）。ここでは `runs/ground/*/epsilon.json` を
  エピソード横断で集め、各エピソードの実行を時系列の1点として扱う代替可視化とする。
- NL→WHAT-IF 変換（LLM 経由）はセッション09以降の範囲外（llm.md 参照）。ここでの
  WHAT-IF タブは付録Cの文法をそのまま入力するフォームのみ。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from gtwm.grounding.ledger import STATUSES, DiscrepancyLedger
from gtwm.kg.whatif.compiler import WhatIfCompileError
from gtwm.kg.whatif.engine import WhatIfUnsupportedVarError, run_whatif
from gtwm.kg.whatif.parser import WhatIfSyntaxError
from gtwm.utils.paths import repo_root

FEEDBACK_PATH = repo_root() / "runs" / "ui_feedback.jsonl"

st.set_page_config(page_title="gtwm ダッシュボード", layout="wide")
st.title("gtwm ダッシュボード")


def _ground_run_dirs() -> list[Path]:
    root = repo_root() / "runs" / "ground"
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "ledger.sqlite").exists())


def _append_feedback(record: dict) -> None:
    """訂正入力を再学習ラベル候補/オントロジー修正候補としてJSONLに追記する。

    ontology.md「LLM の扱い」/世界モデル.md「接地層の実装規則」の精神に合わせ、
    ここでは自動反映せず記録のみ行う（人が後で内容を確認して使う）。
    """
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


tab_ledger, tab_epsilon, tab_warnings, tab_whatif = st.tabs(
    ["乖離台帳", "ε の推移", "将来違反の予兆", "WHAT-IF 実行"]
)

# --- 1. 乖離台帳 -------------------------------------------------------------
with tab_ledger:
    st.header("乖離台帳")
    run_dirs = _ground_run_dirs()
    if not run_dirs:
        st.info("`gtwm ground run --episode <id>` を実行すると台帳が作成されます。")
    else:
        selected = st.selectbox(
            "エピソード", run_dirs, format_func=lambda p: p.name, key="ledger_episode"
        )
        ledger = DiscrepancyLedger(selected / "ledger.sqlite")
        rows = [row for status in STATUSES for row in ledger.list_by_status(status)]
        if not rows:
            st.info("このエピソードには乖離エントリがありません。")
        else:
            df = pd.DataFrame(rows)
            st.dataframe(df, width="stretch")

            st.subheader("確認・却下・訂正入力")
            open_ids = [str(r["discrepancy_id"]) for r in ledger.list_by_status("open")]
            confirmed_ids = [str(r["discrepancy_id"]) for r in ledger.list_by_status("confirmed")]

            if open_ids:
                target_id = st.selectbox("対象（open）", open_ids, key="ledger_open_target")
                resolver = st.text_input("判定者名", key="ledger_resolver_open")
                col1, col2 = st.columns(2)
                if col1.button("確認（confirmed）", disabled=not resolver):
                    ledger.confirm(target_id, resolver=resolver)
                    _append_feedback(
                        {
                            "type": "ledger_confirm",
                            "discrepancy_id": target_id,
                            "resolver": resolver,
                            "at": datetime.now(UTC).isoformat(),
                        }
                    )
                    st.rerun()
                if col2.button("却下（dismissed）", disabled=not resolver):
                    ledger.dismiss(target_id, resolver=resolver)
                    _append_feedback(
                        {
                            "type": "ledger_dismiss",
                            "discrepancy_id": target_id,
                            "resolver": resolver,
                            "at": datetime.now(UTC).isoformat(),
                        }
                    )
                    st.rerun()

            if confirmed_ids:
                st.divider()
                resolve_id = st.selectbox(
                    "対象（confirmed）", confirmed_ids, key="ledger_resolve_target"
                )
                resolve_resolver = st.text_input("判定者名", key="ledger_resolver_confirmed")
                correction = st.text_input(
                    "正しい値（訂正入力。WM再学習ラベル候補/オントロジー修正候補として記録）",
                    key="ledger_correction",
                )
                if st.button("解決（resolved）として記録", disabled=not resolve_resolver):
                    ledger.resolve(
                        resolve_id,
                        resolver=resolve_resolver,
                        resolution=correction or "(訂正値未入力)",
                    )
                    _append_feedback(
                        {
                            "type": "correction",
                            "discrepancy_id": resolve_id,
                            "resolver": resolve_resolver,
                            "correction": correction,
                            "at": datetime.now(UTC).isoformat(),
                        }
                    )
                    st.rerun()
        ledger.close()

# --- 2. ε の推移 --------------------------------------------------------------
with tab_epsilon:
    st.header("ε の推移（ホライズン別）")
    run_dirs = _ground_run_dirs()
    records = []
    for d in run_dirs:
        epsilon_path = d / "epsilon.json"
        if not epsilon_path.exists():
            continue
        for rec in json.loads(epsilon_path.read_text(encoding="utf-8")):
            records.append({"episode": d.name, **rec})
    if not records:
        st.info("`gtwm ground run` を実行すると ε の記録が作成されます。")
    else:
        df = pd.DataFrame(records)
        st.caption(
            "簡略化：本来は同一エピソード内の時系列だが、現状は `gtwm ground run` の"
            "実行（=エピソード）ごとに1点しか無いため、エピソードを時系列の代替軸として使う。"
        )
        for horizon_s, group in df.groupby("horizon_s"):
            st.subheader(f"ホライズン {horizon_s:.0f}秒")
            chart_df = group.set_index("episode")[["epsilon"]]
            st.line_chart(chart_df)
            st.caption(
                "管理限界（平常時の分布から設定。session 08 の EXP-04 で正式に導出）：未設定"
            )
            st.dataframe(
                group[["episode", "epsilon", "n_samples", "decomposition"]],
                width="stretch",
            )

# --- 3. 将来違反の予兆 ---------------------------------------------------------
with tab_warnings:
    st.header("将来違反の予兆")
    st.caption(
        "台帳の open エントリのうち、severity が high/medium のものを"
        "「予兆」として一覧する（`grounding/shield.ComplianceShield` によるリアルタイム"
        "先読みチェックは session 07 では WHAT-IF/計画呼び出し時のみ実行され、"
        "常時監視のバックグラウンドジョブはまだ無い。将来のセッションで接続する）。"
    )
    run_dirs = _ground_run_dirs()
    warning_rows = []
    for d in run_dirs:
        try:
            ledger = DiscrepancyLedger(d / "ledger.sqlite")
            for row in ledger.list_by_status("open"):
                if row.get("severity") in ("high", "medium"):
                    warning_rows.append({"episode": d.name, **row})
            ledger.close()
        except sqlite3.Error:
            continue
    if not warning_rows:
        st.success("現在、中〜高重要度の未対応乖離はありません。")
    else:
        st.dataframe(pd.DataFrame(warning_rows), width="stretch")

# --- 4. WHAT-IF 実行 -----------------------------------------------------------
with tab_whatif:
    st.header("WHAT-IF クエリ実行")
    st.caption("文法は poc_plan.md 付録C。例：`PREDICT ?queue_len AT +1s SAMPLES 10`")
    query_text = st.text_area(
        "クエリ",
        value="PREDICT ?queue_len AT +1s SAMPLES 10",
        height=120,
    )
    col_ep, col_set = st.columns(2)
    episode = col_ep.text_input("初期状態エピソードID", value="ep_0000_seed0")
    set_name = col_set.text_input("セット名", value="wm_smoke")

    if st.button("実行"):
        with st.spinner("ロールアウト中（smoke規模のWMを読み込み・学習しています）..."):
            try:
                result = run_whatif(query_text, episode, set_name)
            except (WhatIfSyntaxError, WhatIfCompileError, WhatIfUnsupportedVarError) as exc:
                st.error(str(exc))
            else:
                st.success(f"モデル版: {result.model_version}")
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "var": p.var,
                                "horizon_s": p.horizon_s,
                                "point_estimate": p.point_estimate,
                                "interval_low": p.interval_low,
                                "interval_high": p.interval_high,
                                "n_rollouts": p.n_rollouts,
                            }
                            for p in result.predictions
                        ]
                    ),
                    width="stretch",
                )
