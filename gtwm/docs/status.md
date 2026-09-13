# 進捗（Claude Code が作業ごとに更新する）

最終更新: 2026-09-13

## 現在のフェーズ
P2 相当の一部（概念発見・LLM クライアント・EXP-08、2拠点連合・EXP-09）完了。CLAUDE.md
「現在のフェーズと着手順」の 9 のうち残り（反実仮想の忠実性＝EXP-07、EXP-10 準備）が次
（docs/prompts.md ではセッション11として分離されている）。

## 着手順チェックリスト
- [x] 1. 足場（pyproject / uv / Makefile / ruff / mypy / pre-commit / gtwm doctor）
- [x] 2. sim（MJCF 倉庫、センサ、アンカー、WMS モック、`make sim-smoke`）
- [x] 3. kg（gt-core.ttl、SHACL 3本、KGStore、EPCIS 取込）
- [x] 4. wm（エンコーダ、スロット、動態、`make train-smoke`）
- [x] 5. grounding（α / γ / ε / 同一性 / 乖離台帳）
- [x] 6. eval（ランナー、EXP-01/02/03/06）
- [x] 7. whatif + ui
- [x] 8. P1 相当（realism、EXP-04/05/11）
- [~] 9. P2 相当：概念発見・LLM クライアント・EXP-08・2拠点連合（EXP-09）は完了。
      反実仮想の忠実性（EXP-07）、EXP-10 準備は未着手

## 実験の状態
| EXP | 仮説 | 状態 | 最新結果（runs/ パス） | 判定 |
|---|---|---|---|---|
| EXP-01 | H1（接地精度） | smoke実行済み | `runs/EXP-01/20260913-074513`：position_fact_f1=0.849, type_accuracy=0.806 | 参考（smoke） |
| EXP-02 | H1（遮蔽下の同一性） | smoke実行済み | `runs/EXP-02/20260913-074542`：id_switch_rate=0.0 | 参考（smoke） |
| EXP-03 | H2（記号条件付け） | smoke実行済み | `runs/EXP-03/20260913-072507`：error_improvement_60s≈0.004, effective_horizon_ratio=NaN | 参考（smoke） |
| EXP-04 | H3（ε とドリフト検知） | smoke実行済み | `runs/EXP-04/20260913-084456`：drift_detection_auroc=1.0（n=2+2の極小サンプル）, epsilon_daily_cv≈0.003 | 参考（smoke） |
| EXP-05 | H4（乖離注入と検知） | smoke実行済み | `runs/EXP-05/20260913-085936`：detection_rate=0.567(17/30), false_alarms_per_day≈149760（smoke分母が極小なための人為的な跳ね上がり）, detection_latency_median_s=0.0 | 参考（smoke） |
| EXP-06 | H5（シールド付き計画） | smoke実行済み | `runs/EXP-06/20260913-074554`：violations_with_shield=0/3, violations_without_shield=3/3, throughput_loss≈1.42 | 参考（smoke） |
| EXP-11 | N1（非機能） | smoke実行済み | `runs/EXP-11/20260913-090429`：e2e_latency_p50_s≈7.16（batch実装のため悲観的上限）, availability=1.0（代理指標）, monthly_cost_per_zone≈$180（概算） | 参考（smoke） |
| EXP-08 | H7（概念発見） | smoke実行済み | `runs/EXP-08/20260913-100119`：n_candidates=5, injected_concept_top5_hit=3/3種（目標2種以上） | 参考（smoke） |
| EXP-09 | H8（連合ツインと漏洩評価） | smoke実行済み | `runs/EXP-09/20260913-103010`：ece=1.0（要フォローアップ、下記参照）, reconstruction_ssim≈0.123（目標0.30以下は満たす）, reid_top1_vs_chance=3.0（目標1.2倍以下を大きく超過、線形分類器がsmoke規模のワーカー3人を容易に判別）, n_policy_violations=1（意図的な違反要求が正しく拒否・記録された） | 参考（smoke） |

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

