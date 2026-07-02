# IMPROVEMENT.md — ORX 残課題

- 初版レビュー: 2026-06-13 / 最終整理: 2026-07-02
- 基準文書: `PROJECT.md`（H1–H8・§5.2不変条件・§7評価方法論・§9フェーズ・DoD）、`CLAUDE.md`。

> **本書の扱い**: 本書には**未解決の残課題のみ**を残す。完了済みの作業（2026-06-13 監査の C/H/M/L、
> R-1〜R-3・R-A/B/C 装置、第2モデル再現、S6 能力境界、**キネティック層 K0/K1**＝アクション層・S8、
> **R-INFRA**＝`make report`、**R-K1**＝S8 agent 射程 live 実測、**H8 を PROJECT.md 登録**、
> **recovery_rate 実装**、**R-OPT S8 3 モデル能力境界**（nano/mini/full・H8 は能力帯全域で model-independent）、
> **D-1〜D-4**＝2026-07-02 受入指摘のログ・データ収集是正（per-run structlog／コンフィグ＋エピソード
> アーカイブ／legacy run 除外／ORXツリー限定 dirty 判定＋クリーンツリー受入・計画=`docs/D_PLAN.md`）
> 等）は本書から削除した。詳細は **git 履歴**・`docs/`（`LIVE_RESULTS.md §5.2`・`design/kinetic_layer.md`・
> `D_PLAN.md`）・`REPORT.md` を参照。

---

## 0. 現状（結論）

基盤は本物（捏造・リギング・真値漏洩なし・oracle 隔離 7/7 contracts kept・全テスト 433 green・$0 再現）。
**H1–H8 すべてで PROJECT.md §12 の成功基準を充足**——決定的反証 32/32 ✓、情報層 H1–H7 ＋ キネティック層 H8
（S8 アクション型）を agent 射程で **3 モデル live 実証**（nano/mini/full）。H8 のキネティック保証（安全・監査・
可逆）は**能力帯全域で model-independent**（S6 の規制ルーティング非単調と対照的・`docs/LIVE_RESULTS.md §5.2`）。
詳細結果は `REPORT.md`。**必須の残課題は無し**（以下はいずれも任意・要コスト承認・結論不変）。
2026-07-02 の `make scenario-all` 受入実行（REPORT.md §9）で決定的射程の全数再現・反証 32/32・
全テスト green を再確認。同実行で見つかったログ・データ収集の指摘 **D-1〜D-4 は同日中に是正済み**
（計画と設計判断は `docs/D_PLAN.md`）: per-run structlog（`log.jsonl`）＋コンフィグ自己完結アーカイブ＋
エピソード入力永続化（s2/s4/s6/s7）＋記録方式メタデータ（`results.json.recording`）＋legacy run の
集計除外＋ORX ツリー限定の dirty 判定＋`make git-provenance-check` ガード＋クリーンツリーでの受入再実行。

---

## 1. 残課題（任意・優先度 低）

### R-K2 既存シナリオの act 化（S3/S5/S7）— 趣旨は実質的に充足済み

> **状況**: R-K2 の趣旨「アクションゲートが null/mixed を構造修復する」は**既存条件で実質的に実証済み**:
> S7 `OR-full-llm-guarded`（ツール側ゲートが mixed を修復・誤配送0）／S5 `s5_domain_norm`（ドメイン固有
> 規制で規範オントロジーの価値が再び立つ）／S8（能力・規範・所有の全送信基準を実証）。**新規 `*-act` 条件の
> 追加は既存証拠と重複**するため、CLAUDE.md「仮説に紐づかない/重複機能は作らない」に従い優先度は低い。
> 純粋な上積みは S5/S7 を**物理移動まで含む完全閉ループ**に作り替える大規模リファクタのみで、複合 H8 の
> 限界価値に対しリスクが見合わない。実施するなら 1 シナリオずつ非破壊（新条件追加）で。

### R-OPT 統計精緻化

- **S5/S7 live のシード数**: S5 は 4 seed・S7 は 8 seed（2026-07-02 受入検証で results.json を実測し訂正。
  旧記載「いずれも 8 seed」は誤り）。質的結論は seed 非依存で堅牢だが率の点推定 CI は広い。
  （S8 は決定的 closed＝seed 非依存・agent は 3 モデルで一貫のため精緻化不要。）
- **別ベンダー再現**: 現状 OpenAI スナップショット 3 点（nano/mini/full）。別ベンダー LLM で
  model-dependence 地図を拡張すれば外的妥当性をさらに補強できる（要モデル名＋コスト承認）。
