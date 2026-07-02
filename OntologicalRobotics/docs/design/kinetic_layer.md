# docs/design/kinetic_layer.md — キネティック層（アクション型）実装設計

- 起案: 2026-06-27 / 対象: IMPROVEMENT.md §3 の K0/K1（$0・stub/決定的）
- 上位文書: PROJECT.md（§5.1 データフロー・§5.2 不変条件）、CLAUDE.md（§3 依存方向）、IMPROVEMENT.md §3。
- スコープ: K0（部品）＋K1（act ツール・閉ループ・S8 決定的 ceiling）。**K2（live 課金）と
  H8（PROJECT.md 改訂）は承認ゲートにつき本実装では着手しない**（IMPROVEMENT.md §3.10）。

## 1. 目的（PROJECT.md §5.1 の未配線ループを充足）

`C7 agent → C2 skills → C1 sim → C6 kg(書戻し)` を閉じる。エージェントが**アクション型**を
発行し、世界グラフに対する送信基準で検証され、成功時に物理（C1）が変化し、来歴付きで
書き戻され（C6）、custody で監査でき、補償でアンドゥできる——を実装・検証する。

## 2. import-linter 制約による配置（重要な設計判断）

`pyproject.toml` の契約上 **`orx.skills` は `orx.kg`/`orx.agent`/`orx.exp` を import できない**
（`sim/skills/business/perception` ← それ以外）。したがって:

| 要素 | 配置（コンポーネント） | 依存可否の根拠 |
|---|---|---|
| `ActionType`/`ActionRequest`/`ActionReceipt`/`ValidationResult`/`ActionExecutor`/`EffectSink` | `orx/skills/action.py`（C2） | common のみ import。検証関数・EffectSink は**注入**（DI）し kg/agent に依存しない |
| `SimEffectSink`（物理反映） | `orx/skills/action.py`（C2） | `skills→sim` は許可（sim は forbidden に無い） |
| `DictEffectSink`（決定的 truth 反映） | `orx/skills/action.py`（C2） | 純データ（dict 更新）。test/決定的 ceiling 用 |
| `SimWorld.apply_effect` | `orx/sim/world.py`（C1） | 物理権威。tick 境界で適用（不変条件4） |
| Claim 構築ヘルパ（effect/custody/compensation/action-exec） | `orx/kg/action_claims.py`（C6） | common のみ import。`kg→common` は許可。assertion は `assert_claim` 経由 |
| グラフ送信基準（SPARQL 検証）＋ act ツール | `orx/agent/tools/skill_tool.py`（C7） | `agent→kg/skills/sim` は許可 |
| 閉ループ記録器・S8 runner・書戻し orchestration | `orx/exp/act_loop.py`・`exp/suites/s8_fulfillment/`（C10） | exp は全層 import 可 |
| post-action 真値採点 | `orx/oracle/scenarios/s8.py`（C8） | **post-action 真値＋業務定義のみ**。ORコア非 import（既存契約で強制） |

→ 結論: 執筆計画（§3.5）の「ActionExecutor を skills に」は維持しつつ、kg 結合（Claim 構築）は
**DI＋exp 側 orchestration** に逃がして契約を破らない。これが唯一の設計上の調整点。

## 3. アクション型のセマンティクス（Palantir 準拠・§3.4）

`ActionRequest{robot_id, skill, target_barcode, dest_zone}` に対し `ActionExecutor.apply`:

1. **送信基準（validate_fn 注入）**: グラフに対し ①対象個体の存在（identity thread 解決済）
   ②実行ロボットの能力契約（payload/reach/material）充足 ③目的地が規範（Prohibition）非抵触
   ④行為者の権限（ownership）。失敗 → `rejected`（理由付き・**副作用なし**）。
2. **ステージング**: 低確信度/規範フラグは `staged`（適用せず ESCALATE）。
3. **実行**: `SkillServer.execute`（既存の擬似VLA・確率的成否）。失敗 → `applied=False`（効果なし）。
4. **効果**: 成功時 `EffectSink.apply(object, dest_zone, at_time)` で物理（または truth）反映。
5. **書戻し・副作用**（exp 側）: effect=`inZone` 関数的 claim、custody=`CustodyStep`、
   action-exec=`ActionExecution`（status/actedOn/executedByRobot/atTime）を `assert_claim`。
6. **アンドゥ**: `compensate(receipt)` → 逆 `EffectSink.apply`＋元 effect claim の `valid_until` 失効＋
   補償 `ActionExecution(compensates=…)`。`owl:sameAs` 不使用（不変条件3 と同型の可逆設計）。

レシート状態: `applied|staged|rejected`（理由付き）。

## 4. 反実仮想リプレイとの整合（§3.1）

閉ループは条件ごとに物理履歴が分岐するため「1記録→多重リプレイ」は不適用。
**条件独立ロールアウト＋seed-paired 検定**（S1–S7 と同方式、`paired_comparisons`）。
results に `loop:"closed"` を焼き込み、`scope.loop_note()` で注記を出す。

## 5. S8（T15）fulfillment — 決定的 ceiling（K1・$0）

- 世界 `configs/world/s8_fulfillment.yaml`: 能力要件付き品目（重量/素材）＋ドメイン固有規制の
  搬送品（LLM 常識回避）＋所有者付き個人物品＋異種能力の複数ロボット＋scripted_moves（transit）。
- 条件: `OR-full`(決定的 ceiling・グラフ検証 act)／`B1`(個別スキーマ)／`B0`(生ダンプ)。
  agent 条件 `*-llm`/`guarded` は K2（live・要承認）。
- oracle `s8.py`（post-action 真値のみ）: `task_completion`/`safety_violations`/`misdeliveries`/
  `failed_executions`/`audit_completeness`/`recovery_rate`/`over_escalation`。
- 反証予言（決定的・seed 非依存）: `B1_capability_mismatch`(failed_executions>0)・
  `B0_unsafe`(safety_violations>0 ∧ misdeliveries>0)・`OR_full_safe`(safety_violations==0 ∧
  completion≥baseline)・`OR_full_auditable`(audit_completeness==1.0 ∧ baselines<1.0)。

## 6. テスト（CLAUDE.md §5）

`tests/skills/test_action.py`（validate/stage/reject/effect/undo）・`tests/sim/test_apply_effect.py`
（物理移動・ループ純粋性）・`tests/kg/test_action_claims.py`・`tests/replay/test_act_streams.py`
（記録→再読込同一）・`tests/scenarios/s8/test_falsification_s8.py`・`tests/scenarios/s8/test_shapes_s8.py`
（SHACL）・S8 CQ（custody/compensation 再構成）。全緑＋`lint-imports` 0 broken＋`ruff` clean を完了基準。

## 7. 完了基準（K0/K1）

K0: 上記 K0 テスト緑／`apply_effect` で真値が動く／記録→リプレイ同一／SHACL 適合／契約 0 broken。
K1: `make scenario-s8`（決定的・$0）緑・反証全 ✓・`loop:closed` 注記／全 suite・cq・SHACL・lint 緑。
ゲート: K2（live）・H8（PROJECT.md）は未着手で handoff（IMPROVEMENT.md §3.10）。
