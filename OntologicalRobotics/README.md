# ORX (Ontological Robotics eXperiments)

MuJoCoシミュレーション上で Ontological Robotics（共通オントロジー＋アンカリング層による異種システム統合）の有効性を**反証可能な形で**検証する研究基盤。

- プロジェクト定義（仮説H1–H7・完了基準）: [PROJECT.md](PROJECT.md)
- 開発規約: [CLAUDE.md](CLAUDE.md) / 実装計画: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) / 進捗: [docs/PROGRESS.md](docs/PROGRESS.md)

## 5分クイックスタート

要件: Apple Silicon Mac、Python 3.11+、[uv](https://docs.astral.sh/uv/)。**APIキー・GPU設定・ビューア不要。**

```bash
uv sync                 # 環境構築（make install でも可）
uv run orx demo         # オフラインのエンドツーエンド実行（〜1分）
```

よく使うコマンドは `make help` に一覧（`make demo` / `make test` / `make exp-all` など）。
live計測用の環境変数は [.env.example](.env.example) を `.env` にコピーして設定する
（既定のオフライン動作では不要）。

`orx demo` は P0 パイプライン（sim → perception → anchoring → world graph → oracle）を
ヘッドレスで実行し、忠実度レポート（真理グラフとの差分）を表示します。

最初の記録とレポート:

```bash
uv run orx sim run configs/world/demo_tiny.yaml --duration 20 --run-id my-first-run
uv run orx replay my-first-run --condition OR-no-identity   # 反実仮想リプレイ
uv run orx report my-first-run                              # reports/my-first-run.md
uv run orx cq                                               # コンピテンシー質問回帰
```

テスト（完全オフライン）:

```bash
uv run pytest -q            # 全テスト
uv run pytest tests/cq -q   # CQ回帰のみ
```

実験の実行（タスクスイート T1〜T7、すべてオフラインで再現可能）:

```bash
uv run orx exp run configs/experiments/t1_identity.yaml      # H2 越境同一性
uv run orx exp run configs/experiments/t2_business.yaml      # H6/H7 業務-物理クエリ
uv run orx exp run configs/experiments/t3_capability.yaml    # H3 能力計画+較正
uv run orx exp run configs/experiments/t4_degradation_sweep.yaml  # H5 頑健性曲線
uv run orx exp run configs/experiments/t5_onboarding.yaml    # H1 統合コスト
uv run orx exp run configs/experiments/t7_sop.yaml           # H4 SOP検索
uv run orx onboard configs/robots/fuzzed_vendor_x.yaml       # 新ロボット統合
```

## 実装状況（P0〜P5 全フェーズ構築済み）

- **C1 sim**: MuJoCoミニ倉庫、異種2ロボット（カメラ/擬似LiDAR）、ベンダースキーマA/B、
  スキーマ・ファジング、劣化ノブ5種、クレーン搬送
- **C2 skills**: 擬似VLAスキルサーバ＋条件付き故障注入（重量/素材/リーチ）
- **C3 business**: 模擬WMS（SQLite）・SOPコーパス・アイデンティティ・スレッド
- **C4 perception**: 宣言的リフティング・ローカルCLIP（MPS）/決定的スタブ埋め込み
- **C5 anchoring**: 2仮説スコア（静止×搬送=等速予測）＋埋め込み変調＋大域割当
- **C6 kg**: クレーム単位named graph（来歴・確信度・時刻必須、SHACL強制）、
  確信度×新しさの信念調停、能力台帳、LanceDB索引
- **C7 agent**: ツール使用ループ（SPARQL/SQL/生観測）、B0/B1ベースライン、H7計測
- **C8 oracle**: 真理グラフ忠実度（ORコアと相互不可侵 — import-linterで強制）
- **C9 replay**: 全ストリーム記録・反実仮想リプレイ（メトリクスはバイト一致）
- **C10 exp**: 条件×シード実験、McNemar/Wilcoxon/ブートストラップ、レポート自動生成

各フェーズのゲート測定値は [docs/PROGRESS.md](docs/PROGRESS.md)。
LLMを使う本計測（T2エージェント条件・T7ベクトルRAG・T5 LLM支援）のみ
OPENAI_API_KEY とコスト承認が必要（オフラインのstubハーネスで全経路検証済み）。
