# Claude Code 開発プロンプト集（gtwm PoC）

CLAUDE.md と `.claude/rules/` は自動で読み込まれるので、プロンプトには「そのセッションで何を、どこまで、どう確認するか」だけを書く。各プロンプトはそのままターミナルの Claude Code に貼り付ける。

## 使い方の原則

1. 1セッション1タスク。タスクが終わったら `/session-end` で締め、次は `/clear` してから始める（前タスクの文脈を引きずらない）。
2. 実装タスクは必ずプランモード（Shift+Tab で切替）で始め、計画を読んでから承認する。計画に「対象ファイル・テスト・受入基準・所要時間」が無ければ差し戻す。
3. 受入基準は数値で書く。プロンプトの受入基準は計画書（`docs/poc_plan.md`）と `experiments/criteria.yaml` の値に合わせ、閾値をプロンプトで緩めない。
4. 会話が長くなったら `/compact`。CLAUDE.md は compact 後も再読込されるが、会話中に出した指示は消えるので、残したい指示は CLAUDE.md か `docs/status.md` に書かせる。
5. Claude Code の報告を鵜呑みにしない。`make lint test` の結果と `git diff --stat` を自分でも見る。実験の判定は `docs/results/EXP-xx.md` の表で確認する。
6. 10分を超えるジョブは Claude Code にバックグラウンド起動させ、自分は別セッションで次の作業を進めてよい（`tail -n 50 runs/<name>/log.txt` で確認）。
7. 迷ったら `/adr` で決定を記録してから進める。指標・合格基準・名前空間・技術選定の変更は ADR なしに受け入れない。

各プロンプト末尾の「報告」は共通で、次の意味：変更ファイル一覧、実行したコマンドと結果（緑/赤）、受入基準の達成表、判断が必要な点、`docs/status.md` の更新内容。

---

## P0 相当：シミュレーション基盤

### 00 キックオフ（最初のセッション、読み込みと理解の確認だけ）

```
このリポジトリで PoC 開発を始める。まず CLAUDE.md、.claude/rules/ の6ファイル、docs/status.md、docs/poc_plan.md の 2章・3.1・5.1・付録A を読め。
読み終えたら、実装は一切せずに次を報告せよ。
1. このプロジェクトの目的と「接地層」「整合性ギャップ ε」を各2文で説明
2. MacBook 環境のために計画書から変更されている点（CLAUDE.md の絶対条件）を箇条書き
3. 着手順 1〜9 のうち、依存関係上の理由で順序を変えるべきものがあれば指摘
4. 環境確認：python3 --version、uv --version、docker --version、ffmpeg -version、
   python3 -c "import torch; print(torch.backends.mps.is_available())" を実行し結果を表にする
   （uv や ffmpeg が無ければ brew install コマンドを提示するだけで、実行はしない）
5. 不明点・確認したい前提を最大5つ
```

### 01 足場（pyproject / Makefile / CLI / doctor）

```
着手順 1「足場」を実装せよ。プランモードで計画を出してから着手すること。

やること
- pyproject.toml（uv、src レイアウト、Python >=3.11,<3.12、optional-dependencies は .claude/rules/infra.md の通り）と uv.lock
- Makefile：CLAUDE.md「コマンド」節の全ターゲット。未実装の機能を呼ぶターゲットは「未実装」と表示して exit 1 する仮実装でよい
- src/gtwm/ のパッケージ骨格（sim, wm, grounding, kg, llm, dataspace, eval, ui, utils）と typer CLI `gtwm`（doctor sim kg wm ground exp whatif llm のサブコマンド。未実装は「未実装」表示）
- gtwm doctor：Python 版、MPS 可用性、mujoco の import とオフスクリーン 1 フレーム描画、ffmpeg、docker、.env の存在、必須環境変数を検査し表で表示
- utils：get_device()、seed_everything()、structlog 設定、Hydra 設定のロード
- ruff / mypy / pre-commit 設定、tests/unit の最初のテスト（device, seed, CLI --help）
- .env.example と .gitignore の内容確認、docs/status.md の 1 にチェック

受入基準
- make setup → make doctor → make lint → make test が全て緑
- uv run gtwm --help にサブコマンド8つが表示される
- tests/unit は合計10秒以内

禁止：sim や wm の中身を先回りして実装しない。brew 依存は uv と ffmpeg 以外に増やさない。

最後に報告し、docs/status.md を更新し、feat: コミットを1つ作れ。
```

