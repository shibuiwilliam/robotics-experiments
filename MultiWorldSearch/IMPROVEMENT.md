# IMPROVEMENT.md — 未完課題

> **本書の位置づけ**
> **未完の修正・改善課題のみ**を保持する。完了済み課題・既知の制約・対応不要/不可能と判断した事項は、混乱を避けるため本書には残さない（経緯は `git log --follow IMPROVEMENT.md` と `REPORT.md` を参照）。

---

## 未完課題

> 2026-06-14 `make scenario-multi-seed-all`（G1–G6 完了後の検証走）で判明したコスト計測精度のギャップ。
> 詳細・数値は `REPORT.md` を参照。「結論の信頼性」ではなく「コスト台帳の精度」の課題。

### G7 — `estimated_cost_usd` が real/modeled 呼び出しを合算し実課金額と一致しない（中・コスト精度）
- **現状**: `CloudCostTracker.estimated_cost_usd` は real（実課金）と modeled（コストモデルのみ・非課金）の両方のトークンを合算する。mock 走では `llm_calls_real=0` なのに `estimated_cost_usd≈0.0016`＞0 となり（2026-06-14 multi-seed-all で確認）、**実課金ゼロの走が非ゼロの推定額を表示**する。ライブ走でも modeled ステップが混ざり headline 額が実課金を上回る。サマリは件数を `llm_calls_real`/`llm_calls_modeled` に分離している（M8）のに金額は未分離で、CLAUDE.md §5.1/§10 の「実クラウド spend を計測」と齟齬。
- **修正案**: real 呼び出し由来のトークンのみから `estimated_cost_usd_real` を算出して併記する（既存 `estimated_cost_usd` は「modeled込み推定」と明記）。埋め込みは live のみ real 計上（mock embed は modeled 扱い）。
- **受け入れ基準**: mock/replay 走の `cloud.estimated_cost_usd_real == 0`／ライブ走では real 呼び出しのトークンのみが `*_real` に反映されるテスト（mock・決定的）。

---

直近で消化した課題（2026-06-14, G1–G6・計測網羅性/コスト統制）— 詳細は `git log` と `REPORT.md`:

- **G1** ライブ走のコスト/トークン記録 ＋ `MWS_CONFIRM_LIVE_SPEND` spend ゲート（`mws/core/runguard.py`・`scenario run`）
- **G2/G5** 走行前バナー（cloud_mode / embedding / vector backend / seed・単一seed明示）
- **G3** 知覚税を全検索シナリオに拡張（S1/S3/S5/S7）・非該当は `perception_tax.applicable=false`＋理由（S2/S4/S6）
- **G4** ライブ E2E 受け入れ PASS/FAIL アサート（`mws/eval/acceptance.py`・`scenario run` が未達で非ゼロ終了）
- **G6** ベクトル検索レイテンシをバックエンド別バケットで計時（ES=`es_search` / in-memory・LanceDB=`local_ann`）

新たな課題が見つかったら、本セクションに次の形式で追記する:

```
### <ID> — <一言要約>（<深刻度>・<分類>）
- **現状**: 何が問題か（再現条件・影響範囲）
- **修正案**: どう直すか
- **受け入れ基準**: 何をもって完了とするか（テスト要件含む）
```