## 着手順7（whatif + ui）の実装メモ（2026-09-13）
- **文法の解釈補足**：poc_plan.md 付録Cは `<filter> ::= <triple-pattern> {"AND" <triple-pattern>}` と書くが `<triple-pattern>` 自体の生成規則を与えていない。例文 `WHERE station = gt:Station_S3` を正とし、`<triple-pattern> ::= IDENT "=" <entity-or-literal>` と解釈した（`src/gtwm/kg/whatif/grammar.lark` 冒頭のコメントに明記）。付録Cの他の記述と矛盾しないため、付録C自体の修正は不要と判断。
- **付録Cの例文は実質1つのみ**：docs/prompts.md セッション07プロンプトは「付録Cの例文3つ」を前提にしていたが、poc_plan.md 本体（5.6節）にはコンベア速度の例文が1つしか無い（付録Cはグラフィカルには例文を含まずBNFのみ）。文法の受理/拒否は`tests/unit/test_whatif_grammar.py`で19件の合成例文（AND条件、absent、current+/-、SAMPLES/INTERVAL/MODELの単独・組合せ等）でカバーした。
- **実体の置き換え**：poc_plan.md 5.6の例文が使う `gt:Station_S3`（「station」という概念自体が本オントロジーに存在しない）・`gt:Conveyor_C1` はこの倉庫の `sim/assets/registry.yaml` に存在しない。実行確認には `gt:Zone_Pick`（既知ゾーン）・`gt:Equipment_Conveyor_0001`（実際のコンベア個体ID）に置き換えた（`gtwm whatif "PREDICT ?queue_len AT +1s, +2s WHERE station = gt:Zone_Pick GIVEN do(gt:Equipment_Conveyor_0001.speed := 1.2 * current) SAMPLES 20 INTERVAL 0.9"` で実行確認済み）。
- **KPI実装はqueue_lenのみ**：`cycle_time`（例文が要求するもう1つの変数）はイベントベースの滞在時間計測が必要で本セッションのスコープ外。`kg/whatif/engine.py`は未対応の変数を`WhatIfUnsupportedVarError`で明示的に拒否する（黙って0を返すことはしない）。
- **行動空間とdo()の対応は簡略化（要フォローアップ／ADR候補）**：`wm/dynamics.py`のDynamicsは意味付けの無い抽象行動ベクトル（action_dim次元）しか持たず、エンティティ×プロパティ別の制御チャンネルが無い。`kg/whatif/compiler.py`は全ての`do(entity.property := ...)`を行動ベクトルの次元0への一様上書きとして解釈し、`current`の基準値は正規化された1.0のプレースホルダーとする（`ACTION_DIM_FOR_INTERVENTION`/`DEFAULT_CURRENT_VALUE`にコメントで明記）。行動空間を意味付けする設計は将来のセッション（P2以降）の課題。
- **実装中に発見・修正した実バグ**：`kpi_queue_length.rq`を`zone_facts.rq`と同じ「平坦化済みグラフ」前提で書いたが、`KGStore.query()`は具象化（reification）された生グラフに対して実行されるため、`?subject gt:currentZone ?zone`という平坦パターンは常にゼロ件だった。`tests/unit/test_whatif_engine.py`の`test_queue_len_filters_by_zone`が新規作成の合成Probeテストでこれを検出（フィルタ無し=5件、同じゾーンでフィルタ=0件という矛盾）。修正：`store.snapshot(t)`（材質化済みグラフ）に対してクエリするよう`engine.py`を変更。
- **シールドの汎化**：`grounding/shield.py`に`ComplianceShield`を追加（単一ゾーン制約`gt:PalletSingleLocationShape`＋任意のゾーン容量制約`gt:ZoneCapacityShape`を1回のベクトル化ロールアウトで同時検証）。EXP-06専用の`RestrictedZoneShield`はそのまま残し、変更後もEXP-06 smoke再実行でshield有り0件/無し3件を再確認（回帰なし）。危険物隣接制約（`gt:HazmatAdjacencyShape`）はαに危険物クラス予測ヘッドが無いため対象外（EXP-06と同じ簡略化理由）。
- **ダッシュボードの簡略化**：「εの推移」は本来1エピソード内の時系列だが、現状`gtwm ground run`は1回の実行につきホライズンごとに1つの集計値しか出さないため、エピソード横断（実行ごとに1点）を時系列の代替軸として使う（画面内にその旨のcaptionを表示）。「将来違反の予兆」は台帳のopenかつseverity high/mediumの一覧に留め、`ComplianceShield`によるリアルタイム先読みの常時ジョブは未接続（将来セッションへ申し送り）。NL→WHAT-IF変換（LLM経由）はllm.md/session09の範囲でありUIには組み込んでいない。
- **UI検証方法**：Streamlit公式のヘッドレステストAPI（`streamlit.testing.v1.AppTest`）で4タブすべてを1回のスクリプト実行で検証（`st.tabs`は選択タブに関わらず全タブのコードが毎回実行されるため、1回の`at.run()`で全画面をカバーできる）。`at.exception`が空であることを確認し、さらに乖離台帳タブの確認（confirm）ボタンを実際にクリックして`open→confirmed`遷移とフィードバックJSONL（`runs/ui_feedback.jsonl`）への追記が動くことも確認した。`curl`によるHTTP到達性確認（200 OK）も別途実施したが、これは静的シェルの到達確認に過ぎずスクリプト実行の検証にはならない点に注意（AppTestが本体の検証手段）。