### 02 シミュレーション（MuJoCo 倉庫、センサ、アンカー、WMS モック）

```
着手順 2「sim」を実装せよ。.claude/rules/sim.md と docs/poc_plan.md 4.1・5.2「入力」を先に読み、プランモードで計画を出せ。

やること
- sim/assets/warehouse.xml と include：12m×8m、ゾーン6つ、棚4本（slot サイト付き）、ドック2、コンベア1、AGV 2台、作業者3体、パレット20＋ケース40、固定カメラ4台。registry.yaml で個体 ID を管理
- src/gtwm/sim/：env.py（ステップ、10Hz ログ）、render.py（RGB/セグメント/深度、遮蔽率）、sensors/anchors.py（RFID・スキャン・秤・扉・PLC）、wms_mock.py（オーダー、割当、期待プロセス F、EPCIS 2.0 JSON-LD 発行、遅延/欠落/誤登録の注入ノブ）、scenarios.py（制約からのシナリオ列挙、ドメインランダム化）、writer.py（sim.md のデータ書出仕様）
- gtwm sim gen --set <name> --episodes N --duration S --seed K、make sim-smoke（30秒）
- tests/sim：決定性（同一 seed で poses 一致）、アンカー検出のユニットテスト、書出ファイルの存在と形状

受入基準
- make sim-smoke が data/sim/smoke/<episode>/ に cam_*.mp4・masks.npz・depth.npz・poses.parquet・occlusion.npz・events.parquet・meta.json を出力する
- 30秒エピソードの生成時間を計測して報告し、実時間の2倍以内。超える場合は物体数を減らして再計測（解像度は下げない）
- make test-sim が緑（直列実行）。GUI ウィンドウは開かない
- events.parquet に真値時刻（t_true）と観測時刻（t_obs）の両列があり、smoke では等しい

禁止：mujoco.viewer を呼ぶコードをデータ生成経路に入れない。EGL/OSMesa を前提にしない。

報告し、docs/status.md を更新し、コミットせよ。生成に5分以上かかるなら途中経過を先に報告すること。
```

### 03 オントロジーと KG

```
着手順 3「kg」を実装せよ。.claude/rules/ontology.md と docs/poc_plan.md 5.3・付録B を先に読み、プランモードで計画を出せ。

やること
- ontology/gt-core.ttl：計画書 5.3 の PoC 拡張表のクラス・プロパティを全て定義（rdfs:label ja/en、rdfs:comment 付き）
- ontology/vendor/：EPCIS 2.0・CBV・SOSA/SSN・PROV-O・BFO の公式 TTL/JSON-LD を取得して配置し、LICENSES.md に出典 URL と条件を書く。取得できないものは最小スタブを作り、その旨を LICENSES.md と docs/status.md に明記
- ontology/shapes/：単一ゾーン、危険物隣接禁止、ゾーン容量の3本（sh:Warning で開始）
- src/gtwm/kg/：store.py（KGStore：rdflib 実装と Oxigraph 実装、add_beliefs/query/validate/snapshot）、schema.py（Belief の pydantic モデルと RDF-star 変換）、epcis.py（WMS モックの JSON-LD → record 出所の信念）、queries/*.rq
- gtwm kg validate（ttl の構文、SHACL の自己整合、queries の構文）
- docker-compose.yml の profile core に oxigraph（arm64、:7878、healthcheck）。他のサービスはまだ入れない
- tests：shapes の適合/違反グラフ、EPCIS 取込、snapshot(t) のバイテンポラル挙動、Oxigraph 経由の統合テスト（integration マーカー）

受入基準
- make test 緑、make up → make test-int 緑
- data/sim/smoke の events.parquet から生成した信念が KGStore に入り、SPARQL で「時刻 t のパレット位置」が引ける
- gtwm kg validate が緑で、pre-commit に組み込まれている

禁止：SPARQL をコードに文字列で埋め込まない。gt: 名前空間を変えない。

報告し、docs/status.md を更新し、コミットせよ。
```

