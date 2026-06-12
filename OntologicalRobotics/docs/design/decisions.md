# 設計決定ログ（ADR）

> 実験に重大な影響を持つ判断は IMPLEMENTATION_PLAN.md §10（D1–D4）でユーザー承認を経る。
> ここには軽微な決定を ADR 形式で記録する（CLAUDE.md / 実装プロンプトの規約）。

## ADR-001: ベクトル索引は LanceDB（faiss-cpu ではなく）
- 日付: 2026-06-12 / 状態: 採用
- PROJECT.md §10 は `lancedb`（または `faiss-cpu`）を許す。LanceDB はIRIキーのテーブルとして永続化でき、メタデータ併置・再オープンが素直で、リプレイ再現（同一索引の再構築）が単純になる。faiss はインメモリ前提で IRI↔行番号の対応管理が別途必要。Apple Silicon ネイティブ wheel あり。

## ADR-002: 依存方向の強制は import-linter の forbidden 契約＋subprocess テスト
- 日付: 2026-06-12 / 状態: 採用
- layers 契約は同層内の相互参照（例: skills→sim）の扱いが曖昧になるため、CLAUDE.md §3 の規則を forbidden 契約の集合として明示的に書き下した（pyproject.toml）。`tests/test_architecture.py` が CI で契約を実行する。

## ADR-003: pub/sub は当面プロセス内 asyncio（ZeroMQ 不導入）
- 日付: 2026-06-12 / 状態: 採用（再訪可）
- PROJECT.md §10 は「asyncio＋ZeroMQ程度」。単機・単プロセスで全フェーズが成立する見込みのため、依存最小の原則に従い pyzmq は必要が生じるまで追加しない。物理/知覚の周波数分離は asyncio タスクで実現する。

## ADR-004: ビルドは hatchling・src レイアウト・エントリポイント `orx = orx.cli:app`
- 日付: 2026-06-12 / 状態: 採用
- uv 標準の軽量バックエンド。src レイアウトはテスト時のインポート事故（カレントディレクトリの優先）を防ぐ。

## ADR-005: ruff に T20（print禁止）を含める
- 日付: 2026-06-12 / 状態: 採用
- CLAUDE.md §4「printデバッグを残さない」を lint で機械強制。CLI 出力は typer.echo を使う。

## ADR-006: 物理/知覚の分離は「決定的協調スケジューリング」で実装
- 日付: 2026-06-12 / 状態: 採用（P0）
- 不変条件4（レイテンシ分離）は、壁時計ベースの asyncio 並行ではなく、
  ミリ秒整数に正規化した決定的タイムライン（物理を knot まで進める→知覚→評価）で実装した。
  理由: リプレイ同一性（P0完了基準）は実行順序の完全決定性を要求し、実時間並行はそれを壊す。
  「シムの内側ループにI/O・グラフ・LLMを入れない」という規約上の本質は維持している。

## ADR-007: 真値対応はメッセージの oracle 専用フィールドで運搬
- 日付: 2026-06-12 / 状態: 採用
- 検出→真値物体の対応（同一性F1の採点に必要）は RawObservation/PerceptionEvent の
  `oracle_truth_ids` で運び、anchoring/kg/agent がこのフィールドへアクセスしないことを
  ASTベースのアーキテクチャテストで強制する（tests/test_architecture.py）。
  幾何ベースの事後対応付けより単純で、採点経路が監査可能。

## ADR-008: メトリクスの正準JSONでバイト一致を判定
- 日付: 2026-06-12 / 状態: 採用
- リプレイ同一性は FidelityReport の正準JSON（sort_keys, ensure_ascii=False, indent=2）
  同士のバイト比較で判定（orx/replay/io.py metrics_json）。壁時計を含むマニフェストは対象外。
