# ORX 是正計画（REMEDIATION_PLAN）

> IMPROVEMENT.md（2026-06-13 監査）の全項目を駆動して 0 にする計画。
> 基準: PROJECT.md（H1–H7・§5.2不変条件・§7評価・DoD）> CLAUDE.md > SCENARIOS.md > IMPROVEMENT.md。
> live 計測（item 7, OPENAI_API_KEY＋コスト承認要）は本計画の対象外（オフラインで完結する全項目を実装）。
> 進捗は本ファイルと docs/PROGRESS.md に記録。各ユニットは「宣言→実装→テスト→緑→コミット」。

## 最優先原則（C1 由来・全作業を貫く）
計測射程を**3カテゴリで明示分離**して報告する。仕様化:
- **ceiling**（表現上限）: 決定的リファレンスソルバ／期待SPARQL-SQL。LLM不要。
- **ablation**（機構アブレーション）: 例 anchoring/belief on↔off、OR-sym/OR-vec。
- **agent**（エージェント検証）: 実LLM/実埋め込み。**現状すべて未実行（live）**。
ceiling/ablation の数値を仮説確認として提示しない。stub 入力に対する統計値は引用しない。

## ユニット一覧（優先順＝IMPROVEMENT.md §5）

| U | closes | 内容 | 主な対象 | 証明 |
|---|--------|------|---------|------|
| U1 | C1 | 計測射程の表記分離（共通scopeヘルパ＋全レポート＋PROGRESS表＋README） | `exp/scope.py`(新), `exp/report.py`, `exp/runner.py`, `exp/suites/s1*/runner.py`, docs, README | レポートに scope 凡例＋agent=NOT RUN 明記 |
| U2 | C2 | stub 入力に対する無意味 p 値を是正（T7 vector-rag 等を harness-only 表記、H4 主張から除外） | `exp/runner.py` T7/T2/T5 render | stub時 p値を「harness検証(無意味)」表記 |
| U3 | H-1 | 新語彙のクラス固有 SHACL shape（S1: Lot/memberOfLot/RecallOrder/QuarantineZone, S2: Contact/Reset/possiblyContaminated）＋適合/不適合テスト | `ontology/shapes/scenarios.ttl`(新), `tests/scenarios/*/test_shapes_*.py` | 不適合フィクスチャを拒否 |
| U4 | H-2 | シナリオ出力に git_commit・model snapshot スタンプ | `exp/scenario.py`, `exp/suites/s1*/runner.py` (S1Result), S2/S6も | results.json に4スタンプ |
| U5 | H-3 | シナリオ replay-identity テスト（同一runを2回評価しbyte一致） | `tests/scenarios/s1/test_replay_identity_s1.py` | 正準JSONバイト一致 |
| U6 | H-4 | メタモルフィック（決定的経路の識別子リネーム不変性） | `src/orx/exp/metamorphic.py`(新), `tests/scenarios/*/test_metamorphic_*.py` | リネーム下で回答集合不変 |
| U7 | M-1..M-4,L-1,L-2 | 整理: docs/scenarios に superseded 注記, .gitignore(reports/proposals), 残骸削除, .gitkeep削除, ハードケース増 | .gitignore, docs/scenarios/README, configs | tree クリーン |
| U8 | C3(S2) | S2 アレルゲン完成（X2接触蒸留・X3洗浄・world/exp config・3条件ソルバ・反証・掃引・CQ・SHACL・demo・gate） | `exp/suites/s2_allergen/`, configs, ontology, tests/scenarios/s2 | 受入: 汚染F1=1.0・違反0・見落とし掃引で曲線分離・反証green |
| U9 | C3(S6) | S6 リサイクル（OR-sym/OR-vec 条件追加＋双対表現経路・X4 ID無し・X6 非対称コスト・gate） | `exp/episode.py` CONDITIONS, `exp/suites/s6_recycling/`, configs, tests/scenarios/s6 | 受入: コスト加重で OR-full>両アブレ・反証green |
| U10 | C3(M-A) | M-Scenario-A 比較レポート（S1+S2+S6×全条件） | `exp/scenario.py` または新 report | 3シナリオ比較md生成 |

## 受入（各シナリオ共通・SCENARIOS.md §4/§6・DoD）
全テスト緑(offline)・受入数値を PROGRESS.md 記録・反証 green（baseline/ablation が指定クエリで構造的失敗、
OR-full 成功, no-rigging）・`orx scenario demo s{n}` 緑・3スタンプ＋git/model・SHACL＋CQ・コミット。

## 非対象（明示）
- live 計測（T2 agent3条件・T7 vector-rag 実埋め込み・T5 llm・H7トークン・H1–H7のagent検証）。
  OPENAI_API_KEY＋コスト承認が前提。完了後 mode=cache で再現可能化する手順は PROGRESS に記載済み。
