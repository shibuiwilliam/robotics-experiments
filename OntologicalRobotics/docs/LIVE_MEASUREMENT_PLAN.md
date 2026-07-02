# LIVE_MEASUREMENT_PLAN.md — R-1/R-2 実装計画

作成日: 2026-06-14 / 基準: `IMPROVEMENT.md` §2（R-1/R-2）, `PROJECT.md`（H1–H7・§5.2・§8.3・§12）, `CLAUDE.md`（§6 実験規約）。

本書は IMPROVEMENT.md に残る2課題（R-1 live計測 / R-2 ハードケース増強）を **production レベル**で
完了させるための実装計画と優先順位を定める。実装は段階を **「オフライン（無課金・即実行）」** と
**「課金実行（明示承認ゲート）」** に厳密分離する（CLAUDE.md §6: 大量記録はユーザー承認が前提）。

---

## 0. 現状（2026-06-14 監査・実証済み）

- 全テスト **314 passed**、lint clean。S1–S7 シナリオ 7/7 ゲート閉。
- live 計測の**装置は実在**するが、本番 config はすべて `provider.mode=stub`:
  - `OpenAILLMClient`（temperature 0・書き抜きキャッシュ・token抽出）`common/providers.py:144-180`
  - トークン集計と H7（accuracy/1k tokens）`exp/runner.py:648-671`
  - 射程分離 `exp/scope.py`（ceiling/ablation/agent）
- **未充足のギャップ**（Explore 監査）:
  1. 課金前の**コスト概算が無い** → 承認判断の数値が出せない。
  2. `mode=openai` で `OPENAI_API_KEY` 不在時の**事前検証が無い**（最初のAPI呼で初めて落ちる）。
  3. **live config が無い**（`mode: openai`＋モデルスナップショット固定の実験定義）。
  4. **ワンコマンドの live 経路・runbook が無い**。
- R-2: S1 のハード事例が **n=1**（搬送済みID不可読は b3 のみ）。

---

## 1. 設計原則

- **コスト承認ゲートを越えるまで実APIを叩かない。** オフライン経路（stub/cache）で live 経路の
  正しさを 100% 検証してから、課金実行は別ステップで承認を取る。
- **モデル名のハードコード禁止**（CLAUDE.md §6）。live config の `provider.llm_model` /
  `text_embedding_model` にスナップショット名を固定し、構成ハッシュ・マニフェストに焼き込む。
- **全応答キャッシュ**。初回 `mode=openai` 記録後は `mode=cache` で 0 円再現（PROJECT.md §8.3）。
- **射程分離を維持**（C1）。live 数値は agent 列にのみ入れ、ceiling/ablation と混同しない。
- 既存の不変条件（§5.2）と依存方向を壊さない。oracle は ORコアを import しない。

---

## 2. 作業項目と優先順位

### Phase A — オフライン（無課金・即実行）

| # | 項目 | 価値 | リスク | 成果物 |
|---|------|------|--------|--------|
| A1 | **コスト概算器** `exp/cost.py` ＋ `orx exp estimate <config>` | 高（承認を数値で解禁） | 低 | 期待LLM/埋め込み呼数・トークン・USDレンジ |
| A2 | **API-key/mode プリフライト** | 高（事故防止） | 低 | `mode=openai`＋key不在で即・実行可能なエラー |
| A3 | **live config＋Makefile＋runbook** | 高（再現性・ワンコマンド） | 低 | `configs/experiments/*_live.yaml`, `make exp-live-*`, `docs/LIVE_MEASUREMENT.md` |
| A4 | **R-2: S1 ハードケース増強（n≥2）** | 中 | 中 | 2個目の搬送済みID不可読個体＋全テスト緑 |
| A5 | **テスト＋ドキュメント更新** | 高 | 低 | cost/preflight 単体テスト、IMPROVEMENT/PROGRESS 同期 |
| A6 | **検証**（make check / scenario-validate / estimate 実行） | 高 | 低 | 全緑・live経路がstub/cacheで実証 |

### Phase B — 課金実行（明示承認ゲート）

| # | 項目 | 前提 |
|---|------|------|
| B1 | 概算提示 → **ユーザー go/no-go** → `make exp-live` 実行 → H1–H7 agent レポート生成 → IMPROVEMENT R-1 クローズ | `OPENAI_API_KEY` ＋ コスト承認 |

---

## 3. 実装順序

1. A1（cost.py＋CLI）→ A2（preflight）→ A3（config/runbook）：R-1 を「承認すれば即・安全に回る」状態にする。
2. A4（S1 ハードケース）：R-2。世界変更は最もリスクが高いため、全 s1 テスト＋反証ゲートで都度検証。
   万一アンカリング追跡が崩れる場合は「頑健性掃引を主指標に前面化」へフォールバック。
3. A5/A6：テスト・検証・ドキュメント同期。
4. B1：概算をユーザーに提示し、承認後にのみ課金実行。

---

## 4. 完了基準（DoD）

- **R-1 readiness（Phase A）**: `orx exp estimate` が T2/T5/T7 の概算を出力。`mode=openai`＋key不在で
  実行前に明確に失敗。live config がロードでき、stub/cache で live 経路がエンドツーエンドに通る。
  runbook と Makefile から 1 コマンドで起動可能。全テスト緑（新規テスト含む）・lint clean。
- **R-2**: S1 のハード事例が複数化し、反証予言（B0 recall<1 / B1 stale / OR-full=1）と
  replay-identity・SHACL・CQ・metamorphic の全 s1 テストが緑。
- **R-1 完了（Phase B・別ステップ）**: コスト承認後に live 実行し、H1–H7 各々に agent 検証列の数値が
  入り、ceiling/ablation と分離報告される（PROJECT.md §12 成功基準の (a)(b)(c) を agent 射程で充足）。
