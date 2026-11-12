---
paths:
  - "src/sbp/reasoning/**"
  - "src/sbp/actions/**"
  - "actions/**"
---
# 推論・サービス層 / アクション層のルール

## 信念更新（`sbp.reasoning.belief`）
- 実体単位の前向きフィルタ（半マルコフ）。因子は遷移（T + 滞在時間 + 計画遵守率）、プローブ尤度、記録尤度（信頼性モデル）。log 空間、numpy で実装する。
- 実体間の結合は `strong_relations`（部分−全体、同一 Situation）に限定した loopy BP を周期実行。周期・反復回数は Settings。
- 出力は `inferred` アサーション。来歴に三源の寄与率 `contrib: {observed, record, plan}` を必ず入れる（説明サービスが使う）。
- 記録尤度の計算には `AuditMask` を適用する（不変条件 6）。

## 照合（`sbp.reasoning.reconciliation`）
- 五類型の検出規則は `rules/kind1.py` … `kind5.py` に一つずつ。閾値 θ・持続 τ は Settings、拠点別に上書き可。
- `Discrepancy` の遷移は `Open → Triaged → Assigned → Resolved → Learned` と `Open → Suppressed` のみ。`transition()` 経由で行い、直接 status を書かない。
- 重複統合: 同一実体・同一類型・区間重なりは統合して証拠を追加する。抑制は「信頼性モデルで説明できる系統誤差」に限定し、理由を来歴に残す。
- 解決結果 → 学習信号は `learning.py`（物理正: 記録訂正提案 + 信頼性更新 / 記録正: 負例 `TimeBag` / 両誤: 概念候補 / 判定不能: 観測要求）。

## 予測・What-if（`prediction`, `whatif`）
- 予測は SOSA `Forecast` として `predicted` で格納。起点別の CRPS・被覆率を `metrics` テーブルに書く。
- What-if の結果は必ず `hypothetical` + `scenario`。主信念への昇格は `promote_scenario()` のみ。反実仮想には介入データ量に基づく妥当性スコアを併記する。

## 期待検証（`expectation`）
- 状態機械 `Created → Active → {Fulfilled, Violated, Undetermined} → Learned`。判定は conformal 被覆か Bayes 因子で、`method` を記録する。
- 記号側期待（T 由来）と潜在側期待（rollout 由来）を別々に検証し、どちらが外れたかを切り分ける。

## 概念発見・スケジューラ・説明（`discovery`, `scheduler`, `explain`）
- 発見の入力は (a) 低被覆・高密度クラスタ (b) 整合残差の持続 (c) 反復する不一致。LLM 提案は `sbp.llm` 経由で、人の採否を待つ（自動採用禁止）。
- スケジューラの効用は `Σ v(x)·EIG − cost`、貪欲 + 劣モジュラ近似。人への質問頻度上限は Settings。実行結果は `observed` で戻る。
- 説明は説明グラフ（記号側 + 潜在側 + 寄与率）を返す。LLM による文章化はその上に任意で載せる。

## 規則（`rules`）
- Datalog / ASP は clingo。規則ファイルは `ontology/process/*.lp` と `ontology/norms/*.lp`。Python 側で規則文字列を組み立てない（テンプレートは `.lp` 側に置く）。
- 確信度の伝播（min / 積 / noisy-OR）を規則ごとに宣言する。

## アクション（`sbp.actions`）
- ActionType は `actions/*.yaml`（設計書 §9.1 の形式）から読み、pydantic で検証する。
- 実行フロー: 前提検証（記号・物理・規範）→ 承認 → 実行（冪等 `command_id`）→ `ActionEvent` → `Expectation` 生成。順序を省略しない。
- `riskClass ≥ medium` は承認必須。人の上書きは来歴に残す。同一対象への短時間の反復はレート制限する。
- 補償アクション（`compensation`）が定義されていない ActionType は登録を拒否する。
