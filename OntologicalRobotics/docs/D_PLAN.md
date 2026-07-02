# D_PLAN — 2026-07-02 受入指摘（D-1〜D-4）是正計画

- 起点: `IMPROVEMENT.md §1`（2026-07-02 `make scenario-all` 受入実行での指摘・REPORT.md §9）
- 基準: `PROJECT.md`（§5.2 不変条件・§7.2 反実仮想リプレイ・§8.3 再現性ポリシー）、`CLAUDE.md`（§4 ログ規約・§7 Git規約）
- 仮説対応: いずれも仮説の**結論を変えない**装置品質の是正。D-2/D-3 は H6/H8 の「監査可能性・再現可能性」
  主張の証拠強化、D-1/D-4 は §8.3 来歴焼き込みの完全性に紐づく。

---

## 0. 優先度と依存関係

| # | 指摘 | 対応 | 優先度 | 理由 |
|---|------|------|--------|------|
| D-4 | 来歴欠落の旧 run が集計を汚しうる | 集計側で provenance 必須化（除外） | **P1** | レポート正しさに直結・最小変更 |
| D-2 | シナリオ run に structlog 未保存 | `run_experiment` チョークポイントで per-run log.jsonl | **P1** | CLAUDE.md §4 準拠・全8シナリオに一括適用 |
| D-3 | エピソード永続記録が S1 のみ | コンフィグのアーカイブ（全suite）＋エピソード入力の永続化（s2/s4/s6/s7） | **P2** | アーカイブ単独リプレイの成立 |
| D-1 | 公式実行が dirty ツリー | ①ORXツリーに限定した dirty 判定 ②Makefile ガード ③全変更コミット→クリーン受入再実行 | **P2**（ただし最後に実施） | コミットは全ゲート緑の後 |

実施順: D-4 → D-2 → D-3 → D-1（コード）→ 全ゲート → ドキュメント → コミット → クリーンツリー受入再実行。

## 1. D-4 — 集計の provenance ガード

- `exp/scenario.py` に述語 `has_provenance(results: dict) -> bool` を追加
  （`git_commit`・`model_snapshot` が非空文字列であること）。
- `scenario.latest_scenario_results()`（scenario-all 集約）と `exp/report.py:latest_results_by_scope()`
  （REPORT.md 生成）の両方で、provenance を欠く results.json を**最新選択から除外**。
- データは削除しない（CLAUDE.md §8）。`data/runs/scenario-s1-89389d38/` にはローカル注記
  `LEGACY.md`（除外理由）を置く。
- テスト: `tests/exp/test_report_main.py` に「provenance 欠落 run は集計から除外される」を追加。

## 2. D-2 — per-run 構造化ログ

- 実装点は `scenario.run_experiment()`（全8シナリオ共通のチョークポイント）1箇所:
  - `make_run_logger(exp_dir, run_id=..., scenario=...)` で `log.jsonl` を作成。
  - `run_start`（name・config_hash・conditions・seeds・knob/knob_values・world_config・provider.mode）。
  - suite への `notify` をラップし、既存の進捗メッセージ（条件×seed×指標行）を
    `progress` イベントとして構造化記録（suite 側の変更ゼロで s1–s8 に適用）。
  - `run_end`（scope・git_commit・model_snapshot・falsification 判定）。
- `common/logging.py:make_run_logger` を行バッファ（`buffering=1`）にし、プロセス継続中でも
  各イベントが即時ディスクに載るようにする（テスト・中断時の可観測性）。
- 物理ステップループ内の I/O ではない（実験オーケストレーション層）ため不変条件4に抵触しない。
- テスト: `tests/exp/test_run_logging.py`（小規模実験を実行し、`log.jsonl` の run_start /
  progress / run_end と主要フィールドを検証）。

## 3. D-3 — アーカイブ単独で再実行可能な記録

2段構え（PROJECT.md §7.2「記録→多条件リプレイ」の記録側を run ディレクトリに自己完結させる）:

