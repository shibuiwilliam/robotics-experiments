# REPORT.md — MWS Scenario Validation Report

**Date**: 2026-06-11（P9 完了後の検証本走）
**Command**: `make scenario-all`（live）＋ `mws eval reconcile`（ゼロ差分監査）
**Mode**: `MWS_CLOUD_MODE=live` — 実 Gemini Embedding 2（768d・**v2 非対称タスク指示スキーム**）＋ gemini-3.5-flash（ADK）
**Manifest**: git `48d2026c72e3` · seed 0 · `gemini2-768-v2` · Python 3.13.6
**Result**: **7/7 scenarios PASS — エラー 0** · スイート **255 passed**（mock・両シェル決定的） · ruff clean · pyright 0 errors

**Run IDs**: S1 `maintenance_handoff-0-7886b620` · S2 `physical_record_reconciliation-0-ebf9c22f` · S3 `collective_weak_signal-0-c629941a` · S4 `new_sku_rampup-0-4eb66aad`（対照 `-642318ec`） · S5 `incident_response-0-de1100d7` · S6 `counterfactual_safety-0-bb2e1aad` · S7 `order_to_fulfillment-0-329f69bc`

---

## 🧾 突き合わせ監査（reconciliation）— 本レポートの信頼性の根拠

```
$ mws eval reconcile --log <run.log> --runs <8 run IDs>
actual : embedding_requests=123  llm_calls_real=18   （ログの実リクエスト行数）
counted: embedding_requests=123  llm_calls_real=18   （metrics の計上値合計）
delta  : 0 / 0  →  reconciled: true
```

**metrics に計上されていない実クラウド呼び出しは存在しない。** 監査単位は E2 以降
「リクエスト」（バッチ1回=ログ1行=計上1）。テキスト数は `embedding_texts`（163）で別途
機械可読。実 LLM 18/18 は**全件が usage_metadata 実測トークン**。

## エグゼクティブサマリー（live・実測）

| 指標 | 値 |
|------|----|
| 完了 | 7/7（＋S4 A/B 対照） |
| Gemini Embedding 2 | **123 リクエスト / 163 テキスト**（ゼロ差分・バッチ化済み） |
| 実 ADK LLM 呼 | **18**（S1:9 / S5:8 / S7:1 — 全件実測トークン）＋コストモデル計上 11 |
| クラウドコスト | **$0.00456**（実測トークン基準） |
| 帯域（実/仮想） | 908 KB / 166 KB |
| 監査ログ | 100 entries（8 runs） |

> **live の自然な揺らぎ（正直な注記）**: S1 の実 LLM 呼が前走 8→今走 **9**。原因は
> LLM が今回 **8 ステップの計画**を生成したため（前走7。計画1呼＋ステップ8呼）。
> ステップ完了は従来どおり検索エビデンスでゲートされ **8/8 完了**。計画長は live の
> LLM 非決定性に由来し、応答が記録されていないため事後再現できない（→ IMPROVEMENT M16）。

## シナリオ別結果（全ゲート・反証テスト緑）

| Sc | agent_mode | 主要結果 |
|----|-----------|----------|
| S1 | live | R@10 **0.90**・MRR 1.0・nDCG@10 0.931・**知覚税@10 0.10**・relational_hits 1・**8/8 ステップ**（エビデンスゲート）・スキル転移 True（共有VLA policy, 信頼度0.88） |
| S2 | modeled | 融合 **30.048**（誤差0.048・検索取得2観測）・最良単一誤差 0.126・MC 融合優越・WMS **50→30 書き戻し**（誤差0）・差異チケット起票 |
| S3 | modeled | **lot_L 発見**（margin 2・純度0.6・recall 1.0）・QoR 1/1・**prefetch カバレッジ3**・federation 3ストア横断・standing query 登録 |
| S4 | modeled | 転移ゲイン **1.00**（実演あり 4.04N≤5N 成功率1.0 / 実演なし 8.25N 超過 0.0） |
| S5 | live | R@10 **1.00**・SQ自動発火・偵察派遣・SDS/出口/名簿を計画が消費・**8/8**（実ADK 8呼・実測トークン） |
| S6 | modeled | 計算リスク 0.80/0.615 → **AVOID×2**・反実仮想アトム 2 件が想起可能 |
| S7 | live | R@10 **1.00**・E2E **1.00**・鮮度ゲート幽霊在庫検出→WMS 書き戻し→再発注→**実ADK 通知文生成**→ERP クローズ |

