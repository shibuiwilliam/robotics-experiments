# REPORT.md — MWS Scenario Validation Report

**Date**: 2026-06-12（P11: 監査三角形・リプレイ・重みスイープ・並列 act 完了後の本走）
**Command**: `make scenario-all`（live）＋ `mws eval reconcile`（**3点照合**）＋ `MWS_LLM_REPLAY` リプレイ実証 ＋ `eval tune-fusion`
**Mode**: `MWS_CLOUD_MODE=live` — 実 Gemini Embedding 2（768d・v2 非対称）＋ gemini-3.5-flash（ADK・**並列ステップ実行**）
**Result**: **7/7 scenarios PASS — エラー 0** · スイート **276 passed**(mock・両シェル決定的) · ruff clean · pyright 0 errors

**Run IDs**: S1 `maintenance_handoff-0-028cc291`（リプレイ `-384110f0`） · S2 `physical_record_reconciliation-0-471042cb` · S3 `collective_weak_signal-0-7459002e` · S4 `new_sku_rampup-0-fac2a544`（対照 `-631be9dc`） · S5 `incident_response-0-3d01dc5f` · S6 `counterfactual_safety-0-3f1b7798` · S7 `order_to_fulfillment-0-29151bc0` · 重みスイープ `tune_fusion-0-df849f70`

---

## 🧾 突き合わせ監査 — **3点照合**でゼロ差分（M19 完了）

```
$ mws eval reconcile --log <run.log> --runs <8 run IDs>
actual : embedding_requests=64  llm_calls_real=17  llm_calls_recorded=17
counted: embedding_requests=64  llm_calls_real=17  llm_calls_recorded=17
delta  : 0 / 0 / 0  →  reconciled: true
```

監査は**ログのマーカー行・metrics のカウンタ・llm_calls.jsonl の記録行**という独立生成された
3 つのソースを照合する。記録脚の欠落・過剰はどちらも非ゼロ差分で検出される（反証テスト付き）。
**レコーダはもう「信頼」ではなく「検証」の対象。**

## エグゼクティブサマリー（live・実測）

| 指標 | 値 |
|------|----|
| 完了 | 7/7（＋S4 対照・＋S1 リプレイ実証） |
| Gemini Embedding 2 | **64 リクエスト / 163 テキスト**（3点照合ゼロ差分） |
| 実 ADK LLM 呼 | **17**（全件実測トークン・全応答記録・**並列実行**） |
| act フェーズ | S1 **35.6→12.4 秒（-65%）**・S5 **35.1→9.9 秒（-72%）** |
| クラウドコスト | ~$0.0045 |

## P11 の成果（本走で受け入れ確認）

### M19 — 監査の第3照合脚（上記）

### REPLAY — 記録応答の決定的リプレイ（CLAUDE §5.1 完結）

`MWS_LLM_REPLAY=runs/<S1>/llm_calls.jsonl` で同一シナリオを再実行:
**ADK 呼 0・リプレイ 8/8・タスクゲート完全一致**（7/7 ステップ・転移 True・全 task metrics 同値）・
**再記録なし**（リプレイ走に llm_calls.jsonl は生成されない — 監査の三角形を汚さない）・
act 12.4 秒→**0.88 秒**。purpose 一致の順序許容マッチング（並列記録の完了順揺れに耐性）、
不一致・枯渇は明示エラー（実クラウドへのサイレントフォールバック禁止）。live の
record→replay 同一性テストも緑。リプレイ走は再現実験であり **reconcile の入力にはしない**（文書化済み）。

### 重みスイープ — 事前登録で「現行重み確定」＋ **R@5 上限の発見**

`eval tune-fusion`（run `tune_fusion-0-df849f70`）: シナリオ自身が所有するゴールデン
（S1/S3/S5/S7 × seeds 0–1 = 8 ペア）に対し、事前登録した12候補グリッドを掃引。

- **全候補で mean R@5 = 0.5029 と完全フラット**。relational_boost は R@10/nDCG を4ペアで悪化。
- **判定（事前登録規則）: 採用なし — 現行重み確定。**
- **より重要な発見**: R@5 には構造上限 5/n_relevant があり、**S1（n=10, max 0.50）・S3（n=13, max 0.385）・S7（n=9, max 0.556）は既に上限に張り付いている**。改善余地は S5 の1アトム（4/7→5/7）のみ。
  **「R@5 が低い」という従来の限界記述は大部分が計測上限のアーティファクト**であり、ランキング欠陥ではない（mean 上限 0.5386 に対し実測 0.5029 = 93%）。

### 並列ステップ実行（Backlog B — 読んでから実装）

コード読解で S1/S5 の各ステップが**同一の事前計算済み検索射影のみを消費**し相互依存しないことを
確認した上で、実 ADK 呼び出しを並列化（`MWS_LLM_MAX_CONCURRENCY` 既定4・結果は元順序で収集）。
**S1 act 35.6→12.4 秒・S5 35.1→9.9 秒**、エビデンスゲート/監査ログ/コスト計上/記録の決定的
順序は維持（usage は結果に同梱して属性付け — 共有状態レース排除、レコーダはロックで排他、
スレッド安全テスト付き）。mock/リプレイは逐次のまま（挙動不変）。

## シナリオ別結果（全ゲート・反証テスト緑）

| Sc | agent_mode | 主要結果 |
|----|-----------|----------|
| S1 | live(並列) | R@10 **0.90**・知覚税@10 0.10・relational_hits 1・**7/7**・転移 True・**リプレイで全ゲート再現** |
| S2 | modeled | 融合誤差 0.048・MC 融合優越・WMS 50→30（誤差0）・チケット起票 |
| S3 | modeled | lot_L 発見（margin 2・recall 1.0）・prefetch 3・H5 圧縮25.8%/保持1.00 |
| S4 | modeled | 転移ゲイン **1.00** |
| S5 | live(並列) | R@10 **1.00**・SQ自動発火・偵察・**8/8** |
| S6 | modeled | リスク 0.80/0.615 → AVOID×2 |
| S7 | live | R@10 **1.00**・E2E 1.00・鮮度ゲート→書き戻し→実ADK通知 |

レイテンシ: ANN ~0.04ms / embed p50 ~370–410ms / infer p50 3.7–3.9s（per-call、並列化で
wall-clock のみ短縮 — per-call 分布は不変）。perceive 全シナリオ 0.4–0.9s。

## 仮説検証（全9仮説＋E1）

H1–H9・E1 すべて従来どおり実験的結論あり（v2 live CI 含む、前 REPORT 参照）。
**新規**: 融合重み「現行確定」（事前登録）・R@5 構造上限の定量化。

## 制約（既知）

R@5 は構造上限の 93%（残余は S5 の1アトムのみ — 真の改善はコーパス/ラベル設計側）。
小規模コーパスで MRR 飽和。S6 ルールベース・VLA は力/軌道モデル・業務系スタブ。
A2A 未実装。EmbeddingGemma は HF gated（唯一の外部ブロック項目）。Batch API token_count None。

## 再現

```bash
make scenario-all 2> run.log                      # live 実走
uv run python -m mws.cli eval reconcile \
  --log run.log --runs <run IDs>                   # 3点照合（非0で失敗）
MWS_LLM_REPLAY=runs/<RUN_ID>/llm_calls.jsonl \
  MWS_CLOUD_MODE=live uv run python -m mws.cli \
  scenario run --name maintenance_handoff --seed 0 # 記録応答の決定的リプレイ（LLM呼0）
uv run python -m mws.cli eval tune-fusion          # 事前登録の重みスイープ（mock）
uv run pytest                                      # 276 tests（mock・決定的）
```
