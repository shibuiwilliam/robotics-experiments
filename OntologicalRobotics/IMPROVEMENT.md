# IMPROVEMENT.md — ORX 残課題

- 初版レビュー: 2026-06-13 / 最終整理: 2026-07-02
- 基準文書: `PROJECT.md`（H1–H8・§5.2不変条件・§7評価方法論・§9フェーズ・DoD）、`CLAUDE.md`。

> **本書の扱い**: 本書には**未解決の残課題のみ**を残す。完了済みの作業（2026-06-13 監査是正、
> R-1〜R-3・R-A/B/C、live 実測＋第2/第3モデル再現、キネティック層 K0/K1/K2＝S8・H8、R-INFRA、
> 2026-07-02 受入指摘 D-1〜D-4、R-K2＝既存条件で実質充足と判断し close 等）は本書から削除した。
> 詳細は **git 履歴**（例 `e0932b6`）・`docs/`（`LIVE_RESULTS.md`・`design/kinetic_layer.md`・
> `D_PLAN.md`）・`REPORT.md` を参照。

---

## 0. 現状（結論）

基盤は本物（捏造・リギング・真値漏洩なし・oracle 隔離 7/7 contracts kept・全 443 テスト green・$0 再現）。
**H1–H8 すべてで PROJECT.md §12 の成功基準を充足**——決定的反証 32/32 ✓、情報層 H1–H7 ＋ キネティック層 H8
（S8 アクション型）を agent 射程で **3 モデル live 実証**（nano/mini/full）。H8 のキネティック保証（安全・監査・
可逆）は**能力帯全域で model-independent**（S6 の規制ルーティング非単調と対照的・`docs/LIVE_RESULTS.md §5.2`）。
2026-07-02 にクリーンツリー（commit `e0932b6`・非 dirty）で `STRICT_PROVENANCE=1 make scenario-all` 受入済み。
詳細結果は `REPORT.md`。**必須の残課題は無し**（以下はいずれも任意・要コスト承認・結論不変）。

---

## 1. 残課題（任意・優先度 低・要コスト承認）

### R-OPT 統計精緻化

- **S5 live のシード数**: 現状 4 seed（S7 は 8）。質的結論は seed 非依存で堅牢だが、率の点推定 CI は広い。
  シード追加で CI を狭められる（S8 は決定的 closed＝seed 非依存・agent は 3 モデルで一貫のため精緻化不要）。
- **別ベンダー再現**: 現状 OpenAI スナップショット 3 点（nano/mini/full）。別ベンダー LLM で
  model-dependence 地図を拡張すれば外的妥当性をさらに補強できる（要モデル名＋コスト承認）。
