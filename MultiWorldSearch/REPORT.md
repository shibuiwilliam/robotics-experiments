# REPORT.md — MWS `make scenario-multi-seed-all` 検証レポート（seeds 0–3・mock・ES・G1–G6 完了後）

**日付**: 2026-06-14
**コマンド**: `make scenario-multi-seed-all`（`setup` → `es-reset` → 受け入れテスト → seeds 0–3 マルチシード集計）
**モード**: `MWS_CLOUD_MODE=mock`（埋め込みは MockEmbedder・**実クラウド非接触＝実課金 $0**）／**ベクトルバックエンド = Elasticsearch**（docker-compose, dense_vector + kNN/HNSW）
**結果**: **受け入れテスト 91 passed**（in-memory・決定的）＋ **seeds 0–3 × 7 シナリオ完走（32 ラン・48 ES ストア・exit 0）**・実行後 ES 残インデックス **0**・全 manifest が backend／embedder 実空間／cloud コストを整合記録

> 本走は G1–G6（コスト記録・spend ゲート・走行前バナー・知覚税拡張・ライブ E2E アサート・ES レイテンシ
> バケット分離）の実装完了後の初の multi-seed 検証走であり、新機能が実走で機能することを確認する。

---

## 1. 実行サマリー

| 段 | 内容 | 結果 |
|----|------|------|
| 環境構築 | `setup`（dev+live+es 同期） | OK |
| ES 初期化 | `es-reset`（volume wipe → up --wait） | Healthy |
| [1/2] 受け入れテスト | `pytest tests/scenarios/`（mock・in-memory・決定的） | **91 passed** |
| [2/2] マルチシード | seeds 0–3 × 7 シナリオ を **ES バックエンド**で実行 | **32 ラン・48 ストア完走（exit 0）** |
| 後始末 | 各ストアの ES インデックスを teardown で drop | **残 0** |
| 再現性 | 全 run の manifest が backend＋embedder 実空間＋cloud を記録 | 整合 |
| コスト | mock（実クラウド呼び出しなし） | **実課金 $0**（`cloud.llm_calls_real=0`） |

## 2. マルチシード CI（mock 埋め込み・seeds 0–3・ES バックエンド）

| シナリオ | 指標 | mean [95% CI] |
|---------|------|---------------|
| S1 設備保全 | Recall@10 | 0.775 [0.695, 0.855] |
| S1 | 知覚税@10（H3） | 0.225 [0.145, 0.305] |
| S3 弱信号 | Recall@10 | 0.462 [0.362, 0.561] |
| **S3** | **知覚税@10（H3・G3 で新規算出）** | **0.308 [0.208, 0.408]** |
| S5 インシデント | Recall@10 | 0.786 [0.654, 0.917] |
| **S5** | **知覚税@10（H3・G3 で新規算出）** | **0.214 [0.083, 0.346]** |
| S7 受注充足 | Recall@10 | 0.750 [0.662, 0.838] |
| **S7** | **知覚税@10（H3・G3 で新規算出）** | **0.250 [0.162, 0.338]** |
| S4 新規SKU | 転移ゲイン（H1） | **1.000 [1.000, 1.000]** |
| S2 物理↔記録（H9） | 融合誤差 | **0.718 [0.702, 0.733]** |
| S2 | 最良単一観測誤差 | 0.800 [0.769, 0.831] |

- **H9 は CI 分離で支持**: 融合 0.718 [0.702, 0.733] ＜ 最良単一 0.800 [0.769, 0.831]（区間が重ならない）。
- **H1 転移 1.000±0** はシード間で完全安定。
- **H3 知覚税が4シナリオに拡張（G3）**: S1 0.225・S3 0.308・S5 0.214・S7 0.250。従来 S1 のみだった特権オラクル差分が、
  検索中心シナリオ全体で CI 付きで定量化できるようになった（PROJECT.md は H3 を「1, 全般」と位置づける）。
- R@10・H9・転移の値は P14–P17 走と**完全一致**（バックエンド・走を跨いだ決定性）。

## 3. G1–G6 の実走検証（本走の成果物で確認）

ES バックエンドの実 run（例 `incident_response-0-*`）の成果物で、新機能が実際に機能していることを確認:

- **G1（コスト記録）**: `manifest.cloud` と `metrics.cloud` に同一のクラウド使用ledgerが記録される —
  `embedding_requests=7, embedding_texts=19, embedding_tokens=561, llm_calls=8（real=0/modeled=8）,
  estimated_cost_usd=0.00158`。**`llm_calls_real=0`** が mock＝実課金ゼロを正しく示す。
- **G3（知覚税拡張）**: `metrics.perception_tax` に `oracle`/`tax` が入る（従来 S1 のみ → S1/S3/S5/S7）。
  非該当の S2/S4/S6 は `perception_tax.applicable=false`＋理由。
- **G6（レイテンシバケット分離）**: ES 走の system 指標に **`es_search_p50=5.77ms`** が独立計上され、
  `local_embed=0.086ms`（埋め込み）と区別される。**ES の HTTP 往復が `local_ann` に混ざらない**（G6 の狙い）。
