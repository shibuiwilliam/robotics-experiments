# REPORT.md — MWS Scenario Validation Report

**Date**: 2026-06-12（P10: 再現性・記録・取込バッチ全面適用後の本走）
**Command**: `make scenario-all`（live）＋ `mws eval reconcile` ＋ `make scenario-multi-seed-live`（v2 CI）
**Mode**: `MWS_CLOUD_MODE=live` — 実 Gemini Embedding 2（768d・v2 非対称タスク指示）＋ gemini-3.5-flash（ADK）
**Manifest**: git `57fd3ee9050b`（**dirty=false・untracked=false** — MWS ツリーをコミット済み）· seed 0 · `gemini2-768-v2` · モデルID/バッチサイズ/依存バージョン記録
**Result**: **7/7 scenarios PASS — エラー 0** · スイート **263 passed**（mock・両シェル決定的） · ruff clean · pyright 0 errors

**Run IDs**: S1 `maintenance_handoff-0-e5afccd8` · S2 `physical_record_reconciliation-0-1f351c25` · S3 `collective_weak_signal-0-36f44b67` · S4 `new_sku_rampup-0-0634e9f9`（対照 `-b88d816b`） · S5 `incident_response-0-a90c3f39` · S6 `counterfactual_safety-0-1dbe32eb` · S7 `order_to_fulfillment-0-b48e9762` · multi-seed seeds 0–2（同日 v2）

---

## 🧾 突き合わせ監査（reconciliation）— ゼロ差分

```
$ mws eval reconcile --log <run.log> --runs <8 run IDs>
actual : embedding_requests=64   llm_calls_real=17   （ログの実リクエスト行数）
counted: embedding_requests=64   llm_calls_real=17   （metrics の計上値合計）
delta  : 0 / 0  →  reconciled: true
```

**metrics に計上されていない実クラウド呼び出しは存在しない。** M17（全シナリオの取込
バッチ化）により embedding リクエストは **123 → 64（-48%。P8 比 163→64 で -61%）**。
埋め込んだテキスト数は 163 のまま（`embedding_texts` で機械可読）。実 LLM 17/17 全件
が usage_metadata 実測トークン。

## エグゼクティブサマリー（live・実測）

| 指標 | 値 |
|------|----|
| 完了 | 7/7（＋S4 A/B 対照） |
| Gemini Embedding 2 | **64 リクエスト / 163 テキスト**（ゼロ差分） |
| 実 ADK LLM 呼 | **17**（S1:8 / S5:8 / S7:1）— **全件 llm_calls.jsonl に応答記録**（M16） |
| クラウドコスト | **$0.00454**（実測トークン基準） |
| live multi-seed | seeds 0–2 を **v2 スキームで再計測**（M18・下表） |

## P10 の成果（本走で受け入れ確認）

### M15 — manifest が単独で再現を特定

manifest に **モデルID**（gemini-embedding-2 / gemini-3.5-flash / 生徒モデル）・
**embedding_batch_size**・**git_dirty / git_untracked_tree**・**主要依存バージョン**
（mujoco/lancedb/duckdb/google-genai/sentence-transformers）を追加。**MWS ツリーを
コミット**（215 ファイル・シークレット/成果物ゼロをスキャン確認）し、本走 manifest は
`git_sha=57fd3ee9050b, git_dirty=false, git_untracked_tree=false` — **SHA が実コードを
一意に指す状態を達成**（従来は全ツリー未追跡で SHA が無意味だった）。

### M16 — 実 LLM 応答の常設記録

S1/S5/S7 の live 実行で `runs/<RUN_ID>/llm_calls.jsonl` に **17/17 レコード**
（agent・purpose・プロンプト・**応答本文**・実測トークン・レイテンシ）。S1 の計画
ステップ数が走ごとに変動する問題（7↔8）は、**今後は記録された計画本文から説明可能**。
mock 実行ではファイル自体が生成されない（遅延作成・挙動不変、テスト付き）。

### M17 — 取込バッチの全面適用（前後比較）

| perceive p50 | P9（per-atom） | P10（バッチ） |
|------|---------------:|-------------:|
| S3 | 5,568 ms | **445 ms (-92%)** |
| S5 | 3,656 ms | **408 ms (-89%)** |
| S7 | 4,828 ms | **811 ms (-83%)** |
| 全体リクエスト | 123 | **64 (-48%)** |

