# ORX (Ontological Robotics eXperiments)

MuJoCoシミュレーション上で Ontological Robotics（共通オントロジー＋アンカリング層による異種システム統合）の有効性を**反証可能な形で**検証する研究基盤。

- プロジェクト定義（仮説H1–H7・完了基準）: [PROJECT.md](PROJECT.md)
- 開発規約: [CLAUDE.md](CLAUDE.md) / 実装計画: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) / 進捗: [docs/PROGRESS.md](docs/PROGRESS.md)

## 5分クイックスタート

要件: Apple Silicon Mac、Python 3.11+、[uv](https://docs.astral.sh/uv/)。**APIキー・GPU設定・ビューア不要。**

```bash
uv sync                 # 環境構築
uv run orx demo         # オフラインのエンドツーエンド実行（〜1分）
```

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

## いま動くもの（Phase 0 完了範囲）

- 最小倉庫世界（1ロボット・8箱・ゾーン・スクリプト遷移）、オフスクリーン224px描画
- ベンダースキーマA観測 → 宣言的リフティング（`ontology/mappings/`） → 知覚イベント
- アンカリング骨格（バーコード決定的＋空間最近傍）、`orx:anchoredTo` による改訂可能な同一性
- 世界グラフ（クレーム単位named graph、来歴・確信度・時刻必須、SHACL検証、LanceDB索引）
- 真理グラフ忠実度（トリプルP/R/F1・同一性F1・位置RMSE・遷移検出・陳腐化率）
- 記録→反実仮想リプレイ（メトリクスはバイト一致で再現）

以降のフェーズ（2台目ロボット・業務ブリッジ・エージェント・劣化掃引・オンボーディング）は
[docs/PROGRESS.md](docs/PROGRESS.md) を参照。
