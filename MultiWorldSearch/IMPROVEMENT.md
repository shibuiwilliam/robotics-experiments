# IMPROVEMENT.md — 未完課題

> **本書の位置づけ**
> **未完の修正・改善課題のみ**を保持する。完了済み課題・既知の制約・対応不要/不可能と判断した事項は、混乱を避けるため本書には残さない（経緯は `git log --follow IMPROVEMENT.md` と `REPORT.md` を参照）。

---

## 未完課題

**なし。** 実装可能な登録課題はすべて消化済み・外部要因ブロックもゼロ。

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
