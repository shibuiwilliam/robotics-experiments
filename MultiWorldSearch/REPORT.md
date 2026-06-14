# REPORT.md — MWS `make scenario-multi-seed-all` 検証レポート（seeds 0–3・mock・ES）

**日付**: 2026-06-14
**コマンド**: `make scenario-multi-seed-all`（`setup` → `es-reset` → 受け入れテスト → seeds 0–3 マルチシード集計）
**モード**: `MWS_CLOUD_MODE=mock`（埋め込みは MockEmbedder・**クラウド非接触＝$0**）／**ベクトルバックエンド = Elasticsearch**（docker-compose, dense_vector + kNN/HNSW）
**結果**: **受け入れテスト 70 passed**（in-memory・決定的）＋ **seeds 0–3 × 7 シナリオ完走（32 ラン・48 ES ストア・exit 0）**・実行後 ES 残インデックス **0**・全 manifest が backend／embedder 実空間を整合記録

> 直近の **ライブ** `make scenario-all` 走の詳細・コスト開示と、そこで判明した運用ギャップ
> （G1–G5）は [IMPROVEMENT.md](IMPROVEMENT.md) に登録済み。本走はその姉妹である
> **mock・マルチシード（統計的裏付け）走**の記録である。

---

## 1. 実行サマリー

| 段 | 内容 | 結果 |
|----|------|------|
| 環境構築 | `setup`（dev+live+es 同期） | OK |
| ES 初期化 | `es-reset`（volume wipe → up --wait） | Healthy |
| [1/2] 受け入れテスト | `pytest tests/scenarios/`（mock・in-memory・決定的） | **70 passed** |
| [2/2] マルチシード | seeds 0–3 × 7 シナリオ を **ES バックエンド**で実行 | **32 ラン・48 ストア完走（exit 0）** |
| 後始末 | 各ストアの ES インデックスを teardown で drop | **残 0** |
| 再現性 | 全 run の manifest が backend＋embedder 実空間を記録 | 整合（下記 §3） |
| コスト | mock（クラウド呼び出しなし） | **$0** |

---

## 2. マルチシード CI（mock 埋め込み・seeds 0–3・ES バックエンド）

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

- **H9 は CI 分離で支持**: 融合 0.718 [0.702, 0.733] ＜ 最良単一 0.800 [0.769, 0.831]（区間が重ならない）。
  等重み対照 0.887 は単一観測にも劣るため、勝因は逆分散重みであって定数ではない。
- **H1 転移 1.000±0**・**prefetch カバレッジ 3.0±0** はシード間で完全安定。
- これらの値は P14/P15 の in-memory／ES 走と**完全一致**しており、**バックエンド・走を跨いだ決定性**が保たれている。

---

## 3. manifest 自己記述の検証（P15/P16）

本走の 32 ラン manifest がすべて整合的に記録（例: `maintenance_handoff-3-*`）:

```
vector_backend     : elasticsearch
embedding_space    : gemini2-768-v1        ← embedder 実体由来（P16）
elasticsearch_index: mws-vectors-gemini2-768-v1-768d-*   ← 同じ space/dims から導出
git_dirty_paths    : [...]                 ← P15
```

`embedding_space` と `elasticsearch_index` が**同一 space/dims から導かれており食い違わない**ことを
実走で確認（P16 の狙い）。`make scenario-multi-seed-all` 単独で、どのバックエンド・どの埋め込み空間で
走ったかが manifest から判別できる。

---

## 4. 横断的検証

- **ベクトルバックエンド（Elasticsearch）**: 全 32 ランで ES（dense_vector+kNN）を使用（48 ストア）。
  コーパス規模（17–31 件）では HNSW が実質厳密一致（CI が in-memory と完全一致）。**残インデックス 0**。
- **反証可能ゲート**: 受け入れテスト 70 件が全緑。「壊し方」テスト（観測抑止で融合失敗／ノイズ増で発見失敗／
  把持力上限引き下げでスキル転移失敗／鮮度逆転で上書き不成立 等）が機能。
- **決定性**: mock 埋め込み＋固定 seed のため、ES 走と in-memory 走、本走と前走が同一値。

---

## 5. 限界・取得できていないデータ（→ IMPROVEMENT.md に登録）

1. **G6（Medium・計測の妥当性）【本走で新規発見】** — **ES バックエンド時、ベクトル検索のレイテンシが
   `local_ann` バケットで計時される**。本走（ES）の実測 `local_ann_p50 = 4.25ms`（in-memory 走の
   約 0.04ms の **~100倍**）は、実体が ES への **HTTP 往復**であって「local ANN」ではない。
   `engine.search` が `stores.vector.search` を一律 `local_ann` で計時するため、バックエンドにより
   バケットの意味が変わり、レイテンシ分解（CLAUDE.md §10）が誤読されうる。
   → **ES 検索は独立バケット（例 `es_search`）で計時**し、バックエンドを区別すべき。
2. **（注記・スコープ）** `scenario-multi-seed-all` は **mock 埋め込み**のため retrieval recall は
   seed 依存の擬似ランダム値で**意味的品質の指標ではない**（mock で意味を持つのは構造的不変量＝
   H9 の CI 分離・転移 1.0・prefetch 3・各ゲート）。意味的 CI が必要なら
   `MWS_CONFIRM_LIVE_SPEND=1 make scenario-multi-seed-live`（live・gemini-embedding-2）を使う。
3. **（既登録）** 直近のライブ `scenario-all` 走で判明した **G1–G5**（コスト未記録／spend ゲート無し／
   モード不可視／知覚税 S1 限定／ライブ E2E に PASS/FAIL アサート無し）も未対応のまま IMPROVEMENT.md に残る。

---

## 6. 総合判定

**`make scenario-multi-seed-all` は環境構築から ES クラスター初期化・受け入れテスト・seeds 0–3 集計までを
一括で完走し、全シナリオが各仮説の支持証拠を生んだ（H9 は CI 分離、H1 転移・prefetch は完全安定）。**
ES バックエンド上で 32 ラン・残インデックス 0・manifest 整合（backend＋embedder 実空間）・受け入れ 70 緑。
バックエンド切替（ES↔in-memory）が結果を変えないことも CI 一致で確認した。

残課題は「結論の信頼性」ではなく「計測網羅性・運用統制」: 本走で新規発見した **G6（ES 検索レイテンシの
バケット誤分類）** と、ライブ走由来の G1–G5。いずれも IMPROVEMENT.md に登録済み。

## 再現

```bash
make scenario-multi-seed-all          # 環境構築→ES初期化→受け入れテスト→seeds0-3集計（ES）
make es-down                          # 後片付け（ES 停止）
cat runs/<RUN_ID>/manifest.json       # backend / embedder 実空間 / es index 規約 / dirty パス
MWS_CONFIRM_LIVE_SPEND=1 make scenario-multi-seed-live  # 意味的な live CI（実費）
```
