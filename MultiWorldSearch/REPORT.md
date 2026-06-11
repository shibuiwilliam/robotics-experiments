# REPORT.md — MWS Scenario Validation Report

**Date**: 2026-06-12（P10 完了後の検証本走）
**Command**: `make scenario-all`（live）＋ `mws eval reconcile`（ゼロ差分監査）
**Mode**: `MWS_CLOUD_MODE=live` — 実 Gemini Embedding 2（768d・v2 非対称タスク指示）＋ gemini-3.5-flash（ADK）
**Manifest**: git `9e895f0b501e` · **git_dirty=false / git_untracked_tree=false** · seed 0 · `gemini2-768-v2` · batch=64 · モデルID/依存バージョン記録済み
**Result**: **7/7 scenarios PASS — エラー 0** · スイート **263 passed**（mock・両シェル決定的） · ruff clean · pyright 0 errors

**Run IDs**: S1 `maintenance_handoff-0-b123bd4d` · S2 `physical_record_reconciliation-0-61cb572b` · S3 `collective_weak_signal-0-029da7ef` · S4 `new_sku_rampup-0-10730d72`（対照 `-9847fffb`） · S5 `incident_response-0-8a1b41c9` · S6 `counterfactual_safety-0-fea8fb81` · S7 `order_to_fulfillment-0-f0188d65`

---

## 🧾 突き合わせ監査（reconciliation）— ゼロ差分

```
$ mws eval reconcile --log <run.log> --runs <8 run IDs>
actual : embedding_requests=64   llm_calls_real=17   （ログの実リクエスト行数）
counted: embedding_requests=64   llm_calls_real=17   （metrics の計上値合計）
delta  : 0 / 0  →  reconciled: true
```

**metrics に計上されていない実クラウド呼び出しは存在しない。** 実 LLM 17/17 全件が
usage_metadata 実測トークンで、**全応答が `llm_calls.jsonl` に記録済み**（S1:8 / S5:8 / S7:1）。

## エグゼクティブサマリー（live・実測）

| 指標 | 値 |
|------|----|
| 完了 | 7/7（＋S4 A/B 対照） |
| Gemini Embedding 2 | **64 リクエスト / 163 テキスト**（バッチ化・ゼロ差分） |
| 実 ADK LLM 呼 | **17**（全件実測トークン・全応答記録） |
| クラウドコスト | **$0.00448** |
| 帯域（実/仮想） | 890 KB / 166 KB |
| 取込レイテンシ（perceive p50） | **全シナリオ 0.38–0.94 秒**（バッチ取込） |
| 再現性 | manifest が SHA（dirty=false）・モデルID・バッチサイズ・依存バージョンを記録 |

> **再現性の実証（M16 の成果）**: 今走の S1 は **7 ステップ計画**（前走は 8）。従来は
> 説明不能だったこの live 揺らぎが、今回は `runs/<S1>/llm_calls.jsonl` の `purpose: plan`
> レコードに**計画本文がそのまま記録**されており（"Isolate and lock-out pump_07" 〜 の
> 7 項目 JSON）、変動を成果物から逐語的に検証できた。

## シナリオ別結果（全ゲート・反証テスト緑）

| Sc | agent_mode | 主要結果 |
|----|-----------|----------|
| S1 | live | R@10 **0.90**・MRR 1.0・知覚税@10 **0.10**・relational_hits 1・**7/7 ステップ**（計画は記録済み）・スキル転移 True（信頼度 0.88） |
| S2 | modeled | 融合 **30.048**（誤差 0.048・検索取得 2 観測）・WMS **50→30 書き戻し**（誤差 0）・差異チケット起票 |
| S3 | modeled | **lot_L 発見**（margin 2・純度 0.6・recall 1.0）・QoR・prefetch カバレッジ 3・standing query 登録 |
| S4 | modeled | 転移ゲイン **1.00**（実演あり成功率 1.0 / なし 0.0） |
| S5 | live | R@10 **1.00**・SQ 自動発火・偵察派遣・SDS/出口/名簿消費・**8/8**（実 ADK・応答記録） |
| S6 | modeled | 計算リスク 0.80/0.615 → **AVOID×2**・反実仮想アトム 2 件想起可能 |
| S7 | live | R@10 **1.00**・E2E **1.00**・鮮度ゲート幽霊在庫→WMS 書き戻し→再発注→実 ADK 通知（応答記録） |

## レイテンシ分解（per-call 実測）

| バケット | p50 | 備考 |
|----------|----:|------|
| local ANN | ~0.04 ms | |
| Gemini embed | ~370–410 ms | リクエスト単位（バッチ含む） |
| Gemini infer | 2.8–4.2 s | S1 n=8 / S5 n=8 / S7 n=1 |
| perceive（全シナリオ） | **0.38–0.94 s** | M17 バッチ取込の効果が全シナリオで持続 |

前走で観測された S2 perceive の 7.2 秒は再現せず（今走 0.56 秒）— 一過性のネットワーク
遅延と確定。コードに帰着する滞留は残っていない。

## live multi-seed CI（v2 スキーム・seeds 0–2、前日計測）

知覚税@10 **0.100 [0.100, 0.100]** / H9 融合 **0.717 [0.687, 0.746] < 0.807 [0.764, 0.850]**
（等重み対照 0.879）/ 転移 1.000±0 / prefetch 3.0±0 / R@10 全 CI 幅 0。

## 仮説検証（全9仮説＋E1・実験的結論あり）

H1 支持（1.000±0）/ H2 支持（3 ストア横断・直接通信なし）/ H3 計測（0.100±0）/
H4 支持（鮮度上書き S2/S7）/ H5 支持・計測（圧縮 25.8%・保持 1.00）/ H6 支持 /
H7 支持（27.4×・品質比 1.06、MiniLM 代替）/ H8 支持（5 射影）/ H9 支持（CI 分離）/
E1 支持（接頭辞 +0.056 R@5・事前登録）。

## 取れていないログ/データ（IMPROVEMENT に登録）

1. **M19** — **応答記録そのものが監査対象外**: `mws eval reconcile` はログ↔metrics の2点照合だが、`llm_calls.jsonl` の記録件数は照合されない。レコーダが故障しても（例外を握る変更・配線漏れ）ゼロ差分のまま気づけない。監査に第3の照合脚（`llm_calls_real == llm_calls.jsonl 行数`）を追加すべき。
2. **（既知・任意）** `MWS_LLM_REPLAY`（記録応答の決定的リプレイ）は未実装 — 記録のみで M16 受け入れは満たすが、応答固定の A/B 比較には将来必要。

## 制約（既知）

小規模コーパスで MRR 飽和（示唆的なのは Recall@5 = 0.39–0.57）。S6 ルールベース・
VLA は力/軌道モデル・業務系スタブ。A2A 未実装。EmbeddingGemma は HF gated。
Batch API の per-item token_count は None（`tokens_source` 明示）。フェーズ単体は
1 サンプル（p95=p50、multi-seed 側で分布化）。

## 再現

```bash
make scenario-all 2> run.log                     # live 実走（ログ捕捉）
uv run python -m mws.cli eval reconcile \
  --log run.log --runs <run IDs>                  # ゼロ差分監査（非0で失敗）
cat runs/<RUN_ID>/llm_calls.jsonl                 # 実 LLM 応答の記録（live のみ）
cat runs/<RUN_ID>/manifest.json                   # SHA/dirty/モデル/依存（再現情報）
MWS_CONFIRM_LIVE_SPEND=1 make scenario-multi-seed-live   # live CI（見積もり表示付き）
uv run pytest                                     # 263 tests（mock・決定的）
```
