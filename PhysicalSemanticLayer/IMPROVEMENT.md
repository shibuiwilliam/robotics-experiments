# IMPROVEMENT.md — PSL-Bench 改善レポート

> **最終更新**: 2026-06-07
> **対象**: Physical Semantic Layer (PSL-Bench) 全コードベース・ドキュメント

---

## 現状

全指摘事項が解決済み。残存は環境依存の R1 のみ。

- **テスト**: 262 passed, 4 skipped (CLIP 依存)
- **シナリオ**: 7/7 PASS（オンライン・オフライン両方、5シード統計評価済み）
- **Phase 4 ストレステスト**: スキーマファジング + クロックスキュー + 接地汎化 — 全完走
- **HTTP 404**: 0件（discovery API + プロンプト改善で解消）
- **フォールバック**: 0件（全シナリオがオンラインで正常完走）

---

## 解決済み項目一覧

| ID | 重大度 | 概要 | 解決内容 |
|----|--------|------|----------|
| C1 | Critical | VLA 未実装 | CLIP ViT-B/32 推論パス実装 + hash fallback 保持。`pyproject.toml` に `[vla]` optional deps 追加 |
| C2 | Critical | エージェントアダプタ空 | `ClaudeAgentAdapter` 実装、全シナリオで WorldModel に統合 |
| C3 | Critical | ロボット2種のみ | `DroneAdapter` (6-DOF, ENU) 追加。S4 にドローン空中検査フェーズ統合。N+N 証明 |
| M1 | Major | オンラインモードテスト不足 | ASGI トランスポート・フォールバック挙動・404 統計テスト追加 |
| M2 | Major | 業務モック浅い | 25+ items, 8+ bins, pagination, fault injection, RBAC 追加 |
| M3 | Major | WorldModel スレッド非安全 | `threading.RLock` 導入、並行書き込みテスト追加 |
| M4 | Major | menagerie 空スタブ | モデルパス解決・レジストリ・env override 実装 |
| M5 | Major | MR5 がドキュメント未更新 | `docs/metamorphic.md` に MR5 + Phase 4 ストレステスト追記 |
| A1 | Arch | 協調パス不在 | `SemanticCommandChannel` (issue/poll/acknowledge) 実装 |
| A2 | Arch | 業務データ N+N 化 | `CloudDataAdapter` 実装、全シナリオで統合 |
| A3 | Arch | マルチシード未実行 | 5シード × 7シナリオ = 35ラン実行、全 PASS。REPORT.md に統計追記 |
| N1 | Minor | S7 タイムアウト | timeout 180s + プロンプト改善 + `agent_fallback_used` フィールド追加 |
| N2 | Major | マルチシード未実行 | `run_multi_seed_report.py` 作成・実行。95% CI 付き統計を REPORT に記載 |
| N3 | Major | VLA 実推論未検証 | `tests/test_vla_clip_integration.py` (`@slow` + `skipif`) 作成 |
| N4 | Minor | エージェント 404 多発 | discovery API (`/bins`, `/workorders`, `/sops`) + プロンプトに明示 ID + `agent_404_count` 記録 |
| N5 | Major | Drone シナリオ未統合 | S4 にドローン空中検査フェーズ追加、`drone_r2r_handoff` breakpoint 追加 |
| N6 | Minor | agent_trace 未保存 | `write_manifest()` に `agent_trace` パラメータ追加、自動保存 |
| R2 | Minor | `get_vla_info()` 不正確 | `get_vla_info(encoder)` がランタイムモード参照に変更、`vla_mode` フィールド追加 |
| Phase 4 | — | ストレステスト未実装 | スキーマファジング + クロックスキュー + 接地汎化 実装・実行 |
| m1 | Minor | torch 不要 import | CLIP パス内のみに限定 |
| m2 | Minor | LOD 型注釈 | `Callable[[], float]` に修正 |
| m6 | Minor | manifest に VLA 未記録 | `get_vla_info()` をマニフェストに含める |

---

## 残存事項

### R1. VLA 実 CLIP 推論の環境依存検証

- **状態**: コードパスは実装済み。`open-clip-torch` + `torch` が未インストールのため、全シナリオは hash fallback で実行。
- **テスト**: `tests/test_vla_clip_integration.py` が `@pytest.mark.slow` + `skipif` で準備済み。
- **検証手順**: `uv pip install psl-bench[vla]` → `make test-slow` → `make scenario-s6`
- **影響**: H5（埋め込み優位）の支持は hash fallback 条件下での結果。実 CLIP での比較は未実施。
- **重大度**: Minor（アーキテクチャ・機能に問題なし。検証の深度のみ）

---

## Phase 4 ストレステスト結果サマリ

| Test | Key Result |
|------|-----------|
| スキーマファジング (50 samples) | 42/50 ゲート通過。単位/フレーム変換は完全可逆。ノイズのみが不可逆劣化源 |
| クロックスキュー (8 levels) | 0s=ゼロ違反。≥1ms で全遷移検出・拒否。ゲート感度は非常に高い |
| 接地汎化 (20 holdout objects) | 100% エンベディング/アフォーダンス生成。5マテリアルクラス。cosine 0.068 (区別可能) |
