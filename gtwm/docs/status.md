# 進捗（Claude Code が作業ごとに更新する）

最終更新: 2026-09-12

## 現在のフェーズ
P0 相当（シミュレーション基盤）。CLAUDE.md「現在のフェーズと着手順」の 1 から開始。

## 着手順チェックリスト
- [x] 1. 足場（pyproject / uv / Makefile / ruff / mypy / pre-commit / gtwm doctor）
- [x] 2. sim（MJCF 倉庫、センサ、アンカー、WMS モック、`make sim-smoke`）
- [x] 3. kg（gt-core.ttl、SHACL 3本、KGStore、EPCIS 取込）
- [x] 4. wm（エンコーダ、スロット、動態、`make train-smoke`）
- [ ] 5. grounding（α / γ / ε / 同一性 / 乖離台帳）
- [ ] 6. eval（ランナー、EXP-01/02/03/06）
- [ ] 7. whatif + ui
- [ ] 8. P1 相当（realism、EXP-04/05/11）
- [ ] 9. P2 相当（概念発見、2拠点連合、EXP-07/08/09、EXP-10 準備）

## 実験の状態
| EXP | 仮説 | 状態 | 最新結果（runs/ パス） | 判定 |
|---|---|---|---|---|
| EXP-01 | H1 | 未着手 | | |

## 既知の制約・記録
- MPS fallback が発生した op：`make train-smoke`（configs/wm/smoke.yaml）実行中は **0件**（`PYTORCH_ENABLE_MPS_FALLBACK=1` 下で `The operator '...' is not currently supported on the MPS backend` 系の warning は一度も出なかった）。ただし別種の MPS 制約を1件発見：`nn.TransformerEncoderLayer` の既定 dropout（0.1）を使うと、`torch.no_grad()` 経路（推論）で `NotImplementedError: scaled_dot_product_attention for MPS does not support dropout` が発生する（train() の勾配ありパスでは再現しなかった＝SDPA のバックエンド選択が学習時と推論時で異なるため）。これは fallback ではなく明示的な未サポートの組み合わせなので `PYTORCH_ENABLE_MPS_FALLBACK` では救えない。対応：`src/gtwm/wm/dynamics.py` の `DynamicsHead` で `TransformerEncoderLayer(..., dropout=0.0)` を明示指定し、この動態モデルでは dropout を使わないことにした（`gtwm wm rollout` で再現・解消を確認済み）。
- `platform: linux/amd64` を使ったサービス：なし
- 計画書からの差分：全フェーズをシミュレーションで実施（CLAUDE.md 参照）
- `make setup` の `pre-commit install` がこの開発機では失敗する：親リポジトリ（`robotics-experiments`）の `core.hooksPath` が明示的に `.git/hooks`（既定値と同じ）に設定されており、pre-commit がこれを検出すると安全のためインストールを拒否する。git config の変更は方針上行わないため、`uv run pre-commit install` を手動で通すか `core.hooksPath` を外すかはユーザー側の判断に委ねる。`doctor` / `lint` / `test` は `pre-commit install` に依存せず単独で緑になることを確認済み。
- 開発機には mujoco が `uv sync --all-extras` で導入済みで、`gtwm doctor` のオフスクリーン描画チェックは OK。

