# CLAUDE.md — 世界モデル × オントロジー 知的データ空間 PoC（gtwm）

## 何をするプロジェクトか
物流倉庫1区画を対象に、世界モデル（WM：潜在ツイン）とオントロジー（記号ツイン）を接地層（α：潜在→記号、γ：記号→潜在）で結び、整合性ギャップ ε で物理と記録の乖離を検知する二層ツインを実装・検証する。
計画の正本は `docs/poc_plan.md`。仮説 H1–H9/N1（3章）、実験 EXP-01〜11（6章）、コンポーネント C1–C14（5.1）、指標の数式（付録A）、WHAT-IF 文法（付録C）はそこを読む。本ファイルには転記しない。
パッケージ名・CLI 名は `gtwm`。現在の進捗と次の作業は `docs/status.md`（作業のたびに更新する）。

## 絶対条件（実行環境）
- 実行機は MacBook Pro（Apple Silicon）。GPU は MPS のみ。CUDA・Isaac Sim・EGL は使えない。計画書の「Isaac Sim」は全て MuJoCo に読み替える。
- 全フェーズ（P0/P1/P2）をシミュレーションで行う。実サイト設置・実 WMS 接続は対象外。P1 相当の現実らしさは `configs/realism/*.yaml`（センサノイズ、アンカー時刻ジッタ ±100–300ms、遮蔽、イベント遅延・欠落・誤登録）で注入する。P2 の連合は2つのシミュレーション拠点（別 Docker ネットワーク）で模擬する。EXP-10（人手評価）は人が手動で行う。Claude Code は模擬例外セットと UI だけ用意する。
- GUI ウィンドウを開かない。レンダリングは `mujoco.Renderer` のオフスクリーンのみ。ビューアは人が `mjpython -m gtwm.sim.view` で手動起動する時だけ使う。
- Docker イメージは arm64 を選ぶ。`platform: linux/amd64` はエミュレーションで遅いので最後の手段とし、使う時は理由をコメントに書く。
- LLM（Anthropic / OpenAI / Gemini）は `gtwm.llm` 経由でしか呼ばない。`gtwm/sim`、`gtwm/wm`、`gtwm/grounding` の実行時経路から LLM を呼ぶコードは書かない。
- 計算予算：学習1ランは2時間以内、1プロセスのメモリは12GB以内、各実験に5分以内で終わる smoke モードを必ず用意する。超えそうな設計は縮小案を出してから相談する。

## リポジトリ構成
```
src/gtwm/
  sim/        C1 相当：MJCF 倉庫、センサ、アンカー、WMS モック（EPCIS 2.0）、シナリオ生成、データ書出
  wm/         C2/C3：エンコーダ、スロット、潜在動態、階層フロー、MPPI 計画
  grounding/  C4–C7, C9, C11：probes(α), conditioning(γ), consistency(ε), identity, ledger, concept_discovery
  kg/         C6, C8, C10：KGStore、SHACL、EPCIS 取込、WHAT-IF パーサ/エンジン
  llm/        LLM クライアントと許可タスク
  dataspace/  C12：EDC 連携（P2 まで着手しない）
  eval/       C14：指標、統計、実験ランナー、レポート
  ui/         C13：Streamlit ダッシュボード（乖離台帳、ε、WHAT-IF）
  utils/      device, seed, config, logging
ontology/     gt-core.ttl, shapes/*.ttl, vendor/（EPCIS, CBV, SOSA, PROV-O, BFO のコピーとライセンス）
configs/      Hydra 設定（sim/, wm/, grounding/, realism/, llm.yaml）
experiments/  EXP-xx/{README.md, config.yaml, run.py}, criteria.yaml（合格基準の唯一の置き場）
tests/        unit/（CPU、外部依存なし）, sim/（MuJoCo、直列）, integration/（Docker）
docker/ + docker-compose.yml   profile core（oxigraph, timescaledb, minio, grafana）, profile p2（EDC ×2）
docs/         poc_plan.md, status.md, results/EXP-xx.md, adr/NNNN-*.md
data/ runs/ mlruns/ .data/     生成物。全て gitignore。`gtwm sim gen` で再生成できる状態を保つ
```

## コマンド（Makefile を正とする。無ければ最初に作る）
```
make setup          # brew: uv ffmpeg を確認 → uv sync --all-extras → pre-commit install
make doctor         # gtwm doctor：Python 3.11 / MPS / MuJoCo オフスクリーン / ffmpeg / Docker を検査
make lint           # ruff check + ruff format --check + mypy src
make test           # pytest -m unit -q
make test-sim       # pytest -m sim -q -p no:xdist（レンダリングはメインスレッド、直列）
make test-int       # pytest -m integration（make up 済みが前提）
make up / make down # docker compose --profile core up -d --wait / down
make sim-smoke      # 30秒エピソードを data/sim/smoke/ に生成
make train-smoke    # configs/wm/smoke.yaml で3分以内の学習
make exp EXP=EXP-01 SMOKE=1   # 実験 → runs/EXP-01/<timestamp>/{metrics.json,report.md}
make dashboard      # streamlit run src/gtwm/ui/app.py
uv run gtwm --help  # サブコマンド：doctor sim kg wm ground exp whatif llm
```
`make lint test` が緑でないコミットはしない。

