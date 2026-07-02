# R3_PLAN.md — IMPROVEMENT.md §2 R-3 実装計画

作成日: 2026-06-15 / 基準: `IMPROVEMENT.md` §2 R-3a–f, `PROJECT.md`（§7.3 掃引・§8.3 統計・§12 DoD）, `CLAUDE.md`。

`make scenario-all` で露見したデータ・ギャップ（R-3a–f）を production レベルで閉じる。
**オフライン（無課金・確実）** と **課金（要承認）** を厳密分離する。

---

## 0. 現状（2026-06-15・324 tests green）

- S1–S7 ゲート閉・`make scenario-all` 正常・反証 27/27 ✓。R-1（live）・R-2（S1）完了。
- ギャップ: R-3a（シナリオに agent 射程無し・**課金**）／R-3b（S5 seeds=2）／R-3c（S3・S5 掃引無し）／
  R-3d（対比較統計が S1 のみ）／R-3e（scenario-all 恒久ログ無し）／R-3f（git stamp dirty 非対応）。

## 1. 優先順位と分類

| # | 項目 | 価値 | リスク | 課金 | 本パスで |
|---|------|------|--------|------|---------|
| R-3f | git_commit に `-dirty` | 中（再現性整合） | 低 | 無 | **実装** |
| R-3e | scenario-all 恒久ログ（`reports/scenario-all-<date>.md`） | 中（再現性） | 低 | 無 | **実装** |
| R-3d | 対比較統計（McNemar/Wilcoxon/CI）を S2–S7 に | 中（§8.3 厳密化） | 低〜中 | 無 | **実装**（S2/S3/S4/S6/S7） |
| R-3c | 頑健性掃引を S3・S5 に追加 | 中（§7.3） | 中 | 無 | **実装** |
| R-3b | S5 seeds→8（R-3c で seed 依存化後に有意味） | 中 | 中 | 無 | **実装** |
| R-3a | シナリオに LLM エージェント条件 | 高 | 高 | **有** | **設計＋概算＋承認ゲート** |

## 2. 設計上の判断（重要）

- **S5 は決定的（`_eval(world)` が seed 非依存）**。seeds を増やすだけは「偽の n」。R-3b を**誠実**に満たすには
  S5 を **seed 依存（確率的）に**する必要がある。→ R-3c で **custody/normative 劣化ノブ**（seed 毎に確率的に
  custody 記録を欠落 or 規範標識を遮蔽）を追加し、それが seeds と掃引の両方を意味あるものにする。
  ノブ=0 では現行の決定的結果を保存し、反証ゲートを壊さない。
- **R-3d の統計的誠実さ**: McNemar は「各 seed が独立試行」を前提とする。S2/S4/S6/S7 は seed 毎に
  episode が変わる（確率的）ため有意味。S1 同様、OR-full が全 seed で勝てば p≈0.0078（8 seed）。
  決定的・seed 非依存の条件には適用しない（誤った独立性主張を避ける）。
- **射程分離（C1）は不変**: 追加する統計はすべて ceiling/ablation 射程（決定的）。agent 射程は R-3a のみ。

## 3. 実装方針（各項目）

- **R-3f**: `episode._git_commit()` で `git status --porcelain` が非空なら `<hash>-dirty` を返す。+ 単体テスト。
- **R-3e**: `orx scenario report-all`（新 CLI）＝最新の各シナリオ results.json を集約し
  `reports/scenario-all-<date>.md` に「条件別サマリ＋反証＋スタンプ」を書き出す。Make `scenario-all` から呼ぶ。+ テスト。
- **R-3d**: 各 runner の `_eval_seeds` を「agg ＋ per-seed (success_bool, metric)」を返すよう拡張し、
  `run()` で `compare_conditions(OR-full, baseline, …)` を全ベースラインに適用、`comparisons` を Result に追加、
  render_report に対比較表を追加。S1 のパターンに揃える。
- **R-3c (S3)**: `fault_rate`（または再割当頻度）ノブで掃引し overall_accuracy 曲線を出す。
- **R-3c+R-3b (S5)**: `custody_gap_rate` ノブ（seed 依存確率欠落）を追加→掃引＋seeds=8→R-3d も有意味化。
- **R-3a**: `docs/SCENARIO_AGENT_DESIGN.md` に設計（T2 ToolAgent 再利用・条件別ツール・scorer 写像）＋
  `orx exp estimate` 相当の概算を提示し、**ユーザー承認後に** 1 シナリオ（S1）で live 実証。

## 4. 完了基準（DoD）

- R-3b–f: 全シナリオで `comparisons`（該当）と `robustness`（S3/S5 追加）が results.json に入り、
  反証ゲート・replay 同一性・全テスト緑。`make scenario-all` が `reports/scenario-all-<date>.md` を生成。
  `_git_commit` が dirty を反映。新規/更新テストが緑。lint clean。
- R-3a: 設計＋概算をユーザーに提示し、承認後に S1 で agent 射程の数値を ceiling/ablation と分離報告。