1. **全 suite 共通（run_experiment）**: 実験コンフィグの解決済みダンプ
   `experiment_config.json` と世界コンフィグの原本コピー `world_config.yaml` を exp_dir に保存。
   results.json に `recording` ブロック（scheme・アーカイブファイル名）を追記。
   → s3/s5/s8（エピソード＝world+seed から決定的に導出される suite）も、**run ディレクトリ＋
   ソース版（git_commit）だけで**再実行が完全に規定される。scheme は
   `deterministic-regeneration` と明示（暗黙にしない）。
2. **エピソード入力モデルを持つ suite（s2/s4/s6/s7）**: 生成された `S{n}Episode` を
   `exp_dir/episodes/{knob}-{value}-seed{seed}.json`（S1 の命名に整合）へ永続化する共有ヘルパ
   `exp/record.py:persist_episode()` を追加し、主評価＋掃引の生成点に配線。scheme は `episodes`。
   → アーカイブされた JSON から `S{n}Episode` を復元して再採点でき、コード＋seed に依らない
   アーカイブ単独リプレイが成立。
- agent（live）射程は LLM 応答キャッシュ（`data/cache/llm`）＋同一 (world, seed) 決定論で既に
  $0 再現が成立しており、本対応の対象外（既存設計のまま）。
- テスト: `tests/exp/test_episode_persistence.py`（s2/s4/s6/s7 の round-trip: 永続化→復元→
  モデル等価）、`tests/exp/test_run_logging.py` 内でコンフィグアーカイブと `recording` ブロックを検証。

## 4. D-1 — クリーン来歴での公式実行

- **判定の正確化**: `exp/episode.py:_git_commit()` の dirty 判定はリポジトリ全体
  （`robotics-experiments/`）を見るため、**ORX 外の兄弟ディレクトリ（例 TunedPastAction/）の
  変更でも dirty になる**。`repo_root()`（= OntologicalRobotics/）にスコープした
  `git status --porcelain -- .`（cwd=repo_root）へ修正。ORX の結果の来歴は ORX ツリーの状態。
- **ガード**: Makefile に `git-provenance-check`（dirty なら警告・`STRICT_PROVENANCE=1` で失敗）を
  追加し、`scenario-run-all` / `scenario-run-live-all` の前段に配線。
- **恒常的クリーン化**: 再生成可能な `REPORT.md` を `.gitignore` に追加
  （`reports/*.md` と同じ根拠 = R-INFRA で `make report` から決定的に復元可能）。
  blog 草稿・docs・S8/agent 実装・live コンフィグ等の未コミット一式は**コミットして**ツリーを
  クリーン化する（configs/ontology/tasks は CLAUDE.md §7 でコミット必須）。
- **受入再実行**: クリーンツリーで `make scenario-all` を再実行し、全 results.json に
  非 dirty の git_commit を焼き込む。
- テスト: `tests/exp/test_git_stamp.py` に「ORX ツリー外の変更では dirty にならない」を追加。

## 5. 完了基準（DoD）

1. `uv run ruff format --check` / `ruff check` / `uv run lint-imports`（7 kept / 0 broken）緑。
2. `uv run pytest -q` 全緑（既存 433 ＋ 新規）。
3. `make scenario-all` exit 0・反証 32/32・全 run ディレクトリに `log.jsonl`・
   `experiment_config.json`・`world_config.yaml`（＋該当 suite は `episodes/`）。
4. クリーンツリー受入再実行後、最新 results.json の `git_commit` に `-dirty` が**付かない**。
5. `orx report-all` / `orx report-main` が legacy run を選択しない。
6. IMPROVEMENT.md から D-1〜D-4 を削除（完了項目は残さない・本書と git 履歴が記録）。

## 6. 非対応（明示）

- **R-K2**（S3/S5/S7 の act 化）: 既存証拠と重複のため実施しない（IMPROVEMENT.md §2 の判断を維持）。
- **R-OPT**（S5 live シード増・別ベンダー再現）: 実課金＝要ユーザー承認のため本計画から除外。
- **PROJECT.md の改訂**（D-3 の「解釈明記」案）: CLAUDE.md §7 により Claude Code は独断で行わない。
  代わりに記録方式を results.json の `recording.scheme` と本書で**機械可読・文書化**した。
