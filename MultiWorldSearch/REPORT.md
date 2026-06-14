# REPORT.md — MWS Scenario Validation Report

**Date**: 2026-06-12（P11 完了後の検証本走）
**Command**: `make scenario-all`（live）＋ `mws eval reconcile`（3点照合監査）
**Mode**: `MWS_CLOUD_MODE=live` — 実 Gemini Embedding 2（768d・v2 非対称タスク指示）＋ gemini-3.5-flash（ADK・並列ステップ実行）
**Manifest**: git `77b7ee3d6ec1` · seed 0 · `gemini2-768-v2` · batch=64 · モデルID/依存バージョン記録
**Result**: **7/7 scenarios PASS — エラー 0** · スイート **276 passed**（mock・両シェル決定的） · ruff clean · pyright 0 errors

**Run IDs**: S1 `maintenance_handoff-0-7c4df7b7` · S2 `physical_record_reconciliation-0-1eb9411b` · S3 `collective_weak_signal-0-ce075d03` · S4 `new_sku_rampup-0-10221ac2`（対照 `-9dfd0a9d`） · S5 `incident_response-0-40594ac4` · S6 `counterfactual_safety-0-2efcb857` · S7 `order_to_fulfillment-0-786f61e9`

> **manifest の正直さについての注記**: 本走の manifest は `git_dirty: true` を記録している。
> 原因は実走時点で REPORT.md（本ファイル）が作業ツリーから削除されていたこと——コードは
> コミット `77b7ee3` と完全一致であり、計測には影響しない。dirty フラグが「ドキュメント
> 1 ファイルの削除」まで正確に拾った事例（どのパスが dirty かは manifest からは読めない
> — IMPROVEMENT M20 として登録）。

---

## 🧾 突き合わせ監査 — 3点照合ゼロ差分

```
$ mws eval reconcile --log <run.log> --runs <8 run IDs>
actual : embedding_requests=64  llm_calls_real=18  llm_calls_recorded=18
counted: embedding_requests=64  llm_calls_real=18  llm_calls_recorded=18
delta  : 0 / 0 / 0  →  reconciled: true
```

ログのマーカー行・metrics カウンタ・llm_calls.jsonl 記録行という**独立生成された3ソース**が
全件一致。metrics に計上されていないクラウド呼び出しも、記録されていない応答も存在しない。

## エグゼクティブサマリー（live・実測）

| 指標 | 値 |
|------|----|
| 完了 | 7/7（＋S4 A/B 対照） |
| Gemini Embedding 2 | **64 リクエスト / 163 テキスト**（3点照合済み） |
| 実 ADK LLM 呼 | **18**（S1:9 / S5:8 / S7:1 — 全件実測トークン・全応答記録） |
| クラウドコスト | **$0.00457** |
| 帯域（実/仮想） | 912 KB / 166 KB |
| act（並列実行） | S1 16.8s（9呼）/ S5 11.0s（8呼）— 逐次時代の ~35s から半減以下を維持 |
| perceive | 全シナリオ **0.43–0.99 秒**（バッチ取込） |

> **記録が揺らぎを即答した（M16/M19 の実証）**: 実 LLM 呼が前走 17→今走 **18**。
> `llm_calls.jsonl` の `purpose: plan` レコードを読むと、今走の LLM は **8 ステップ計画**
> （"Isolate and lock-out pump_07" 〜）を生成しており（前走は 7）、+1 呼はその分。
> エビデンスゲートは 8/8 通過。検証に要したのは記録ファイルを 1 つ読むことだけ。

## シナリオ別結果（全ゲート・反証テスト緑）