### 04 世界モデル

```
着手順 4「wm」を実装せよ。.claude/rules/world_model.md と docs/poc_plan.md 5.2 を先に読み、プランモードで計画を出せ。

やること
- src/gtwm/wm/：encoder.py（facebook/dinov2-small 凍結＋アダプタ、HF キャッシュ）、slots.py（slot attention K=16 D=128、型ヘッド6クラス）、fusion.py（カメラ→床面座標統合、MJCF からカメラ行列を読む）、dynamics.py（Transformer 4層 d=256、条件トークン・行動トークン、アンサンブル3）、planner.py（MPPI の骨格。シールドは 05 で接続）
- configs/wm/base.yaml と smoke.yaml、学習ループ train.py（Hydra、MLflow、チェックポイント、早期終了）
- データローダ：data/sim/<set> を読み、フレーム間引きとシーケンス切出し。学習/評価は時系列順に分割
- make train-smoke（3分以内）、gtwm wm train / gtwm wm rollout
- tests/unit：形状テスト（CPU、極小次元）、合成系列（等速直線、静止＋遮蔽）の数値テスト

受入基準
- make train-smoke が3分以内に終わり、mlruns/ に損失曲線が記録される
- 合成系列テストで 10 ステップ先の潜在予測誤差が閾値（config に定義）以下
- MPS fallback 警告が出た op を docs/status.md に列挙
- 1プロセスのピークメモリを計測して報告（12GB 以内）

禁止：bf16 を使わない。torch.hub 経由のダウンロードに依存しない（transformers のキャッシュを使う）。学習ループに LLM を入れない。

報告し、docs/status.md を更新し、コミットせよ。smoke の前に data/sim/p0_train（5分×10本）を生成する必要があれば、まず所要時間を見積もって確認を取れ。
```

### 05 接地層（α / γ / ε / 同一性 / 乖離台帳）

```
着手順 5「grounding」を実装せよ。.claude/rules/world_model.md の「接地層の実装規則」と docs/poc_plan.md 2.2・5.4 を先に読み、プランモードで計画を出せ。

やること
- probes.py（α）：存在・型・位置（ゾーン分類＋床面座標）・状態・関係（集約）のヘッド、温度スケーリング較正、Belief 出力
- conditioning.py（γ）：KGSubgraph → R-GCN 埋め込み（256次元）と、テキスト直列化＋小型エンコーダの2実装。設定で切替
- consistency.py（ε）：configs/grounding/epsilon.yaml の重みで d を計算し、h ∈ {10s, 60s, 5min, 30min} の ε_h と分解（知覚/プロセス逸脱/オントロジー欠落）を EpsilonRecord で返す。KGStore.snapshot(t) と WMS モックの F を使う
- constraints.py：product t-norm の微分可能制約損失（SHACL 3本に対応）
- identity.py：ハンガリアン割当、遮蔽中の予測位置保持、アンカーでの強制再同定
- ledger.py：計画書 5.4 の台帳スキーマ（SQLite）、状態遷移 open→confirmed/dismissed→resolved
- 学習への接続：アンカー接地損失と制約損失を wm/train.py の loss_weights に組み込む
- gtwm ground run --episode <id>：エピソードを流して信念を KG に入れ、ε を時系列で記録し、台帳エントリを作る
- tests/unit/test_blindness.py：grounding/ と wm/ が data/injections と wms_mock.injections を import していないことを検査

受入基準
- smoke データで α の位置事実 F1 ≥ 0.6（最低ライン。目標 0.90 は EXP-01 で評価）
- ECE を出力し、較正前後の値を報告
- gtwm ground run が smoke エピソードで完走し、ε_h の時系列が runs/ に保存される
- make lint test 緑、test_blindness が緑

禁止：ε の重み・τ をコードに書かない。台帳の自動解決（人の確認なし）を実装しない。

報告し、docs/status.md を更新し、コミットせよ。
```