## 既知の制約・記録（追加、着手順7）
- 行動空間のエンティティ×プロパティへの意味付けが無い（上記参照）。ADR候補として次セッションで判断する。

## 着手順8（P1相当：realism、EXP-04/05/11）の実装メモ（2026-09-13）

- **一般ノイズと注入の分離**：`sim/realism.py`（`RealismConfig`：アンカー時刻ジッタ±100〜300ms・欠落率・記録の遅延/欠落/誤登録率、`t_true`は変更せず`t_obs`のみ乱す）と`sim/wms_mock.py`の`InjectionConfig`（5型の狙った異常）を独立した軸として実装。`configs/realism/p1.yaml`（学習・評価共通の一般ノイズ＋注入あり、p1_eval用）と`configs/realism/p1_train.yaml`（同じ一般ノイズだが注入0件、p1_train用＝学習データに意図的な異常を混ぜない）の2ファイルを用意。`gtwm sim gen --realism <path>`で有効化、未指定なら従来通りP0/smoke挙動は不変。
- **ドリフト注入**：`sim/drift.py`。物理経路（作業者/AGVは固定スプライン・ウェイポイント巡回）は`wms_mock.generate_orders()`の割当と無関係（`generate.py`から一度も呼ばれていないことを確認済み）なため、真の物理ディスパッチ変更ではなく「業務記録（EPCIS記録相当）の期待プロセス定義が変わった」という記録レベルの変換として実装（検品位置変更＝inspecting記録のzoneを書き換え、ピッキング順序変更＝picking記録の個体対応を時刻順で反転）。スコープの限定を`drift.py`冒頭に明記。
- **p1_train/p1_eval 生成実績**：p1_train（5分×20本、realism有・注入無、seed 1000始まり）とp1_eval（5分×10本、realism+注入、seed 2000始まり）を実機生成。バックグラウンド実行（`nohup`、`runs/p1_data_gen/log.txt`）、合計約32分（見積もり36分と近い）。p1_eval全体で注入180件（5型×36件、目標50件以上・目標80件以上を達成）。
- **実バグを3件発見・修正**（いずれもEXP-05を実データで検証して発覚。smokeの数値を鵜呑みにせず実際に動かして確認した結果）：
  1. `eval/scoring.score_detection`が`detected_at`（`ledger.py`が`entry.detected_at.isoformat()`で保存するISO日時文字列）を`float(str(...))`でパースしようとして常に例外→`dt=0.0`にフォールバックし、時刻許容判定`time_tolerance_s`が実質無効化されていた（既存テストが`detected_at`にベタのfloatを与えていたため発見されていなかった）。`datetime.fromisoformat(...).timestamp()`で修正し、実際のISO文字列を使う回帰テストと、時刻超過で誤報扱いになる新規テストケースを追加。
  2. `sim/wms_mock.apply_injections`が注入台帳の`entity`列にsim名（例：`pallet:5`）を入れていたが、`score_detection`は台帳の`object_id`（常に`gt:`形式）と突き合わせる設計だったため、原理的に一致しえなかった（`test_eval_scoring.py`の既存フィクスチャは全て`gt:`形式を使っており、これが正しい規約だったと確認）。5型すべてで`entity_gt_id`を使うよう修正。
  3. **最も本質的な欠落**：`ground_run.py`の唯一の乖離検知ロジックはWMロールアウトが予測する未来ゾーンの不安定性という物理内部の一貫性チェックのみで、`events.parquet`（業務記録）を一切参照していなかった。そのため注入5型（いずれも記録側の改変）を原理的に検知できなかった（修正1・2適用後もdetection_rate=0.0のままだったことで発覚）。新規`grounding/record_consistency.py`で記録(`events.parquet`)と物理真値(`poses.parquet`)を直接突き合わせる検知を追加（late_registration・wrong_slot・ghost_stockの3/5型を検知可能。wrong_scanは個体再識別が必要、unscanned_moveは「スキャンされない正常な物理移動」との区別ができず平常時誤報が多発することを試作で確認したため見送り、両方とも既知の限界として明記）。修正後、EXP-05 smokeのdetection_rateは0.0→0.567（17/30、検知可能な3/5型の上限に近い）に改善。`detected_at`は記録の`t_true`ではなく`t_obs`（記録が実際に登録された時刻）を使う（`t_true`だと検知遅延が定義上ゼロになってしまうため）。
  4. なお`events.parquet`/`poses.parquet`は注入後の（改変済みの）業務記録・物理真値そのものであり、`gtwm ground run`が最初からアクセスできる正規のデータである。盲検境界の対象は「どの行が注入か」を記録した`data/injections/<episode>.parquet`という答え合わせ用の台帳だけであり、これは`eval/scoring.py`しか読まない（`tests/unit/test_blindness.py`で検証、`record_consistency.py`追加後も緑）。