## 着手順2（sim）の実装メモ（2026-09-12）
- MJCF は `sim/assets/generate_mjcf.py` で生成する静的ファイル（`warehouse.xml` + `include/*.xml`）。レイアウトを変える場合はこのスクリプトを編集して再実行する。
- 実体数：ゾーン6・ラック4×スロット9=36・ドック2・コンベア1・AGV2（3自由度の簡易平面モデル：スライドx/y＋ヨー、真の差動2輪ではない簡略化）・作業者3（mocap キネマティック、Catmull-Rom スプライン巡回）・パレット20＋ケース40（自由関節、最大3段スタック10組）・カメラ4。
- 遮蔽率は「セグメンテーション可視画素数 ÷ 解析的に投影したバウンディングボックスの外接矩形面積」で近似（2回目のレンダリングによる真のシルエット計算はコスト上見送り、外接矩形近似である旨を `render.py` に明記）。
- 30秒 smoke（`make sim-smoke`）の生成時間は **7.1秒**（実時間の約0.24倍、目標2倍以内に対し余裕あり）。オブジェクト数の削減は不要だった。内訳目安：物理6000ステップ≈0.8秒、4カメラ×3パス×300フレームのレンダリング≈3.2秒（レンダラーのウォームアップ後）。
- WMS 記録イベントは、着手順2の時点ではアンカー検出結果から即時導出している（gate:1→receiving、gate:2→shipping、scale→inspecting、scan+ゾーンで storing/picking）。遅延・欠落・誤登録などの乖離注入（`InjectionConfig`）は全件数0のデフォルトで no-op、実装は着手順8。
- `tests/sim`（決定性・アンカー検出・書出形状、計8件）は `make test-sim` で3秒未満、GUI ウィンドウは一切開かない。