### 06 評価基盤と最初の4実験

```
着手順 6「eval」を実装し、EXP-01/02/03/06 を smoke で通せ。.claude/rules/experiments.md と docs/poc_plan.md 3.1・3.2・6.2・付録A を先に読み、プランモードで計画を出せ。

やること
- experiments/criteria.yaml：計画書 3.1 の全仮説の指標名と目標値を転記（数値を変えない）
- src/gtwm/eval/metrics.py：付録A の関数を全て実装し、docstring に数式、tests/unit/test_metrics.py に手計算例
- stats.py：paired_bootstrap_ci、wilson_interval、effective_horizon の補助
- runner.py：config → 実行 → runs/EXP-xx/<ts>/{metrics.json, report.md, config_resolved.yaml, git.txt, log.txt}、MLflow 記録、seed 3本
- scoring.py：注入台帳を読んでよい唯一のモジュール
- experiments/EXP-01, 02, 03, 06 の README.md（計画書 6.2 を転記）、config.yaml（smoke: true 対応）、run.py
- make exp EXP=EXP-01 SMOKE=1 が動く
- docs/results/ のテンプレートと自動生成部分

受入基準
- 4実験の smoke がそれぞれ5分以内に完走し、metrics.json に criteria.yaml の指標が全て入る
- test_metrics が緑（手計算例と一致）
- report.md に「参考（smoke）」の判定と、本実行に必要なデータ量・推定時間が書かれる

禁止：criteria.yaml の値をコードで上書きしない。smoke の結果で合否を書かない。

報告し、docs/status.md の実験表を更新し、コミットせよ。本実行（seed 3本）はこのセッションでは開始しない。
```

### 07 WHAT-IF エンジンとダッシュボード

```
着手順 7「whatif + ui」を実装せよ。docs/poc_plan.md 5.6・付録C と .claude/rules/ontology.md「EPCIS と WHAT-IF」を先に読み、プランモードで計画を出せ。

やること
- src/gtwm/kg/whatif/grammar.lark（付録C の BNF と一致）、parser.py、compiler.py（個体→潜在スロット/行動変数の解決、失敗時はシミュレーション代替を提案する例外）、engine.py（ロールアウト → α で記号化 → kpi_*.rq で集計 → 点推定と予測区間）
- gtwm whatif "PREDICT ?queue_len AT +10min WHERE station = gt:Station_S3 GIVEN do(gt:Conveyor_C1.speed := 1.2 * current) SAMPLES 50 INTERVAL 0.9"
- planner.py にシールド（候補軌道を α で記号化し SHACL 違反を除外）を接続し、EXP-06 から使えるようにする
- src/gtwm/ui/app.py（Streamlit）：乖離台帳（一覧・確認/却下・訂正入力）、ε の推移（ホライズン別、管理限界）、将来違反の予兆、WHAT-IF 実行フォーム。訂正入力はフィードバック（再学習ラベル候補・オントロジー修正候補）として保存
- tests：文法の受理/拒否例20件、コンパイルの解決失敗、engine の smoke

受入基準
- 付録C の例文3つが解析・実行でき、結果に点推定・区間・モデル版・ロールアウト数が含まれる
- make dashboard で 4 画面が表示され、台帳の状態遷移が UI から行える
- EXP-06 の smoke でシールド有の違反数が 0

禁止：文法を変える時は付録C を先に変更する。UI に LLM を組み込まない（NL→WHAT-IF 変換は 09 以降で gtwm.llm 経由）。

報告し、docs/status.md を更新し、コミットせよ。
```

---

## P1 相当：現実らしさの注入と乖離検知

### 08 realism 設定と EXP-04/05/11