S2/S3/S4/S5/S6/S7 の inject/perceive ループを `_ingest_atoms`（バッチ埋め込み）経由に
統一。挿入順・standing query 発火・federated 個別取込は不変（mock スイート全緑・
ゴールデン回帰なし）。

### M18 — live multi-seed CI を v2 スキームで再計測（seeds 0–2）

| 指標 | v2 実測（mean [95%CI]） |
|------|------------------------|
| 知覚税@10（H3） | **0.100 [0.100, 0.100]** |
| H9 融合誤差 vs 最良単一 | **0.717 [0.687, 0.746] < 0.807 [0.764, 0.850]**（CI 分離） |
| H9 等重み対照 | 0.879 [0.858, 0.900]（単一にも劣る — 逆分散重みが必要条件） |
| 転移ゲイン（H1） | 1.000 [1.000, 1.000] |
| prefetch カバレッジ | 3.000 [3.000, 3.000] |
| R@10（S1/S3/S5/S7） | 0.900 / 0.692 / 1.000 / 1.000（全て CI 幅 0） |

**v1 時代の結論はすべて v2 でも保持**（検索品質はシード間で完全安定、H9 の CI 分離も再現）。

## シナリオ別結果（全ゲート・反証テスト緑）

| Sc | agent_mode | 主要結果 |
|----|-----------|----------|
| S1 | live | R@10 **0.90**・MRR 1.0・知覚税@10 0.10・relational_hits 1・**7/7 ステップ**（計画本文は llm_calls.jsonl に記録）・スキル転移 True |
| S2 | modeled | 融合誤差 0.048・MC 融合優越（CI 分離）・WMS 50→30 書き戻し・差異チケット |
| S3 | modeled | lot_L 発見（margin 2・recall 1.0）・QoR 1/1・prefetch 3・H5 圧縮25.8%/保持1.00 |
| S4 | modeled | 転移ゲイン **1.00**（4.04N≤5N vs 8.25N 超過） |
| S5 | live | R@10 **1.00**・SQ自動発火・偵察派遣・8/8（実ADK・応答記録済み） |
| S6 | modeled | 計算リスク 0.80/0.615 → AVOID×2・反実仮想アトム想起可能 |
| S7 | live | R@10 **1.00**・E2E 1.00・鮮度ゲート→WMS書き戻し→再発注→実ADK通知（応答記録済み） |

レイテンシ（per-call 実測）: ANN ~0.04ms / Gemini embed p50 ~370–410ms（リクエスト単位）/
Gemini infer p50 2.8–4.2s。取込フェーズは全シナリオで sub-second 〜 1.2s（M17）。

## 仮説検証（全9仮説＋E1・実験的結論あり）

H1 支持（1.000±0, v2 再計測）/ H2 支持 / H3 計測（0.100±0, v2）/ H4 支持 / H5 支持（25.8%・1.00）/
H6 支持 / H7 支持（27.4×・品質比1.06）/ H8 支持 / H9 支持（**v2 で CI 分離再確認**）/
E1 支持（接頭辞 +0.056 R@5・事前登録）。

## 制約（既知）

小規模コーパスで MRR 飽和。S6 ルールベース・VLA は力/軌道モデル・業務系スタブ。
A2A 未実装。EmbeddingGemma は HF gated。Batch API の per-item token_count は None
（`tokens_source` で推定と明示）。S2 の perceive はロボット観測の単発埋め込み3件を
含むため他より長い（7.2s — 単発リクエスト×3＋MuJoCo。バッチ対象外の by-design）。
LLM リプレイ（`MWS_LLM_REPLAY`）は未実装 — 記録（M16）のみ。応答の決定的固定再生は
今後の任意項目。

## 再現

```bash
make scenario-all 2> run.log                     # live 実走（ログ捕捉）
uv run python -m mws.cli eval reconcile \
  --log run.log --runs <run IDs>                  # ゼロ差分監査（非0で失敗）
cat runs/<RUN_ID>/llm_calls.jsonl                 # 実 LLM 応答の記録（live のみ生成）
cat runs/<RUN_ID>/manifest.json                   # 再現情報（SHA/dirty/モデル/依存）
MWS_CONFIRM_LIVE_SPEND=1 make scenario-multi-seed-live   # v2 CI（~$0.025 見積もり表示）
uv run pytest                                     # 263 tests（mock・決定的）
```
