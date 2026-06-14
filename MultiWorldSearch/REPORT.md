# REPORT.md — MWS Multi-Seed Validation Report

**Date**: 2026-06-14（`make scenario-multi-seed-all`・ES バックエンド既定化後）
**Command**: `make scenario-multi-seed-all`（環境構築 → ES クラスター初期化 → 受け入れテスト → seeds 0–3 マルチシード）
**Mode**: `MWS_CLOUD_MODE=mock`（埋め込みは MockEmbedder・クラウド非接触）／**vector backend = Elasticsearch**（docker-compose, dense_vector+kNN）
**Result**: **受け入れテスト 70 passed**（in-memory・決定的）＋ **マルチシード seeds 0–3 × 7 シナリオ完走**（ES 上で 32 run・48 ベクトルストア）・実行後 ES インデックス残 **0**

> 本走は P14（シナリオ実行の ES バックエンド既定化）の検証。`make scenario-multi-seed-all`
> が「環境構築（`uv sync` ＋ `--extra es`）→ クリーンな ES クラスター初期化（`es-reset`）→
> 受け入れテスト（mock・in-memory）→ マルチシード集計（ES バックエンド）」を一括実行する。

---

## 実行サマリー

| 段 | 内容 | 結果 |
|----|------|------|
| 環境構築 | `setup`（dev+live+es 同期） | OK |
| ES 初期化 | `es-reset`（volume wipe → up --wait） | Healthy |
| [1/2] 受け入れテスト | `pytest tests/scenarios/`（mock・in-memory・決定的） | **70 passed** |
| [2/2] マルチシード | seeds 0–3 × 7 シナリオを **ES バックエンド**で実行 | 32 run 完走 |
| 後始末 | 各ストアの ES インデックスを teardown で drop | **残 0** |
| コスト | mock（クラウド呼び出しなし） | **$0** |

## バックエンド検証（本走の主眼）

**Elasticsearch バックエンドは in-memory（厳密総当たり）と同一の検索結果を返した。**
同一条件（mock 埋め込み・seeds 0–3）で vector backend だけを切り替えて比較:

| 指標（S1） | Elasticsearch | in-memory |
|-----------|--------------|-----------|
| Recall@10 | 0.775 [0.695, 0.855] | **0.775 [0.695, 0.855]** |
| Recall@5 | 0.425 [0.345, 0.505] | **0.425 [0.345, 0.505]** |
| 知覚税@10 | 0.225 [0.145, 0.305] | **0.225 [0.145, 0.305]** |

この規模（17–31 件のコーパス、`num_candidates ≥ 文書数`）では ES の近似 kNN（HNSW）が
実質厳密に一致する。**バックアンド切替が検索品質を変えない**ことを確認した。

## マルチシード CI（mock 埋め込み・seeds 0–3・ES バックエンド）

| シナリオ | 指標 | mean [95% CI] |
|---------|------|---------------|
| S1 設備保全 | Recall@10 | 0.775 [0.695, 0.855] |
| S1 | 知覚税@10（H3） | 0.225 [0.145, 0.305] |
| S3 弱信号 | Recall@10 | 0.462 [0.362, 0.561] |
| S3 | prefetch カバレッジ | **3.000 [3.000, 3.000]** |
| S5 インシデント | Recall@10 | 0.786 [0.654, 0.917] |
| S7 受注充足 | Recall@10 | 0.750 [0.662, 0.838] |
| S4 新規SKU | 転移ゲイン（H1） | **1.000 [1.000, 1.000]** |
| S2 物理↔記録（H9） | 融合誤差 | **0.718 [0.702, 0.733]** |
| S2 | 最良単一観測誤差 | 0.800 [0.769, 0.831] |
| S2 | 等重み対照（誤差） | 0.887 [0.860, 0.914] |

**H9 は CI 分離で支持**: 融合 0.718 [0.702, 0.733] ＜ 最良単一 0.800 [0.769, 0.831]（区間が重ならない）。
さらに**等重み対照 0.887 は単一観測にも劣る**ため、勝因は逆分散重みであって定数ではない。
**H1 転移ゲイン 1.000±0**・**prefetch カバレッジ 3.0±0** はシード間で完全安定。

> **CI が以前の live レポート（R@10 0.90・CI幅0）より低く・広い理由**は ES ではなく
> **mock 埋め込みが seed 依存**だから。`MockEmbedder` は content＋seed からベクトルを生成するため、
> seed ごとに擬似ランダムな座標になり recall がばらつく。つまりこの recall 値は
> **「mock モードの検索」**であって意味的検索品質の主張ではない（後述の制約）。

## 反証可能ゲート（全シナリオ・mock で緑）

受け入れテスト 70 件が全て緑。各シナリオの「壊し方」テスト（観測抑止で融合失敗・ノイズ増で
発見失敗・把持力上限引き下げでスキル転移失敗・鮮度逆転で上書き不成立 等）が引き続き機能している。

## 取れていないログ/データ（IMPROVEMENT に登録）

1. ~~**M21（Medium・再現性）** — manifest が `vector_backend` を記録しない~~ → **解決済み（P15）**。
   `create_manifest` が `vector_backend`（ES 時は `elasticsearch_url`＋index 命名規約）と
   `git_dirty_paths` を記録するようになり、manifest 単独でバックエンド・dirty 内容まで判別可能。
2. **（注記・スコープ）** `scenario-multi-seed-all` は **mock 埋め込み**のため、retrieval recall は
   seed 依存の擬似ランダム値で**意味的品質の指標ではない**。ES バックエンド上での**意味的**な
   CI が必要なら `MWS_CONFIRM_LIVE_SPEND=1 make scenario-multi-seed-live`（live・gemini-embedding-2）を使う。
   mock で意味を持つのは構造的不変量（H9 の CI 分離・転移 1.0・prefetch 3・各ゲート）。

## 制約（既知）

mock 埋め込みの recall は意味品質ではない（上記）。R@5 は構造上限（5/n_relevant）の影響を受ける。
S6 ルールベース・VLA は力/軌道モデル・業務系スタブ。A2A 未実装。`mws scenario run` 単体も既定 ES（Docker）必須。

## 再現

```bash
make scenario-multi-seed-all          # 環境構築→ES初期化→受け入れテスト→seeds0-3集計（ES）
make es-down                          # 後片付け（ES 停止）
# 意味的な live CI（実費）:
MWS_CONFIRM_LIVE_SPEND=1 make scenario-multi-seed-live
```