```
着手順 8「P1 相当」を実装せよ。docs/poc_plan.md 3.2 の H3・H4・N1、6.2 の EXP-04/05/11、.claude/rules/sim.md「WMS モックと EPCIS」を先に読み、プランモードで計画を出せ。

やること
- configs/realism/p1.yaml：センサノイズ、アンカー時刻ジッタ（±100〜300ms）、欠落率、遮蔽の追加（棚裏通過・積み重ね・作業者による隠蔽の頻度）、イベント遅延・欠落・誤登録の率。writer.py で t_obs にジッタを適用し、t_true は保持
- 注入：unscanned_move / wrong_slot / wrong_scan / late_registration / ghost_stock を各10件以上（合計50件以上、可能なら80件）、時間帯と対象を injection_seed で無作為化し data/injections/ に台帳を書く
- ドリフト注入：検品位置の変更、ピッキング順序の変更を各3エピソード相当
- data/sim/p1_train（5分×20本）と p1_eval（5分×10本、注入入り）を生成。所要時間を先に見積もって確認を取る
- EXP-04（ε の平常分布と管理限界、ドリフト検知 AUROC）、EXP-05（乖離検知率・誤報/日・検知遅延、盲検）、EXP-11（E2E 遅延 p50/p95、メモリ、生成・学習・推論の時間）を experiments/ に追加し、smoke を通す

受入基準
- 3実験の smoke が5分以内で完走
- test_blindness が引き続き緑（検知側が台帳を読んでいない）
- realism 有/無で ε_60s の平常分布が変わることを smoke で確認し、数値を報告

禁止：注入の seed と実験の seed を共有しない。検知率を上げるために注入の種類を減らさない。

報告し、docs/status.md を更新し、コミットせよ。本実行はセッションを分けて /exp-run で行う。
```

---

## P2 相当：概念発見、連合、反実仮想

### 09 概念発見と LLM クライアント

```
着手順 9 のうち「概念発見」と gtwm.llm を実装せよ。.claude/rules/llm.md と docs/poc_plan.md 3.2 の H7・6.2 の EXP-08 を先に読み、プランモードで計画を出せ。

やること
- src/gtwm/llm/client.py：anthropic / openai / gemini / mock、configs/llm.yaml（tasks.<task>.provider/model、pricing）、SQLite キャッシュ、runs/llm_usage.jsonl、LLM_MONTHLY_BUDGET_USD の上限、gtwm llm ping / usage
- prompts/concept_naming.md、prompts/nl_to_whatif.md、prompts/episode_qa.md をファイル化
- src/gtwm/grounding/concept_discovery.py：予測残差の収集 → HDBSCAN クラスタリング → 既存記号で説明できるクラスタの除外 → 上位クラスタを LLM で命名・説明（JSON、pydantic 検証）→ gt:ConceptCandidate として KG に保存（reviewStatus=pending）
- 注入：新しい荷姿（長尺物）、新工程（再検品）、新しい一時置き場運用をオントロジー未登録のまま p2_concept セットに入れる
- EXP-08 を experiments/ に追加（候補上位5への出現率、盲検の判定手順を README に）
- tests は mock プロバイダのみ。実プロバイダは gtwm llm ping で人が確認する

受入基準
- gtwm llm ping が設定した3プロバイダで疎通し、モデルの実在を確認できる（結果は人が見て承認）
- EXP-08 の smoke で候補が生成され、KG に ConceptCandidate が入る
- llm_usage.jsonl に task・トークン数・費用が記録される。テスト実行で課金が発生しない

禁止：オントロジー（ttl）への自動追加。SDK を gtwm.llm 以外から import しない。モデル名をコードに書かない。

報告し、docs/status.md を更新し、コミットせよ。
```

### 10 2拠点連合（データ空間）