## レイテンシ分解（per-call 実測・CLAUDE.md §10）

| バケット | p50 | p95 | n |
|----------|----:|----:|--:|
| local ANN（S1） | 0.04 ms | 0.1 ms | 9 |
| Gemini embed（S1/S3/S5/S7） | 371–413 ms | 401–579 ms | 8–22/走 |
| Gemini infer（S1） | 3.40 s | 6.75 s | 9 |
| Gemini infer（S5） | 4.19 s | 5.53 s | 8 |
| Gemini infer（S7） | 2.77 s | =p50 | 1 |

E2 バッチ取込の効果は持続: S1 seed_memory **0.82s** / perceive **0.83s**（バッチ化前は
2.8s / 6.7s）。S3/S5/S7 の perceive は 3.7–5.6s と高止まり — **per-atom 取込ループが
バッチ経路を通っていない**ため（→ IMPROVEMENT M17）。

## P9 受け入れ済みの基盤（本走の前提・同日検証）

- **E1 非対称タスク指示**: 事前登録 A/B 支持（Recall@5 0.944→**1.000**、`e1_ab-0-dd5df34a`）。本走の検索品質・知覚税は v2 スキームでも全数値維持。
- **E2 バッチ埋め込み**: リクエスト 163→123（-25%）。バッチ⇔単発の live 等価（cos>0.999）。
- **E3 Batch API 索引**: 実ジョブ完走（`index_build_batch-0-d9c6e88a`・LanceDB/DuckDB・同期呼0・再開可能）。
- **E4**: 型付き config・自動正規化（L2ノルム≈1.0）live 確認。
- **監査の実績**: P9 検証中に実リーク9件（空間ドリフトによる未計上再埋め込み）を検出→修正→ゼロ差分回復。本走もゼロ差分。

## 仮説検証（全9仮説＋E1・実験的結論あり）

H1 支持（転移 1.0）/ H2 支持（3ストア横断・直接通信なし）/ H3 計測（税 0.100）/
H4 支持（鮮度上書き S2/S7）/ H5 支持・計測（圧縮25.8%・保持1.00）/ H6 支持（偵察・AVOID）/
H7 支持（27.4×・品質比1.06、MiniLM代替）/ H8 支持（5射影）/ H9 支持（MC 融合優越・等重み対照敗北）/
**E1 支持**（接頭辞 +0.056 R@5・事前登録）。

## 取れていないログ/データ（IMPROVEMENT に登録）

1. **M15** — manifest にモデル ID・バッチサイズ・**git dirty/untracked 状態**が無い（MWS ツリー全体が未追跡のため現状の `git_sha` ではコード状態を再現できない。CLAUDE §9 不適合）。
2. **M16** — 実 ADK の**応答本文が未記録**: S1 計画長が走毎に変動（7→8 ステップ）するが、応答を保存していないため原因の事後検証・固定比較ができない（CLAUDE §5.1「応答をキャッシュ／記録」不適合）。
3. **M17** — S3/S5/S7 等の per-atom 取込ループが E2 バッチ経路を迂回（perceive 3.7–5.6s に滞留）。
4. **M18** — live multi-seed CI が v1 時代の値のまま（v2 空間で未再計測。再実行コスト ~$0.014/3シード）。

## 制約（既知）

小規模コーパスで MRR 飽和。S6 ルールベース・VLA は力/軌道モデル・業務系スタブ。
A2A 未実装。EmbeddingGemma は HF gated。Batch API の per-item token_count は None
（コストは推定・`tokens_source` ラベル明示）。フェーズ単体は1サンプルで p95=p50。

## 再現

```bash
make scenario-all 2> run.log                    # live 実走（ログ捕捉）
uv run python -m mws.cli eval reconcile \
  --log run.log --runs <カンマ区切り run IDs>    # ゼロ差分監査（非0で失敗）
uv run python -m mws.cli eval e1-ab              # E1 A/B（~52リクエスト）
uv run python -m mws.cli index build --batch-api # Batch API 索引（live・再開可能）
uv run pytest                                    # 255 tests（mock・決定的）
```
