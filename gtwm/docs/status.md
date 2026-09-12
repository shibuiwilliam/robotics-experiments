# 進捗（Claude Code が作業ごとに更新する）

最終更新: 2026-09-12

## 現在のフェーズ
P0 相当（シミュレーション基盤）。CLAUDE.md「現在のフェーズと着手順」の 1 から開始。

## 着手順チェックリスト
- [x] 1. 足場（pyproject / uv / Makefile / ruff / mypy / pre-commit / gtwm doctor）
- [x] 2. sim（MJCF 倉庫、センサ、アンカー、WMS モック、`make sim-smoke`）
- [x] 3. kg（gt-core.ttl、SHACL 3本、KGStore、EPCIS 取込）
- [x] 4. wm（エンコーダ、スロット、動態、`make train-smoke`）
- [x] 5. grounding（α / γ / ε / 同一性 / 乖離台帳）
- [x] 6. eval（ランナー、EXP-01/02/03/06）
- [ ] 7. whatif + ui
- [ ] 8. P1 相当（realism、EXP-04/05/11）
- [ ] 9. P2 相当（概念発見、2拠点連合、EXP-07/08/09、EXP-10 準備）

## 実験の状態
| EXP | 仮説 | 状態 | 最新結果（runs/ パス） | 判定 |
|---|---|---|---|---|
| EXP-01 | H1（接地精度） | smoke実行済み | `runs/EXP-01/20260913-074513`：position_fact_f1=0.849, type_accuracy=0.806 | 参考（smoke） |
| EXP-02 | H1（遮蔽下の同一性） | smoke実行済み | `runs/EXP-02/20260913-074542`：id_switch_rate=0.0 | 参考（smoke） |
| EXP-03 | H2（記号条件付け） | smoke実行済み | `runs/EXP-03/20260913-072507`：error_improvement_60s≈0.004, effective_horizon_ratio=NaN | 参考（smoke） |
| EXP-06 | H5（シールド付き計画） | smoke実行済み | `runs/EXP-06/20260913-074554`：violations_with_shield=0/3, violations_without_shield=3/3, throughput_loss≈1.42 | 参考（smoke） |

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

## 着手順5（grounding）の実装メモ（2026-09-12）
- 契約通りに実装：`Probe`（existence/zone分類+床面座標回帰/state/relations の4ヘッド。型は `SlotModule.type_logits` を再利用）、`Conditioner`（γ：`rgcn`/`text` の2実装を config で切替、`KGSubgraph` は `KGStore.snapshot(t)` から `kg/queries/zone_facts.rq` で局所部分グラフを抽出）、`Consistency.epsilon`（`configs/grounding/epsilon.yaml` の重みを読み、ε_h と3分解を返す）、`identity.py`（scipy ハンガリアン割当＋距離ゲート、遮蔽保持、`force_reidentify`）、`ledger.py`（SQLite、状態遷移 open→confirmed/dismissed→resolved を型で強制。poc_plan.md 5.4「PoCでは自動反映は行わず、全件人が判定する」に従い、open からの遷移は全て `resolver` 必須）。
- **α の学習方式**：`grounding/train_probes.py` がアンカー時刻（`events.parquet` の `event_type=="anchor"` 行、`zone` 列あり）だけを教師にした自己教師あり学習を行う（world_model.md「全真値ではなくアンカー時刻の真値のみを使う」）。DETR 系と同様、各サンプルで現在の床面座標予測に最も近いスロットへその場で教師信号を割り当てる（K個のスロットのうちどれが対象個体かは事前固定できないため）。F1/ECE の**評価**はシミュレーション全真値（`poses.parquet`）を使ってよい（world_model.mdの明記通り、学習ラベルとは別扱い）。
- **重要な実装上の教訓**：クラス不均衡（Storage_A が smoke データの過半数）に対して重み無し交差エントロピーだと多数派クラスへの一様予測に崩壊し、accuracy は高く見えても macro-F1 が低い（実測：accuracy 0.61 / macro-F1 0.21）という偽陽性を検出した。逆頻度クラス重み＋ステップ数を250→6000に増加（バッチ8×6000ステップでも smoke データ全体で24秒、3分予算に大幅な余裕）した結果、真に4クラス全てを学習した上で **zone_f1_macro=0.658（目標0.6以上を達成）、zone_accuracy=0.722、ECE 0.117→0.099（較正後改善）** を得た。単一 accuracy 指標だけで「学習できた」と判断しない教訓として記録する。
- **ε の簡略化（要フォローアップ）**：F^h（業務プロセスの遷移）は、対象個体の「今後 h 秒以内の予定イベント」をWMSモックから引く仕組みをまだ持たないため、暫定的に恒等写像（記録上のゾーンは h 秒後も不変という仮定）として実装した。3分解（知覚誤り/プロセス不遵守/オントロジー欠落）も、現在時刻の α が真値と一致するかどうかで知覚誤りを切り分ける簡易ヒューリスティック（オントロジー欠落は概念発見ループ稼働まで常に0）。将来 `wms_mock.generate_orders` の割当を使った真の F^h に置き換えることを次セッションへの申し送りとする。
- **同一性損失は未接続**：`identity.py` はバッチ単位のトラック情報を持つ形になっていないため、`wm/train.py` の `loss_weights.identity` はまだ配線していない（anchor_grounding と constraint は配線済み、`probe`/`anchor_samples` を明示的に渡した場合のみ有効化・両方 None なら既存 configs の挙動は不変）。
- **`gtwm ground run --episode ep_0000_seed0 --set smoke` の実行結果**（実機確認）：beliefs=285、ledger_entries=44（全て open）、ε_10s=0.284（decomposition: perception=0.031, process_deviation=0.253, ontology_gap=0.0, n=64）。30秒エピソードでは h=60s/300s/1800s はエピソード長を超えるため計算されない（ログにも出力されない。エピソード長を超えるホライズンを黙って0扱いにするのではなく、正直にスキップする設計）。
- 循環 import 回避：`wm/train.py` は `grounding.constraints`（`wm` に依存しない純粋関数）のみ実 import し、`Probe`/`AnchorSample` 型は `TYPE_CHECKING` 下でのみ import する（`grounding.train_probes` が `wm.train` に依存する方向とは逆になるため）。
- `tests/unit/test_blindness.py` は AST 解析で `grounding/`・`wm/` 配下の全 `.py` を検査し、`gtwm.sim.wms_mock` の import と `data/injections` 文字列参照を禁止する（import 文の静的検査であり、実行時 import の有無に関わらず検知する）。