```
着手順 9 のうち「2拠点連合」を実装せよ。docs/poc_plan.md 5.5・3.2 の H8・6.2 の EXP-09 と .claude/rules/infra.md を先に読み、プランモードで計画を出せ。EDC の構築が1日を超えそうなら、ODRL ポリシーを強制する最小 HTTP コネクタを代替として実装し、その判断を /adr で記録すること。

やること
- docker-compose.yml の profile p2：拠点 A/B のネットワーク分離、各拠点のコネクタ、交換用の最小 API
- src/gtwm/dataspace/：交換データモデル（gt:Belief と予測に PROV-O 出所を付与）、ODRL ポリシー（目的限定・保持期間・再共有禁止）、監査ログ
- 拠点 B は sim を別設定（レイアウト違い、seed 違い）で動かし、到着予定と在庫状態の予測を交換
- 漏洩評価：潜在表現を共有した場合の復元攻撃（潜在→画像デコーダの学習、SSIM/PSNR）と匿名作業者の再識別攻撃（Top-1 vs チャンス率）
- EXP-09 を experiments/ に追加（ECE、ポリシー違反の監査ログ件数、SSIM、再識別精度）

受入基準
- make up-p2 相当で2拠点が起動し、ポリシーに反する要求（目的外利用）が拒否され監査ログに残る
- EXP-09 の smoke が完走し、受領側 ECE と漏洩評価の値が metrics.json に入る
- 生映像・潜在は既定で交換されない（交換されるのは信念と予測のみ）ことをテストで固定

禁止：amd64 イメージを黙って使わない（使うなら理由をコメントと status.md に）。

報告し、docs/status.md を更新し、コミットせよ。
```

### 11 反実仮想の忠実性（EXP-07）と EXP-10 の準備

```
EXP-07 と EXP-10 の準備を実装せよ。docs/poc_plan.md 3.2 の H6・H9、6.2 の EXP-07/10 を先に読み、プランモードで計画を出せ。

やること
- 介入3種（コンベア速度、担当人数、一時置き場位置）をシミュレーションの設定として実装し、介入前に WHAT-IF で予測 KPI と 90% 区間を記録 → 介入を適用した sim を実行して実測 KPI を得る手順を runner に組み込む
- EXP-07：相対誤差、区間被覆率、介入ごとの予測–実測プロット
- EXP-10 の準備：模擬例外10件（台帳エントリと証跡）、従来手順を模した「記録と現物の目視突合」画面、解決時間と正答を記録する計測 UI、SUS 質問票の出力。評価そのものは人が行うので、手順書 docs/results/EXP-10_protocol.md を書く

受入基準
- EXP-07 の smoke で3介入すべての予測–実測ペアが出る
- 模擬例外10件が UI に読み込め、解決時間が記録される
- 手順書に、評価者の人数・順序の無作為化・記録項目が書かれている

報告し、docs/status.md を更新し、コミットせよ。
```

---

## 運用プロンプト（繰り返し使う。同名のスキルを .claude/skills/ に用意）

### 実験の本実行（/exp-run）

```
/exp-run EXP-03 full
```
スキルが無い場合の素のプロンプト：
```
EXP-03 を本実行せよ。experiments/EXP-03/README.md と criteria.yaml を読み、必要なデータセットの有無を確認し、無ければ生成時間を見積もって確認を取れ。
seed 3本で nohup によりバックグラウンド起動し、起動コマンド・ログのパス・推定所要時間を報告して終われ。完了を待たない。
```

### 実験レポート（/exp-report）

```
/exp-report EXP-03 runs/EXP-03/20260915-1030
```
素のプロンプト：
```
runs/EXP-03/<ts>/ の metrics.json を読み、docs/results/EXP-03.md を .claude/rules/experiments.md のレポート形式で書け。判定は criteria.yaml と比較して 合格/不合格 の二値（smoke なら 参考）。
不合格の指標には ε の分解（知覚/プロセス逸脱/オントロジー欠落）に基づく原因仮説を最大3つ、それぞれ次に試す変更とその検証方法を書け。閾値は変えない。docs/status.md の実験表を更新し、docs: コミットを作れ。
```

### 不合格時の深掘り