- **realism有無でのε_60s比較（重要な発見）**：90秒エピソード・同一seed=5001・smoke probe設定で実測した結果、realism ON/OFF で ε_60s は**完全に同一の値**（0.3515151515151509、n=99）になった。誤差の範囲内の「有意差なし」ではなく、ビット単位で同一の浮動小数点値であり、原因を追跡したところ構造的な理由が判明した：`consistency.compute_epsilon`のF^h（業務プロセスの遷移）は`expected_future_zone_idx = s.perceived_zone_idx`という恒等写像（＝WM自身の知覚ゾーンをそのままh秒後の期待値とする）で実装されており、`events.parquet`（realismが唯一変更する対象）を一切参照しない。そのためrealism設定（アンカージッタ・記録遅延・注入等、すべて`events.parquet`側の変換）はε計算に数学的に影響し得ない。これはEXP-05で発見・修正した欠落（`ground_run.py`の乖離検知ロジックも当初`events.parquet`を見ていなかった）と同じ根本原因の別箇所での再発であり、session05の申し送り「将来`wms_mock.generate_orders`の割当を使った真のF^hに置き換える」がまさにこの箇所を指す。今回はEXP-05のdetection_rate=0（ゼロ）ほど致命的ではなく（ε自体は依然WM内部の一貫性指標として機能しており、EXP-04のドリフト検知はF^hを介さず「知覚ゾーンの時間変化」を直接見ているため実際に機能している）、かつconsistency.pyの中核セマンティクス変更はEXP-04の再検証も必要になる大きめの変更のため、本セッションでは修正せず、次セッションへの申し送り事項として記録するに留める：**F^hを`events.parquet`の直近の記録ゾーンを参照するよう改修すれば、ε はrealism/注入に反応するようになるはずである**。
- **EXP-04/05/11 の smoke 所要時間**：181s（EXP-04）、90〜99s（EXP-05）、139s（EXP-11）、いずれも5分予算に十分な余裕。EXP-04は当初n_baseline=n_drift=3で290.8sと5分予算に対し危険なマージンだったため、n=2+2に縮小して181sに短縮した。
- **EXP-11の簡略化**：E2E遅延はバッチ実装（全フレーム処理後に1回だけ`store.add_beliefs`）の「フレーム読込開始→コミット完了」の経過時間なので、ストリーミング実装より悲観的な上限になる（smoke実測 p50≈7.16s, p95≈11.89s、目標1.0秒に対しては未達だが、バッチ設計に起因する誠実な数値であり水増ししていない）。稼働率はデプロイされたサービスが無いと本来測れない（全フェーズシミュレーションのPoCでは対象外）ため、パイプラインの完走率を代理指標として報告（smoke: 3/3=1.0）。月額コストは実測生成速度からのナラティブな概算（$180/区画/月、閾値なしの`report_only`）。

