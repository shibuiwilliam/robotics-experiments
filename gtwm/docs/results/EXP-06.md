# EXP-06 結果

## 目的
オントロジー制約（進入禁止ゾーン）を計画のハード制約として保証できるかを検証する（H5、poc_plan.md 3.1）。

## 設定
- config: `experiments/EXP-06/config.yaml`（`full_run`：`probe_config: configs/grounding/probe_train_full.yaml`、`data_set: p0_eval`、`n_tasks: 100`）
- データ: `p0_eval`（5分×10本、seed=200始まり。`task_idx % len(episodes)` で巡回）
- WM チェックポイント: `runs/wm/checkpoints_full/best.pt`（`configs/wm/full.yaml`、K=16/D=128/4層/アンサンブル3）
- planner: `n_samples=32, horizon=5, temperature=0.3, noise_std=25.0, shortcut_bonus=30.0`（**`full_run` はこれらを上書きしない**——smoke用WM（K=8/D=64/2層、`wm_smoke`データ）に対して個別にチューニングされた値のまま、下記「考察」参照）
- git: 実行時コミット `ec2d401` 付近、dirty フラグは `runs/EXP-06/test-debug-run/git.txt` を参照
- seeds: [0, 1, 2]
- 所要時間: 2774.92s（他の並列本実行（EXP-01/04/07/08/09）との CPU/MPS 資源競合下での実測値）

## 結果表

| 指標 | seed=0 | seed=1 | seed=2 | 平均 | 合格基準 | 判定（機械的） |
|---|---|---|---|---|---|---|
| violations_with_shield | 0 | 0 | 0 | **0.0** | == 0 | 達成 |
| violations_without_shield（参考、合格基準なし） | 0 | 0 | 0 | 0.0 | - | - |
| throughput_loss | 0.0 | 0.0 | 0.0 | **0.0** | <= 0.05 | 達成 |

`task_cost_with_shield` と `task_cost_without_shield` は全 seed で完全に同一の値（例：seed=0で両方とも6.689686312675476）。

## 判定
**合格（ただし機械的な数値のみ。下記「考察」により、この合格は H5 の実質的な検証にはなっていないと考えられる——不合格を言い換えているのではなく、逆に見かけ上の合格を額面通り受け取るべきでない、という指摘）**

## 考察

`runner.judge_criteria()` は `violations_with_shield == 0` かつ `throughput_loss <= 0.05` を機械的に判定するため、上記の通り「合格」となる。しかし全300タスク（100タスク×3seed）で **シールドの有無に関わらず violations が一貫して0件、かつ shield 有り/無しのコストが完全に同一** という結果は、smoke規模の実行（`violations_without_shield=3/3` → shield適用で `0/3` に変化、docs/status.md 着手順6参照）とは質的に異なる。これは「シールドが常に正しく機能した」のではなく、**shield が候補プールから何かを除外する場面が一度も発生しなかった**ことを意味する——シールドの実効性が試されていない、退行実験（vacuous test）である。

根本原因（要フォローアップ、コード変更ではなく設定の不備）：
1. `experiments/EXP-06/config.yaml` の `full_run:` ブロックは `seeds`/`n_tasks`/`probe_config`/`data_set` は上書きするが、**`planner:` サブブロック（`temperature`/`noise_std`/`shortcut_bonus`）を上書きしない**。これらの値は smoke 用 WM チェックポイント（`configs/wm/smoke.yaml`、K=8/D=64/2層）に対して「候補行動がゾーン予測を十分動かす」よう個別にチューニングされたもの（config.yaml 内のコメント参照）。
2. 本実行では全く別のアーキテクチャ・学習データの WM チェックポイント（`configs/wm/full.yaml`、K=16/D=128/4層、`p0_train`）を使うため、床面座標回帰・ゾーン確率の応答特性が smoke チェックポイントと異なる可能性が高く、同じ `noise_std=25.0`/`shortcut_bonus=30.0` が「禁止ゾーンへの近道」を作るのに不十分になっていると推測される（smoke で診断済みの「既定値では候補行動が小さすぎる」問題の再発と考えられる）。
3. 実際に `noise_std=60.0`・`shortcut_bonus=80.0` に引き上げた5タスクの追加診断を試みたが、本 report 作成時点で並列稼働中の他実験（EXP-11 が同時に約20分間 CPU/MPSを占有）との資源競合により、90秒・180秒のタイムアウトいずれでも完了せず、診断を完遂できなかった（本質的な計算量の問題ではなく、単独実行なら数十秒で終わるはずの規模——資源競合が晴れた状態での再試行が必要）。

## 次のアクション（優先順）
1. `experiments/EXP-06/config.yaml` の `full_run:` に `planner:` の本実行用オーバーライド（`noise_std`/`shortcut_bonus` を full チェックポイント向けに再チューニングした値）を追加する。まず数タスクの小規模診断で「shield無しなら実際に violations が発生する」状態を作れることを確認してから、100タスク×3seedの本実行をやり直す。
2. 資源競合のない（他の本実行と時間をずらした）環境で再診断・再実行し、シールドの実効性を実際に確認できる条件下での本実行結果を得る。
3. 上記が終わるまで、この「合格」は poc_plan.md が意図する「シールドが違反を防いだ」ことの実証としては扱わない。危険物隣接・容量制約への拡張（`ComplianceShield` は実装済みだが本タスク生成が単一禁止ゾーンのみを対象にしている点）も未着手のまま。