## 技術選定（固定。変更は `docs/adr/` に ADR を書いてから）
- Python 3.11、uv、src レイアウト、typer CLI、Hydra 設定、pydantic v2、structlog。
- シミュレーション：MuJoCo 3.x（MJCF）。映像は imageio-ffmpeg で MP4、マスク・深度は npz、イベントは Parquet。
- WM：PyTorch（MPS、float32）。`facebook/dinov2-small` を凍結エンコーダ、slot attention と動態 Transformer は自前実装、計画は MPPI。
- KG：rdflib 7（RDF-star）を既定、Oxigraph（Docker、SPARQL :7878）を統合用。SHACL は pyshacl（`advanced=True`）。
- 制約損失：自前の微分可能ファジー論理（product t-norm）。Scallop / LTN は任意で、macOS でビルドできる場合のみ。
- 乖離台帳・LLM キャッシュ：SQLite 既定、Docker profile では Postgres/Timescale。
- 評価：MLflow（ローカル `mlruns/`）、統計は `gtwm.eval.stats`（ブートストラップ、Wilson 区間）。DVC は使わない。
- WHAT-IF：lark で付録 C の文法を実装。UI：Streamlit。データ空間：Eclipse Dataspace Components（P2）。

## 作業の進め方
- 30行を超える変更や新規モジュールは、着手前に「対象ファイル・テスト・受入基準・所要時間」を1画面で示して承認を得る。
- 完了の定義：ユニットテスト緑、`make lint` 緑、smoke が通る、`docs/status.md` と関連 docs を更新、結果は `runs/` に残る。
- 実行前に必ず確認するもの：`data/` `runs/` `mlruns/` `.data/` の削除、付録 A の指標定義・`experiments/criteria.yaml`・`gt:` 名前空間の変更、Docker サービスの追加、課金 LLM 呼出の追加、10分を超えるジョブの開始。
- 10分を超えるジョブは `nohup <cmd> > runs/<name>/log.txt 2>&1 &` で起動し、`tail -n 50` で進捗を見る。フォアグラウンドで待たない。
- 実験結果は `docs/results/EXP-xx.md` に、seed 3本の平均と95%区間、設定パス、コミットハッシュ、所要時間を書く。合格基準に届かない時は数値と ε の分解（知覚 / プロセス逸脱 / オントロジー欠落）に基づく原因分析を書き、閾値は変えない。
- 盲検：乖離・ドリフト・新概念の注入台帳（`data/injections/`）は検知側コードから読まない。`tests/unit/test_blindness.py` で import 境界を検査する。
- コミットは Conventional Commits（`feat: fix: exp: docs: chore: test:`）、1コミット1関心事。ブランチは `feat/<component>-<short>` と `exp/<EXP-id>`。`main` への force push 禁止。
- 不明点は推測で進めず、選択肢と推奨を1つ添えて聞く。ただし小さな決定（変数名、テストデータ）は自分で決めて進める。

## コーディング規約
- ruff（line-length 100）、型ヒント必須、mypy は `src/` を対象。識別子・コミットは英語、docstring とコメントは日本語可。
- 乱数は `gtwm.utils.seed.seed_everything(seed)` だけを使う。マジックナンバーは `configs/` へ。`print` 禁止（CLI は rich、ログは structlog）。
- テンソル形状は docstring に `[B,T,K,D]` の形で書く。デバイスは `gtwm.utils.device.get_device()`（mps → cpu）以外で選ばない。
- 新規モジュールには同名のユニットテストを作る。`tests/unit` は1件1秒以内、ネットワーク・Docker・GPU 不要。
- 1ファイル400行を超えたら分割。関数は50行以内を目安。
- オントロジー・指標・文法など「契約」に当たるものはコードより先にドキュメント（ttl / criteria.yaml / 文法ファイル）を変更する。

## 秘密情報・データ
- API キーは `.env`（gitignore 済）。`.env.example` には変数名だけ：`ANTHROPIC_API_KEY` `OPENAI_API_KEY` `GOOGLE_API_KEY`。キーをログ・コミット・チャット出力に出さない。
- 生成データは全て自前。外部データセット・外部映像は取り込まない。
- `PYTORCH_ENABLE_MPS_FALLBACK=1` と `TOKENIZERS_PARALLELISM=false` は `.env` と Makefile の両方で設定する。

## 現在のフェーズと着手順（P0 → P2 相当。詳細は docs/status.md）
1. 足場：pyproject / uv / Makefile / ruff / mypy / pre-commit / `gtwm doctor` / `.env.example` / `docs/status.md`
2. sim：MJCF 倉庫、センサ、アンカー、WMS モック、シナリオ生成、`gtwm sim gen`、`make sim-smoke`
3. kg：`gt-core.ttl`、SHACL 3本（付録 B）、KGStore、EPCIS 取込、`gtwm kg validate`
4. wm：エンコーダ、スロット、動態、学習ループ、`make train-smoke`
5. grounding：α / γ / ε / 同一性 / 乖離台帳、`gtwm ground run`
6. eval：ランナー、EXP-01/02/03/06 の smoke → 本実行、`docs/results/`
7. whatif + ui：lark 文法、エンジン、Streamlit
8. P1 相当：realism 設定、EXP-04/05/11
9. P2 相当：概念発見、2拠点連合（EDC）、EXP-07/08/09、EXP-10 の準備

## ルールファイル（`.claude/rules/`、該当パスを開いた時に読み込まれる）
`sim.md` シミュレーション ／ `world_model.md` WM と接地層 ／ `ontology.md` オントロジーと KG ／ `experiments.md` 実験と評価 ／ `infra.md` Docker と Makefile ／ `llm.md` LLM の使い方