## 着手順9（概念発見・LLMクライアント、一部）の実装メモ（2026-09-13）

- **LLM クライアント（`gtwm.llm`）**：`LLMClient` が anthropic/openai/gemini/mock の4プロバイダを
  `configs/llm.yaml`（タスク→provider/model の唯一の置き場、モデル名はプレースホルダーで
  実在未確認）で切替。SQLite キャッシュ（(provider,model,prompt hash,schema hash)キー）、
  `runs/llm_usage.jsonl` への使用量記録（コスト不明時はnull、推定しない）、
  `LLM_MONTHLY_BUDGET_USD` 超過時の呼出拒否、失敗時の自動フォールバック無し、を実装。
  `gtwm llm ping`：`.env` が無い開発機では anthropic/openai/gemini=NG（未設定）、mock=OK を
  クラッシュせず報告することを確認済み。テストは `configs/llm_mock.yaml`（全タスクmock固定）
  のみ使用し実課金は一切発生しない。
- **概念発見（`grounding/concept_discovery.py`）**：予測残差収集（1ステップ先の
  `Dynamics.rollout` と実際の次フレームエンコード結果の差、LLM不使用）→ HDBSCAN
  クラスタリング（`allow_single_cluster=True`。既定では「データ全体が単一クラスタ」を
  許さないHDBSCANの仕様により、支配的な残差パターンが1つしか無い場合に全件ノイズ扱いに
  なることを実データで確認したため必須）→ 説明可能クラスタの除外 → 上位クラスタのみ
  `gtwm.llm` の concept_naming タスクで命名 → `gt:ConceptCandidate`（reviewStatus=pending）
  としてKGに保存（オントロジーへの自動追加はしない）。
- **重要な実装バグを発見・修正**：当初 `filter_unexplained_clusters` は型ヘッド
  （pallet/case/agv/worker/equipment/noneの6分類）の平均確信度だけで「既存記号で
  説明済みか」を判定していたが、実データ（p2_concept、後述）で検証したところ
  27クラスタ全てが型確信度0.6以上となり **候補が0件** になった。原因：積み重ねケースの
  上段（oversized_cargo_proxy）は型としては依然「case」に高確信度で分類されるため、
  型確信度だけでは「荷姿として未登録」という概念的新規性を捉えられない。修正：
  クラスタの平均予測残差ノルムが全クラスタの中央値以上（相対基準、固定値をコードに
  書かない）のクラスタも対象に加える（型不明という信号と、動態予測が苦手という信号の
  OR）。修正後、実データで5候補が生成され、EXP-08 で3種中3種が命中した。
