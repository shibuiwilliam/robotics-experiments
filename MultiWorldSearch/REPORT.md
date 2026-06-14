# REPORT.md — MWS Multi-Seed Validation Report

**Date**: 2026-06-14（`make scenario-multi-seed-all`・P15 完了後）
**Command**: `make scenario-multi-seed-all`（環境構築 → ES クラスター初期化 → 受け入れテスト → seeds 0–3 マルチシード）
**Mode**: `MWS_CLOUD_MODE=mock`（埋め込みは MockEmbedder・クラウド非接触）／**vector backend = Elasticsearch**（docker-compose, dense_vector+kNN）
**Result**: **受け入れテスト 70 passed**（in-memory・決定的）＋ **seeds 0–3 × 7 シナリオ完走**（ES 上で 32 run・48 ベクトルストア）・実行後 ES インデックス残 **0**・**全 32 manifest が `vector_backend: elasticsearch` を記録**（P15 検証）

> 本走は P15（manifest の自己記述化）後の検証。`make scenario-multi-seed-all` が
> 「環境構築（`uv sync --extra es`）→ クリーンな ES クラスター初期化（`es-reset`）→
> 受け入れテスト（mock・in-memory）→ マルチシード集計（ES バックエンド）」を一括実行する。

---

## 実行サマリー

| 段 | 内容 | 結果 |
|----|------|------|
| 環境構築 | `setup`（dev+live+es 同期） | OK |
| ES 初期化 | `es-reset`（volume wipe → up --wait） | Healthy |
| [1/2] 受け入れテスト | `pytest tests/scenarios/`（mock・in-memory・決定的） | **70 passed** |
| [2/2] マルチシード | seeds 0–3 × 7 シナリオ を **ES バックエンド**で実行 | 32 run・48 ストア |
| 後始末 | 各ストアの ES インデックスを teardown で drop | **残 0** |
| 再現性 | 全 run の manifest が backend を記録 | **32/32 `elasticsearch`** |
| コスト | mock（クラウド呼び出しなし） | **$0** |

## P15 検証（manifest の自己記述化）

今回のマルチシード 32 run すべての `manifest.json` が新フィールドを記録していることを確認:

```
vector_backend     : elasticsearch
elasticsearch_url  : http://localhost:9200
elasticsearch_index: mws-vectors-gemini2-768-v1-768d-*   (per-run uuid-suffixed)
git_dirty_paths    : [" M MultiWorldSearch/blog.ja.md", ...]
```

**manifest 単独で「どのベクトルバックエンドで・どの index 規約で・どの dirty 状態で走ったか」が
判別可能**になった。前回（P14 検証）は ES↔in-memory 同値性を別途実行して確かめる必要があったが、
今回は manifest が直接 `elasticsearch` と記録している。

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
等重み対照 0.887 は単一観測にも劣るため、勝因は逆分散重み。**H1 転移 1.000±0**・**prefetch 3.0±0** はシード間で完全安定。
これらの値は前回（P14 検証）の in-memory／ES と一致しており、**バックエンド・走を跨いだ決定性**が保たれている。

> CI が以前の live レポート（R@10 0.90・CI幅0）より低く・広いのは ES ではなく **mock 埋め込みが
> seed 依存**だから（`MockEmbedder` は content＋seed からベクトル生成）。この recall は
> 「mock モードの検索」であり意味的品質の主張ではない（後述の制約）。

## 反証可能ゲート（全シナリオ・mock で緑）

受け入れテスト 70 件が全て緑。各シナリオの「壊し方」テスト（観測抑止で融合失敗・ノイズ増で
発見失敗・把持力上限引き下げでスキル転移失敗・鮮度逆転で上書き不成立 等）が引き続き機能している。

## 取れていないログ/データ（IMPROVEMENT に登録）

1. **M22（Low・再現性の精度）** — manifest の `embedding_space` と `elasticsearch_index` は
   `settings.default_embedding_space` 由来だが、**実際のベクトルストアは embedder の空間/次元**
   （単一情報源、`BaseScenario._init_run`）で名前空間化される。mock では一致する（MockEmbedder が
   settings の空間を使う）が、**live では embedder が常に `gemini2-768-v2`/768 を使う**ため、
   `MWS_DEFAULT_EMBEDDING_SPACE` が未設定/古いと manifest の `elasticsearch_index` 規約が実 index と
   食い違いうる（P9 の空間ドリフトと同種）。`create_manifest` は embedder の実空間/次元を記録すべき。
2. **（注記・スコープ）** `scenario-multi-seed-all` は mock 埋め込みのため retrieval recall は
   seed 依存の擬似ランダム値で**意味的品質の指標ではない**。意味的 CI が必要なら
   `MWS_CONFIRM_LIVE_SPEND=1 make scenario-multi-seed-live`（live・gemini-embedding-2）を使う。

## 制約（既知）

mock 埋め込みの recall は意味品質ではない（上記）。R@5 は構造上限（5/n_relevant）の影響を受ける。
S6 ルールベース・VLA は力/軌道モデル・業務系スタブ。A2A 未実装。`mws scenario run` 単体も既定 ES（Docker）必須。

## 再現

```bash
make scenario-multi-seed-all          # 環境構築→ES初期化→受け入れテスト→seeds0-3集計（ES）
make es-down                          # 後片付け（ES 停止）
cat runs/<RUN_ID>/manifest.json       # backend / es index 規約 / dirty パスを確認（P15）
MWS_CONFIRM_LIVE_SPEND=1 make scenario-multi-seed-live  # 意味的な live CI（実費）
```
