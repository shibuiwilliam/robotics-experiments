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