| Sc | agent_mode | 主要結果 |
|----|-----------|----------|
| S1 | live(並列) | R@10 **0.90**・MRR 1.0・知覚税@10 **0.10**・relational_hits 1・**8/8 ステップ**（計画記録済み）・スキル転移 True（信頼度 0.88） |
| S2 | modeled | 融合誤差 0.048・MC 融合優越（等重み対照は敗北）・WMS **50→30 書き戻し**（誤差0）・差異チケット |
| S3 | modeled | **lot_L 発見**（margin 2・純度 0.6・recall 1.0）・QoR・prefetch カバレッジ 3・SQ 登録 |
| S4 | modeled | 転移ゲイン **1.00**（実演あり 1.0 / なし 0.0） |
| S5 | live(並列) | R@10 **1.00**・SQ 自動発火・偵察派遣・**8/8**（実 ADK・応答記録） |
| S6 | modeled | 計算リスク 0.80/0.615 → **AVOID×2**・反実仮想アトム想起可能 |
| S7 | live | R@10 **1.00**・E2E **1.00**・鮮度ゲート→WMS 書き戻し→再発注→実 ADK 通知 |

## レイテンシ分解（per-call 実測）

| バケット | p50 | 備考 |
|----------|----:|------|
| local ANN | ~0.04 ms | |
| Gemini embed | ~370–410 ms | リクエスト単位（バッチ込み） |
| Gemini infer | ~3.7–3.9 s | per-call は不変、並列実行で wall-clock のみ短縮 |
| perceive | 0.43–0.99 s | 全シナリオでバッチ取込が持続 |

## 検証体制（P11 までに常設化した保証）

- **3点照合監査**（`eval reconcile`）— 本走ゼロ差分。記録脚の欠落/過剰も検出（反証テスト付き）。
- **応答記録＋決定的リプレイ** — 18/18 記録。`MWS_LLM_REPLAY` で LLM 呼ゼロの再現実行が可能（前走で S1 全ゲート同一を実証済み）。
- **完全 manifest** — SHA・dirty/untracked・モデルID・バッチサイズ・依存バージョン。本走は dirty=true を**正直に**記録（原因は本ファイルの削除、コードは一致）。
- **事前登録実験** — E1 接頭辞 A/B（+0.056）・融合重みスイープ（現行確定・R@5 構造上限 93% の発見）。
  - 注: H7（教師/生徒二層）は P12 で撤回。埋め込みは `gemini-embedding-2` 単一に統一済み。
- **live multi-seed CI（v2）** — 知覚税 0.100±0・H9 CI 分離・転移 1.000±0。

## 仮説検証（全9仮説＋E1・実験的結論あり）

H1 支持 / H2 支持 / H3 計測（0.100±0）/ H4 支持 / H5 支持（25.8%・1.00）/ H6 支持 /
~~H7~~（P12 で撤回: 埋め込みを gemini-embedding-2 単一に統一）/ H8 支持 / H9 支持（CI 分離）/ E1 支持（+0.056）。

## 取れていないログ/データ（IMPROVEMENT に登録）

1. **M20（Low）** — manifest は `git_dirty: true/false` を記録するが、**どのパスが dirty かは記録しない**。本走の dirty 原因（REPORT.md 削除）の特定には manifest の外（`git status`）が必要だった。dirty パスの先頭 N 件を `git_dirty_paths` として記録すれば manifest 単体で閉じる。

## 制約（既知）

R@5 は構造上限（5/n_relevant）の 93% — 重みでは動かない（事前登録スイープで確定）。
小規模コーパスで MRR 飽和。S6 ルールベース・VLA は力/軌道モデル・業務系スタブ。
A2A 未実装。埋め込みは gemini-embedding-2 単一（P12: 生徒層/H7 撤去）。Batch API token_count None。

## 再現

```bash
make scenario-all 2> run.log                      # live 実走（ログ捕捉）
uv run python -m mws.cli eval reconcile \
  --log run.log --runs <run IDs>                   # 3点照合（非0で失敗）
cat runs/<RUN_ID>/llm_calls.jsonl                  # 実 LLM 応答の記録
MWS_LLM_REPLAY=runs/<RUN_ID>/llm_calls.jsonl \
  MWS_CLOUD_MODE=live uv run python -m mws.cli \
  scenario run --name maintenance_handoff --seed 0 # 決定的リプレイ（LLM 呼 0）
uv run pytest                                      # 276 tests（mock・決定的）
```