## 着手順6（eval）の実装メモ（2026-09-13）

- `experiments/criteria.yaml` に poc_plan.md 3.1 の H1〜H9/N1 を一字一句転記。`src/gtwm/eval/metrics.py` に付録Aの全指標関数（`fact_distance_d` 等11関数）、`stats.py` に `paired_bootstrap_ci`/`wilson_interval`、`scoring.py` を `data/injections` を読んでよい唯一のモジュールとして実装（`test_blindness.py` は引き続き緑）。`runner.py` が `runs/EXP-xx/<ts>/{metrics.json,report.md,config_resolved.yaml,git.txt,log.txt}` を書き出し、smoke時は判定を常に「参考（smoke）」に固定する（`judge_criteria` は本実行でのみ呼ぶ）。
- **EXP-01/02/03 の6.1と6.2の対応関係を確認**：EXP-01=H1「アンカー接地の基礎精度」、EXP-02=H1「遮蔽下の同一性維持」（EXP-01とEXP-02は同じH1を指標で分担：EXP-01がF1/型精度、EXP-02がID切替率/IDF1）、EXP-03=H2「記号条件付けのアブレーション」。当初の作業指示でEXP-02をH2と誤記していたが、poc_plan.md 6.1を直接確認して訂正した。
- **EXP-03**：潜在空間誤差（コサイン距離）で (a)条件なし (b)静的文脈 (c)動的文脈 を比較。`paired_bootstrap_ci()` で none-vs-dynamic・none-vs-static の対応あり比較を追加（同一(episode, t0)地点での誤差なのでペアが保たれる）。smokeエピソードが短く(1〜8秒)、60秒地点の`effective_horizon_ratio`は分母(none_h_star)が0になりNaNになる（正直にNaNのまま報告、smokeの限界として記載）。
- **EXP-06（新規実装、シールド付き計画）**：`grounding/shield.py` の `RestrictedZoneShield` が `wm/planner.py MPPIPlanner` の `ShieldFn` 契約に適合する形で、進入禁止ゾーン制約（`gt:ZoneCapacityShape` を capacity=0 として解釈）をαのゾーン確率に対して直接チェックする（256候補×20ホライズン分のpyshacl検証は非現実的なため、`grounding/constraints.py`と同じ意味論を再利用）。危険物隣接・容量制約への拡張、100タスクへの拡大は本実行時に行う（smokeは3タスク）。
- **重要な実装バグを2件発見・修正（`wm/planner.py`）**：
  1. **シールドの安全網が無かった**：受理された候補どうしの重み付き平均（凸結合）は、Dynamics/Probeが非線形であるため、平均自体がシールドを満たす保証がない（個別には合格する2つの候補を混ぜた行動が、どちらとも異なる違反状態になりうる）。EXP-06のsmoke実行で実際に発生（shield無し3/3違反、shield有り当初1/3違反）。平均後の行動を再検査し、違反していれば単一の最小コスト受理候補にフォールバックする安全網を追加し、shield有りでの違反を0/3にした。`tests/unit/test_wm_planner.py`に非凸受理領域（2つの離れた許容区間）での最小再現テストを追加し、修正前後で赤→緑になることを確認済み。
  2. **棄却候補へのfill_valueが符号を考慮していなかった**：`costs[finite].max() * 10 + 1` は cost_fn が負の値（報酬形コスト）を返す場合、棄却候補の方が魅力的になる（符号反転）。有限コストの「広がり」に対する相対マージン（`max + spread*10 + 1`、spreadは`max-min`をclamp_min(1.0)）に修正し、符号に依存しないようにした。
  3. EXP-06のsmoke設定では、既定のMPPIパラメータ（`noise_std=1.0`）では候補行動が小さすぎてゾーン予測をほとんど動かせず、シールド有無の差が全く出なかった（診断済み：huge action(*50)でようやくゾーン確率の標準偏差0.19が出る）。`noise_std=25`・`temperature=0.3`・`shortcut_bonus=30`に調整することで、意味のある対比（shield無し3/3違反→shield有り0/3違反）を得た。
- `src/gtwm/grounding/ground_run.py` の `_encode_single_frame_slots`（1フレームをスロットへ符号化する共通ロジック）を `encode_single_frame_slots` として公開し、EXP-06からも再利用する（重複実装を避けるための小リファクタ、`__all__`に追加）。
- 4実験（EXP-01/02/03/06）のsmoke実測時間：27.3s / 0.9s / 7.4s / 27.0s（いずれも5分予算に大幅な余裕）。