- **G2/G5（バナー）・G4（受け入れアサート）**: `mws scenario run` 経路で機能（mock 全7シナリオで acceptance PASS を別途確認済み）。
  なお本 multi-seed-all の [2/2] は `eval multi-seed` 経路のため G4 のper-run アサートは通らない（§5-2 参照）。

## 3b. `make scenario-all`（単一seed・ES）でのゲート実走（本依頼で実行）

multi-seed-all が通さない経路（G1 spend ゲート・G4 per-run 受け入れアサート）を `scenario-all` で実走確認:

- **G1 spend ゲート（実走）**: 素の `scenario run`（`.env` 由来で **live 解決**）は実行を**拒否**し `exit=1`・
  クラウド呼び出し **0**:
  `Error: Refusing to run LIVE … cloud_mode=live and MWS_CONFIRM_LIVE_SPEND!=1. Estimated cost: ~$0.008 …`。
  前回の無確認実課金は再発防止された。本検証は `MWS_CLOUD_MODE=mock make scenario-all`（$0）で実行。
- **G4 受け入れアサート（実走）**: `[2/2]` の全7シナリオで受け入れ基準ゲートが緑（`all N criteria PASS`）。
  例: maintenance(4/4)・incident(5/5)・order(2/2)・physical_record(2/2)・new_sku(2/2)・counterfactual(2/2)・
  collective(4/4)。1基準でも未達なら `scenario-all` は非ゼロ終了する。
- **G2/G5 バナー**: 7/7 走で `[MWS] cloud_mode=mock | embedding=mock | vector_backend=elasticsearch | seed=0 …` を表示。
- **G6/G1 成果物（全7 ES走）**: 全走で `system.es_search_*` 計上・`local_ann` 不在、`manifest.cloud`／`metrics.cloud`
  にコスト台帳（`llm_calls_real=0`）。**[1/2] 受け入れテスト 91 passed**。

## 4. 横断的検証

- **ベクトルバックエンド（Elasticsearch）**: 全 32 ランで ES（dense_vector+kNN）を使用（48 ストア）。残インデックス **0**。
- **反証可能ゲート**: 受け入れテスト 91 件が全緑。「壊し方」テストが機能。
- **決定性**: mock 埋め込み＋固定 seed のため ES 走と in-memory 走、本走と前走が同一値。

## 5. 限界・取得できていないデータ（→ IMPROVEMENT.md に登録）

1. **G7（Medium・コスト精度）【本走で新規発見】** — `cloud.estimated_cost_usd` が **real 呼び出しと
   modeled（コストモデルのみ・非課金）呼び出しのトークンを合算**している。本走（mock）では
   `llm_calls_real=0` なのに `estimated_cost_usd=0.00158`＞0 となり、**実課金ゼロの走が非ゼロの推定額を表示**する。
   ライブ走でも modeled ステップのトークンが混ざるため、headline のコスト額が**実際の課金額を上回る**。
   サマリは `llm_calls_real`/`llm_calls_modeled` を分離している（M8）のに金額は未分離。
   → **real 呼び出し由来のみの `estimated_cost_usd_real` を併記**（または金額を real 限定に）し、コスト台帳を実課金と一致させる。
2. **（仕様・要記録）** `make scenario-multi-seed-all` の [2/2] は `eval multi-seed` を使い、G4 の per-run
   受け入れアサート（`scenario run --assert` 経路）を通らない。CI 集計が目的のため設計どおりだが、
   **multi-seed 集計に受け入れ判定を組み込むかは将来検討**（現状は `scenario-all` がゲート役）。
3. **（注記・スコープ）** mock 埋め込みのため retrieval recall は seed 依存の擬似乱数で**意味的品質の指標ではない**。
   意味的 CI は `MWS_CONFIRM_LIVE_SPEND=1 make scenario-multi-seed-live`（live・gemini-embedding-2）で取得する。

## 6. 総合判定

**`make scenario-multi-seed-all` は環境構築から ES 初期化・受け入れテスト・seeds 0–3 集計までを一括完走し、
全シナリオが各仮説の支持証拠を生んだ（H9 CI 分離、H1 転移安定、H3 知覚税が4シナリオに拡張）。**
G1–G6 の新機能（cloud コスト台帳・perception_tax 拡張・`es_search` バケット）は実 ES 走で機能を確認。
ES 上で 32 ラン・残 0・manifest 整合・受け入れ 91 緑。

本走で新規に見つかったのは **G7（estimated_cost_usd が real/modeled を合算しコスト精度を欠く）** の1件で、
IMPROVEMENT.md に登録した。結論の信頼性に関わる問題はなく、コスト計測精度の課題である。

## 再現

```bash
make scenario-multi-seed-all          # 環境構築→ES初期化→受け入れテスト→seeds0-3集計（ES）
make es-down                          # 後片付け（ES 停止）
cat runs/<RUN_ID>/manifest.json       # backend / embedder 実空間 / cloud コスト台帳 / es index 規約
cat runs/<RUN_ID>/metrics.json        # system.es_search_p50_ms（G6）/ perception_tax（G3）/ cloud（G1）
MWS_CONFIRM_LIVE_SPEND=1 make scenario-multi-seed-live  # 意味的な live CI（実費）
```