## 着手順3（kg）の実装メモ（2026-09-12）
- **RDF-star からの逸脱（要ADR）**：ontology.md / CLAUDE.md は信念を RDF-star の埋め込み三つ組で格納する前提だが、pyproject.toml が固定する rdflib 7.6.0 には Turtle-star/SPARQL-star のパーサが無いことを確認した（`rdflib.plugin.plugins(kind=Parser)` に該当プラグインが無く、`<< s p o >>` 構文は N3 パーサが `BadSyntax` で拒否する）。代わりに標準 RDF 具象化（reification：`_:b a rdf:Statement, gt:Belief ; rdf:subject s ; rdf:predicate p ; rdf:object o ; gt:confidence ...`）を rdflib/Oxigraph 両バックエンド共通の表現として採用した（`src/gtwm/kg/schema.py` の NOTE 参照）。将来 RDF-star が必要になれば ADR で採否を判断する。
- **vendor/ の取得結果**：PROV-O（`https://www.w3.org/ns/prov.ttl`）、SOSA（`https://www.w3.org/ns/sosa/`）、SSN（`https://www.w3.org/ns/ssn/`）は公式 Turtle を実際に取得・parse 確認済み。BFO は `http://purl.obolibrary.org/obo/bfo.owl`（OBO PURL 経由の公式 RDF/XML、CC BY 4.0）を実物取得。EPCIS 2.0 は公式の JSON-LD `@context`（`https://ref.gs1.org/standards/epcis/epcis-context.jsonld`）を実物取得したが、これは語彙定義のみで完全な OWL 公理ではない。**CBV のみスタブ**：`ref.gs1.org/cbv/` 配下の JSON-LD コンテキストは全て404、`gs1.org/voc/` は403 で、機械可読な CBV RDF を取得できなかったため、`wms_mock.py`/`gt-core.ttl` が実際に使う biz-step/disposition の URI だけを最小定義した `cbv-stub.ttl` を作成（詳細は `ontology/vendor/LICENSES.md`）。
- **snapshot(t) の実装方針**：`kg/epcis.py` は同一個体の新しい `currentZone` 信念が来た時点で直前の信念の `valid_to` を閉じる（そうしないと移動履歴の全信念が「現在も有効」のまま残り、`snapshot(t)` や SHACL の `maxCount 1` が誤検知する）。`KGStore.snapshot(t)`/`validate()` は意図的に (subject, predicate) の重複排除をしない：正しく閉区間化されていれば同一時刻の重複は発生せず、それでも重複が残る場合こそ「同一時刻に複数の値を主張している」という真の乖離であり、SHACL の maxCount 制約はこれを検出するためにある。
- **docker-compose**：`profile core` に Oxigraph のみ追加（timescaledb/minio/grafana は消費するコンポーネントが無いため見送り、infra.md「まず『なくても回るか』を検討する」に従う）。Oxigraph の公式イメージ（`ghcr.io/oxigraph/oxigraph`、arm64 実物確認済み）は distroless 相当で shell/wget/curl を一切含まない（`docker run --entrypoint sh ...` が `exec: "sh": executable file not found` で失敗することを確認済み）ため、infra.md が求める **コンテナ側 CMD healthcheck を実装できない**。代わりに `make up` から `scripts/wait_for_oxigraph.py` を呼び、SPARQL クエリエンドポイントへの HTTP ポーリングで起動待ちする。`make up`/`make test-int` は実機で確認済み（3件の integration テストが green）。
- `.pre-commit-config.yaml` の TODO（session 01 で保留）を解消：`ontology/` `src/gtwm/kg/queries/` 変更時に `gtwm kg validate` を local hook として実行する。
- `gtwm kg validate` は ttl構文・SHACL自己整合（shapes を空データグラフに対して pyshacl 実行できるか）・queries/*.rq の SPARQL構文を検査する実用的な定義とした。

## 着手順4（wm）の実装メモ（2026-09-12）
- 契約通りに実装：`Encoder.encode`（凍結 `facebook/dinov2-small` + 学習可能アダプタ1層、HFキャッシュ経由・torch.hub不使用）、`SlotModule`（Slot Attention, カメラ単位で呼ぶ設計）、`Dynamics.rollout`（Transformer 4層/d=256、アンサンブル3、出力 [E,B,h,K,D]）。`Fusion`（カメラ毎スロット→床面座標→統合）は契約外のため自由設計：各スロットから正規化ピクセル位置を回帰し、MJCF由来のカメラ外部パラメータ（`sim/assets/warehouse.xml` を `mujoco` で読む）でレイキャストして床面座標を得て、K個の学習可能クエリでクロスアテンションプーリングして統合する。
- `Conditioner`（γ、KGSubgraph→cond）と `Probe`/`Consistency` は着手順5（grounding）の責務のため未実装。`Dynamics.rollout` は `cond: Tensor|None` を受け取れる契約のまま、None のときゼロ条件で動く（session 05 が実装した Conditioner をそのまま差し込める）。
- 学習データ：`wm_smoke`（30秒×2エピソード、`gtwm sim gen` で生成、`make train-smoke` が無ければ自動生成）。学習/評価split は時系列順（1本目=train、2本目=eval）。損失は予測損失のみ（重み1.0）、順列不変（scipy `linear_sum_assignment` によるハンガリアン割当、勾配は流さない）。アンカー接地・制約・同一性損失は重み0のまま session05 以降で有効化する。
- **MPS 固有の不具合を発見・修正**：`nn.TransformerEncoderLayer` の既定 dropout(0.1) が `torch.no_grad()` 推論経路でのみ `NotImplementedError: scaled_dot_product_attention for MPS does not support dropout` を起こす（学習時の勾配ありパスでは再現しない＝SDPA バックエンド選択の違い）。`DynamicsHead` で `dropout=0.0` を明示して回避（詳細は上の「既知の制約」）。
- **`make train-smoke` の受入結果**：所要時間 **4.1〜4.4秒**（目標3分以内に大幅な余裕）、`mlflow`（ローカル `mlruns/`、ファイルストア。新しめの mlflow はファイルストアを既定拒否するため `MLFLOW_ALLOW_FILE_STORE=true` を `train()` 内で明示設定）に real な減少損失曲線を記録済み（5.65→3.29、6ステップ）。ピークメモリは `/usr/bin/time -l` 実測で **maximum resident set size ≈ 838MB**（12GB予算に対し大幅な余裕）。合成系列（等速直線運動・静止+遮蔽）の10ステップ先コサイン距離はどちらも `configs/wm/base.yaml` の `synthetic_eval.max_latent_cosine_distance=0.2` を十分下回る（実測 ~0.005〜0.02、テストで検証）。
- チェックポイントは「損失改善時に保存、maxステップ到達時にも直近状態を必ず保存」に修正（当初 max_steps で早期終了すると保存されないバグがあったため、`_save_checkpoint` をステップ単位の判定に統一）。