```
EXP-01 が不合格（位置事実 F1 = 0.81、目標 0.90）。原因を切り分けよ。
1. 遮蔽率（0–20 / 20–50 / 50+）・照明・混雑度で層別した F1 を出す
2. アンカー時刻の直前1秒で α の出力と真値の混同行列を作る
3. ε の分解で知覚誤りの寄与を確認する
4. 上記から最も効く改善案を3つ、期待効果と実装コストを付けて提案する。実装はまだしない。
```

### 設計判断の記録（/adr）

```
/adr EDC の代わりに最小 HTTP コネクタを採用する
```
素のプロンプト：
```
docs/adr/ に次の番号で ADR を書け：タイトル、状況、検討した選択肢（少なくとも2つ）、決定、結果と影響（受入基準や計画書への影響を含む）、日付。関連する CLAUDE.md・rules・criteria.yaml の該当箇所も同じコミットで更新せよ。
```

### コードレビュー（/review-diff）

```
/review-diff
```
素のプロンプト：
```
git diff main...HEAD を対象にレビューせよ。観点：CLAUDE.md と .claude/rules の違反、盲検境界の侵害、閾値や重みのハードコード、テストの欠落、MPS で動かない可能性のある op、長時間ジョブのフォアグラウンド実行。指摘は「ファイル:行、問題、修正案」の形式で重要度順に。修正はしない。
```

### デバッグ

```
make test-sim が macOS で失敗する。エラー全文を読んで原因を特定せよ。仮説を立てる前に、レンダリングがメインスレッドか、MUJOCO_GL の値、mujoco の版、Renderer の生成回数を確認せよ。修正は最小限にし、再発防止のテストを追加せよ。
```

```
学習中に MPS の fallback 警告が大量に出て遅い。警告の op を列挙し、置換可能な op（同等の MPS 対応実装）と、CPU に残す op を分け、置換後の速度を計測して報告せよ。docs/status.md の「MPS fallback」欄を更新せよ。
```

```
docker compose up で <service> が起動しない。イメージのアーキテクチャ（arm64 か）、healthcheck、ポート競合を順に確認せよ。amd64 エミュレーションで解決する場合は、それを使う理由と代替案を示してから適用せよ。
```

### 性能プロファイル

```
gtwm sim gen の30秒エピソード生成をプロファイルせよ（cProfile と手動計時）。レンダリング・物理・書出の内訳を出し、実時間比を報告。解像度を下げずに2倍以内にする案を、効果の見積り付きで提案せよ。
```

### 計画書との整合チェック

```
docs/poc_plan.md の 5.1 コンポーネント表と 3.1 仮説表に対し、現在のコードの実装状況を表にせよ（実装済/一部/未着手、対応ファイル、テストの有無）。計画書と実装が食い違っている箇所（名前、指標定義、スキーマ）を列挙し、どちらを直すべきか提案せよ。修正はしない。
```

### リファクタ

```
src/gtwm/grounding/probes.py が 400 行を超えた。挙動を変えずに分割せよ。分割前に既存テストを実行して緑を確認し、分割後も同じテストが緑であること、公開 API（インタフェース契約）が変わっていないことを示せ。
```

### セッション開始（/session-start）

```
/session-start
```
素のプロンプト：
```
docs/status.md と git log --oneline -10 と git status を読み、前回の続きとして今日やるべきタスクを1つ提案せよ。未コミットの変更があれば内容を要約して、コミットするか破棄するか聞け。実装はまだしない。
```

### セッション終了（/session-end）

```
/session-end
```
素のプロンプト：
```
このセッションの成果を締めよ。make lint test を実行し、緑でなければ直すか理由を書く。docs/status.md（チェックリスト、実験表、既知の制約）を更新し、Conventional Commits で1つ以上のコミットを作る。最後に、変更ファイル、実行コマンドと結果、受入基準の達成表、次のセッションでやるべきこと、判断が必要な点を報告せよ。
```

### 記憶させたい指示の永続化

```
今後もこのルールを守ってほしい：「<ルール>」。CLAUDE.md か該当する .claude/rules/*.md のどちらに書くべきか判断して追記し、200行制限を超えないことを確認せよ。
```