- **EXP-08 向け概念注入（`sim/concept_injection.py`、新規）**：poc_plan.md 6.2 の
  「新しい荷姿・工程・置き場運用」3種を実装：
  1. `oversized_cargo_proxy`：新形状を作る代わりに、既存倉庫に元からある3段積み
     ケースの上段（`generate_mjcf.py`の`gen_objects()`が生成する case_id 1〜20 の
     偶数番）を「未登録の荷姿」の代理として使う（捏造ではなく実在する物理的差異）。
  2. `reinspecting_step`：新規CBV bizStep（未登録）を確率的に追加発行。当初
     "inspecting" の後段として設計したが、**これまで生成した48エピソード全てで
     "inspecting" bizStepが一度も発生していない**ことを実データ確認で発見
     （`sim/sensors/anchors.py`の`SCALE_POS`固定座標が現在の物体配置ロジックでは
     実質到達不能という、session02由来の既存の制約）。"storing"記録の後段に変更して解決。
  3. `staging_overflow`：物理配置を強制変更せず、本来滞留を想定しないゾーンに
     既に自然発生的に長時間留まっている個体を事後タグ付け。
  いずれも `data/injections/<episode>.parquet` に session08 の乖離注入と**同じ
  スキーマ**で書き込み、`eval/scoring.py`しか読まない（盲検境界、`test_blindness.py`で
  検証）。`sim/generate.py`の`GenConfig`にオプトインの`concept_injections`フィールドとして
  追加（既定None、既存呼出箇所の挙動は不変）。
- **EXP-08の出現率スコアリング（`eval/scoring.score_concept_discovery`）**：候補クラスタは
  フレーム単位のスロットインデックスのみを持ち、個体永続IDを経ていない（同一性解決前の
  生残差）ため、注入との厳密な個体対応付けはできない。代わりに候補メンバーの観測時刻
  （episode_id, frame_idx）と注入イベント時刻`t_true`が時間的に近接するか
  （既定5秒以内）で「命中」とする代理指標を採用（本実行に向けたフォローアップ：
  同一性解決を経た上でentity_gt_idベースの厳密照合に置き換えられると望ましい）。
- **EXP-08 smoke実測**：`p2_concept`（30秒×2エピソード、seed=5000、3種注入全て有効）で
  n_candidates=5、injected_concept_top5_hit=3/3種（目標2種以上、ただしsmokeなので
  判定は「参考」に留める）。所要時間103〜113秒、5分予算に十分な余裕。
- **「候補の説明の妥当性（評価者3名の一致率）」は本セッションでは測定しない**：
  poc_plan.md 6.2が要求する盲検の人手評価3名分は、EXP-10と同様Claude Codeでは代替できない
  ため、`experiments/EXP-08/README.md`に人手評価が必要な旨を明記した（手順書は本実行時に
  `docs/results/EXP-08_protocol.md`として別途用意する）。
- **未着手（次セッションへの申し送り）**：反実仮想の忠実性（EXP-07）とEXP-10準備
  （docs/prompts.mdセッション11）。

## 着手順9（P2相当：2拠点連合、EXP-09）の実装メモ（2026-09-13）

- **EDC ではなく最小 HTTP コネクタ（ADR-0002）**：Eclipse Dataspace Components は Java の
  コントロール/データプレーン2プロセス構成＋Postgres/Vault相当が必要で、この PoC の作業
  予算（1日）を明確に超えると判断した。`src/gtwm/dataspace/`（policy.py=ODRL相当の目的限定・
  保持期間・再共有禁止検査、audit.py=SQLite監査台帳、models.py=交換データモデル、
  connector.py=コアロジック、server.py=標準ライブラリのみのHTTPラッパー、leakage.py=
  漏洩評価）を実装した。詳細は `docs/adr/0002-minimal-http-connector-instead-of-edc.md`。
- **交換データの型的制約**：`ExchangeBelief`/`PredictionRecord`（pydantic、
  `extra="forbid"`）には生映像・潜在フィールドが型として存在しない。追加しようとすると
  `ValidationError` になることを `tests/unit/test_dataspace_models.py` で確認済み
  （poc_plan.md 5.5「生映像・潜在表現は交換しない」の型レベルでの強制）。
- **docker-compose profile p2**：site_a/site_b を別 Docker ネットワークに配置した2コネクタ
  コンテナ（`docker/dataspace/Dockerfile`、`python:3.11-slim`、arm64、torch/mujoco/rdflib
  等の重い extras は一切インストールしない自己完結ビルドで4秒程度）。Oxigraph と異なり
  python:3.11-slim にはシェル/pythonが両方あるため、実際にコンテナ側 `HEALTHCHECK` を
  書けた（infra.md の「全サービスに healthcheck」を満たす）。`make up-p2`/`make down-p2`
  を追加。実機で healthy を確認し、`curl` で実際に `/exchange` を叩いて許可応答・
  ポリシー違反拒否（監査ログへの記録含む）の両方をライブで確認済み（コード上のテストだけ
  でなく Docker 越しの実通信で確認）。
- **実バグを2件、実機検証中に発見・修正**：
  1. `ThreadingHTTPServer` はリクエストごとに別スレッドを立てるが、`AuditLog` の
     sqlite3コネクションが `check_same_thread=True`（既定）のままだったため、2件目の
     リクエストで `ProgrammingError` が発生しサーバが落ちた（`curl` で2回目のリクエストを
     送って実際に再現）。`check_same_thread=False` + `threading.Lock` で修正。
  2. `experiments/eval/experiments/exp09.py` で、site_b が予測を発行した**後**の時刻を
     交換要求の `since`（下限）に渡していたため、`Connector` の
     `generated_at >= since` フィルタで自分が今publishした予測が常に0件除外されていた
     （`n_predictions_exchanged` が常に0になる実バグ、smoke実行で発覚）。予測発行**前**の
     時刻を `since` に使うよう修正し、`tests/unit/test_dataspace_connector.py` に
     「`since` が対象データより後なら除外される」ケースを回帰テストとして追加した。
- **EXP-09 の簡略化**：
  - 「模擬第二拠点」は同一レイアウトを別 seed（`site_b_seed=9000+seed`）で生成した
    `p2_site_b` データセット（docs/prompts.md セッション10自身が「別レイアウトである
    必要はない」と明記）。
  - `PredictionRecord` は poc_plan.md 5.5 の交換単位定義（値・区間・ホライズン・モデル版）
    通り確信度フィールドを持たないため、受領側 ECE は「全予測を確信度1.0として送った体」
    で計算する簡略化（ECE＝誤り率になる）。本実行では確信度を伴う交換スキーマへの拡張が
    必要（次セッションへの申し送り）。
  - 復元攻撃はフレーム全体の融合潜在（Kスロット平均）→16×16縮小画像の線形デコーダ、
    再識別攻撃は識別済みワーカースロット潜在→3クラス線形分類器（いずれも smoke 規模に
    見合う最小構成）。
- **EXP-09 smoke の実測結果**（`runs/EXP-09/20260913-103010`、104〜115秒、5分予算に余裕）：
  `reconstruction_ssim≈0.123`（目標0.30以下を満たす＝復元攻撃は低品質）、
  `reid_top1_vs_chance=3.0`（目標1.2倍以下を大きく超過＝smoke規模ではワーカー3人を
  線形分類器が容易に判別できてしまう。ただしサンプル数が18件と極小なため smoke 特有の
  過学習の可能性が高く、本実行でエピソード数・フレーム数を増やして再評価が必要）、
  `ece=1.0`（上記の確信度簡略化により accuracy=0 のときの理論値と一致。α のゾーン予測
  精度そのものの問題か、確信度簡略化の副作用かを本実行で切り分ける必要がある）、
  `n_policy_violations=1`（意図した違反1件が正しく拒否・記録された）。
- `make lint test`（214 unit tests）・`test_blindness.py` は引き続き緑。
