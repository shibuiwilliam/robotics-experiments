# SCENARIOS.md — Multi-World Search (MWS) シナリオ設計書

> **このドキュメントの役割**
> 本書は MWS の検証シナリオ（`PROJECT.md` §9 の7件）を **Claude Code で実装可能な粒度** まで具体化する設計書です。
> 上位の戦略・仮説・成功基準は `PROJECT.md`、開発規約（mock既定・クラウド境界・依存方向・seed・埋め込み空間規律・DoD）は `CLAUDE.md` に従います。
> 本書はその2つと矛盾しないことを前提とし、重複する説明は最小化します。

---

## 1. 共通シナリオフレームワーク

すべてのシナリオは共通の土台の上に実装する。シナリオ固有の差分だけを各節で定義する。

### 1.1 配置と登録

- 実装は `mws/scenarios/<scenario_key>/` に置く。
- 各シナリオは共通基底 `BaseScenario` を実装し、レジストリに名前で登録する。
- 実行は統一 CLI: `uv run python -m mws.cli scenario run --name <scenario_key> --seed <N>`。

### 1.2 ライフサイクル（BaseScenario の標準フェーズ）

すべてのシナリオはこの順で動く。各フェーズは決定的（同一 seed で同一結果）。

1. `setup(seed, config)` — MuJoCo 世界と合成業務データを構築し、`RUN_ID` を発行。
2. `seed_memory()` — 事前条件となるアトム（既存スキル実演・過去観測・業務レコード）を投入。
3. `inject()` — 検証対象の事象を注入（異常・記録ドリフト・新規物体・危険・遮蔽・注文等）。
4. `perceive()` — センサーから観測アトムを生成し、`core` 経由で正規化、`storage` に格納。
5. `index()` — オフライン索引化（mock 既定。live 時のみ Gemini Embedding 2 / Batch）。
6. `act()` — 消費者（ADK エージェント・検索拡張VLA・制御）が MWS へクエリし行動する。
7. `evaluate()` — 指標を算出し `runs/<RUN_ID>/` に成果物と manifest を書き出す。
8. `teardown()` — リソース解放。

### 1.3 決定性と mock

- 既定 `MWS_CLOUD_MODE=mock`。クラウド（LLM/Embedding）は決定的スタブで代替し、鍵なしで全フェーズが完走する。
- mock の LLM/VLA は「決定的に妥当な行動」を返す軽量スタブ。live への差し替えはアダプタ境界でのみ行う。
- すべての確率的処理は `seed` を経由。シナリオ実行は run manifest を必ず残す（`CLAUDE.md` §9）。

### 1.4 監査（来歴）

- 各シナリオは全クエリ・全行動・全書き戻しを `runs/<RUN_ID>/audit.jsonl` に追記する。
- 監査行は最低限: 時刻・RUN_ID・アクター・操作種別・対象アトム/実体ID・使用インデックス・投影種別・来歴。

### 1.5 関連性ラベルの自動生成（評価の核）

- MuJoCo の真値（物体ID・姿勢・属性）と業務データの真値リンクから、クエリごとの正解アトム集合を自動生成する。
- これにより人手アノテーションなしで Recall@k / MRR / nDCG を算出する（`PROJECT.md` §5.3 / §10）。

### 1.6 クエリ仕様の記法

各シナリオの MWS クエリは次の形で定義する（実装は `retrieval` のクエリプランナに対応）。

```
Query(
  consumer   = ops_agent | manipulator_vla | control | analytics | reactive,
  intent     = 自然文または構造化意図,
  indices    = {spatial?, temporal?, semantic?, symbolic?, structured?},
  filters    = {entity_id?, region?, time_window?, modality?, policy?},
  projection = text+provenance | pose+tensor | numeric | aggregate,
  qor        = {max_latency, freshness, authority, scope}
)
```

---

## 2. 共通データモデル規約

### 2.1 物理世界（MuJoCo）の意味スキーマ

- 各 body に一意の `entity_id`（例 `pump_07`, `valve_03`, `sku_A100`）と意味属性（種別・ロット・状態）を付与。
- センサーは観測アトムの源: RGB-D カメラ、自己受容、接触、レンジファインダ、スカラー診断チャンネル（振動・温度プロキシ）。
- 物体は単純形状（箱・円柱）で意味ラベルを担えば十分（研究プロトタイプ・`PROJECT.md` Non-Goals 準拠）。
- 動態注入（移動・追加・除去）と時間膨張（高速世界）は設定で切替。

### 2.2 合成業務データのスキーマ（`mws/business/`）

> **実装注記（2026-06-11）**: 現行の7シナリオは業務データを各 `mws/scenarios/s*/data.py` の
> 専用ジェネレータで生成しており、`mws/business/` のスキーマ/ジェネレータは**ライブラリ
> （シナリオ未配線）**である。下表は両者が従うべき最小スキーマの規範として維持する。

外部システムは実接続せずスタブ。最小スキーマを `entity_id` で物理実体に紐づける。

| ソース | レコード（最小フィールド） |
|---|---|
| CMMS（保全） | `work_order_id, entity_id, date, symptom, action, technician` |
| ERP/在庫 | `part_id, entity_id, on_hand, location, reorder_point` |
| WMS（倉庫） | `entity_id, recorded_count, location, last_updated` |
| MES/品質 | `lot_id, entity_id, station, defect_flag, timestamp` |
| 資産台帳 | `asset_id, entity_id, status(in_service|maintenance|retired), location` |
| 受注 | `order_id, sku, qty, customer, status` |
| 名簿/シフト | `person_id, role, on_call_window` |
| 文書 | SOP / マニュアル（トルク等の数値仕様）/ SDS（物質安全）。`entity_id`/`substance_id` 参照 |

---

## 3. 各シナリオ仕様

各シナリオは共通テンプレートで記述する。仮説・機構の番号は `PROJECT.md` §8/§9 を参照。

### シナリオ1｜設備保全の多主体引き継ぎ（フラッグシップ）

- **目的（業務価値）**: MTTR 短縮・スキル再利用・説明可能な監査証跡。
- **仮説 / 機構**: H1, H3, H8 / クロスモーダル・ソース融合 ◎、検索拡張VLA ◎、多インスタンス引き継ぎ ◎、来歴/監査 ◎。
- **アクター責務**:
  - PatrolRobot: 巡回し `pump_07` の振動・熱を観測、異常アトムを生成し実体ノードに紐づけ。
  - OpsAgent(ADK): 融合想起→作業指示生成→部品在庫・人員確認→予定確定。
  - MobileManipulator(別形態, VLA): 幾何・トルク仕様・既存スキル実演を想起して施工。
- **世界**: 1室に `pump_07`(診断チャンネル付), `valve_03`, 作業ベンチ。形態の異なる2ロボ。
- **業務/文書**: `pump_07` の CMMS 履歴、ERP 部品在庫、トルク仕様の SOP、技師シフト。
- **seed_memory**: 過去に別ロボが教示した「弁を緩める」VLA 実演アトムを1件投入。
- **inject**: `pump_07` に決定的な複合異常（振動↑＋温度↑）。
- **MWS クエリ**:
  - OpsAgent → `indices={semantic,symbolic,structured,temporal}, filters={entity_id=pump_07}, projection=text+provenance, qor={authority=high}`。
  - Manipulator → `indices={spatial,symbolic}+procedural(skill), filters={entity_id=pump_07,valve_03}, projection=pose+tensor, qor={max_latency=low}`。
- **成功基準/指標**: 正しいマニュアル＋履歴＋スキルの Recall@k、施工タスク成功率、レイテンシ分解、監査完全性、**異形態へのスキル転移成立**。
- **受け入れテスト（mock・決定的）**: 異常注入で融合クエリがマニュアル＋CMMS履歴＋スキル実演を返す／Manipulator が想起スキルで施工成功／audit.jsonl が全工程を記録。
- **CLI**: `scenario run --name maintenance_handoff`。

### シナリオ2｜物理と原本記録の調停

- **目的**: 在庫精度・物理↔デジタルの真実ギャップ解消。
- **仮説 / 機構**: H4, H9 / 物理対記録の整合・鮮度 ◎、多観測者融合 ◎、外部書き戻し ◎。
- **アクター責務**:
  - InventoryRobots(複数): 棚の個数・資産状態を観測（視点・ノイズ差あり）。
  - ReconAgent(ADK): 来歴・信頼度・鮮度・観測一致度で裁定→書き戻し or 差異チケット起票。
- **世界**: 個数を持つ棚（真値 ≈30）、台帳上「整備中」だが床にある資産1点。複数観測者。
- **業務**: WMS 記録（例 50）、資産台帳（status 不整合）。
- **inject**: WMS をドリフトさせ、観測者間に決定的な不一致（ノイズ）を与える。
- **MWS クエリ**: ReconAgent → `indices={structured,semantic,temporal}, filters={entity_id}, projection=text+provenance, qor={freshness=strict}`；融合は来歴重み付き多観測者推定。
- **成功基準/指標**: 調停精度、融合カウント誤差 < 単一観測者誤差（H9）、陳腐ヒット率、書き戻し/起票の正確性。
- **受け入れテスト**: 決定的ドリフトで融合推定が単一観測を上回る／不整合資産に差異チケット生成／書き戻し内容が正しい。
- **CLI**: `scenario run --name physical_record_reconciliation`。

### シナリオ3｜群れによる弱信号の集合的発見

- **目的**: 事後対応でなく予防・品質先取り・サプライヤ説明責任。
- **仮説 / 機構**: H5 / consolidation ◎、クロスソース融合 ◎、standing query ○、時間検索。
- **アクター責務**:
  - Swarm: 時間・インスタンス横断で微小欠陥を観測（個々は無害）。
  - QualityAgent(ADK): consolidation の意味記憶からパターンを読み、MES/サプライヤと相関、standing query 登録。
- **世界**: 複数物体、一部に `lot=L + micro_defect` 属性。時間膨張で多数エピソード生成。
- **業務**: MES ロット記録、サプライヤ台帳。
- **inject**: 欠陥を決定的にロット L に集中させ、観測を時間・インスタンスに散らす。
- **MWS クエリ**: QualityAgent → `indices={semantic,temporal,symbolic,structured}, projection=aggregate`；consolidation がクラスタ→相関。
- **成功基準/指標**: パターン発見（ロットL関連のクラスタ純度/recall）、consolidation の圧縮率↔recall保持、standing query 登録の正確性。
- **受け入れテスト**: 決定的シードで consolidation がロット L を表面化／QualityAgent が L 監視の standing query を登録。
- **CLI**: `scenario run --name collective_weak_signal`。

### シナリオ4｜新規SKU・新規現場の即時立ち上げ（転移）

- **目的**: 立ち上げ高速化・一回の実演を群れで償却。
- **仮説 / 機構**: H1 / 検索拡張VLA・スキル伝播 ◎、多インスタンス ◎、連合、製品マスタ連結。
- **アクター責務**:
  - InstanceA: 新規 SKU を1回の遠隔操作実演 or 探索でスキル/地図アトム化。
  - SwarmB: 想起して再利用。製品マスタが搬送先を駆動。
- **世界**: どのロボも未知の新規 SKU クラス、未経験の現場地図。
- **業務**: 製品マスタ（SKU→搬送先）。
- **inject**: 新規クラス。**実演アトムの有無をトグル**（A/B 比較の独立変数）。
- **MWS クエリ**: SwarmB → `indices={semantic,symbolic}+procedural(skill), filters={sku_class}, projection=pose+tensor`。
- **成功基準/指標**: 転移ゲイン（実演あり vs なしの成功率差）、コールドスタート時間、サンプル効率。
- **受け入れテスト**: 実演アトム有無の A/B で、共有記憶ありの群れ成功率が有意に向上。
- **CLI**: `scenario run --name new_sku_rampup`。

### シナリオ5｜インシデント対応のリアルタイム協調

- **目的**: 安全・応答速度・コンプライアンス。
- **仮説 / 機構**: H6, H2 / standing query ◎、能動的好奇心 ◎、多インスタンス協調 ◎、クロスソース(SDS+平面図+名簿) ◎、鮮度、来歴。
- **アクター責務**:
  - AnyRobot: 危険事象（物質X漏出/転倒/故障）を検知。
  - IncidentAgent(ADK): SDS/SOP＋平面図/最寄り出口＋当番名簿を融合想起→人へ通知＋最寄りロボ派遣。遮蔽領域は好奇心ループで偵察してから計画確定。
  - RealTime 4D シーングラフ: 誰が何処にいるかを保持。
- **世界**: 物質タグ付き「漏出」物体、出口を持つ複数室、状態不明の遮蔽領域。
- **業務/文書**: SDS、安全SOP、建物平面図、当番名簿。
- **inject**: 危険事象＋遮蔽領域＋最寄りロボ選択。
- **MWS クエリ**:
  - standing query: 「いずれかのインスタンスが漏出を観測したら発火」。
  - IncidentAgent → `indices={semantic,spatial,structured}, filters={substance_id,region}, projection=text+provenance, qor={freshness=strict,max_latency=low}`。
  - Curiosity: 遮蔽でヒット不足→探索タスク発行→新規観測でギャップ充足。
- **成功基準/指標**: 応答レイテンシ、SDS/出口/名簿の想起正確性、好奇心ループによる計画失敗率低下（H6）、協調。
- **受け入れテスト**: 危険注入で standing query 発火／ギャップ時に偵察ロボ派遣／計画が SDS＋出口＋名簿を使用。
- **CLI**: `scenario run --name incident_response`。

### シナリオ6｜反実仮想に基づく現場安全判断

- **目的**: リスク低減・事故/破損回避・行動の正当化。
- **仮説 / 機構**: H6 / 反実仮想・sim検索 ◎、検索拡張の意思決定 ◎、クロスソース(SOP上限+仕様)、安全。
- **アクター責務**:
  - SafetyAgent / VLA: 危険操作の直前に MWS へ反実仮想ロールアウトを問い合わせ、安全行動を選択。
  - Sim バックエンド: デジタルツインをフォークしロールアウト、結果アトムを生成。
- **世界**: 危険な構成（高積みの荷／加圧弁など）。MuJoCo の状態フォークが反実仮想エンジンを兼ねる。
- **業務/文書**: SOP のリスク上限、機器仕様。
- **inject**: 危険構成＋フォーク/ロールアウト機構。
- **MWS クエリ**: VLA → `intent=「この操作は安全か」, indices={semantic,structured}+counterfactual(sim), projection=numeric+provenance`。結果アトム（荷崩れする/しない）を想起。
- **成功基準/指標**: 安全結果（破綻回避率）、意思決定品質、ロールアウトコスト、反実仮想アトムが想起・利用される。
- **受け入れテスト**: 決定的フォークで、ロールアウトが破綻を予測する操作を回避／結果アトムが格納・想起可能。
- **CLI**: `scenario run --name counterfactual_safety`。

### シナリオ7｜注文から充足までのエンドツーエンド

- **目的**: 注文精度・幽霊在庫失敗の削減・自動化。
- **仮説 / 機構**: H4, H8 / クロスシステム統括 ◎、物理対デジタル整合・鮮度 ◎、消費者適応投影 ○、多インスタンス ○、外部書き戻し ◎。
- **アクター責務**:
  - OrderAgent(ADK): 受注を統括。WMS だけでなくロボ観測で物理在庫を確認。
  - PickingVLA / TransportRobot: ピッキングと搬送。
  - External: 受注・WMS・調達・ERP（スタブ）。
- **世界**: SKU を並べた棚、対象注文、**破損品を1点注入**。
- **業務**: 受注、WMS（幽霊在庫: 記録ありだが物理は欠品/破損）、調達、ERP。
- **inject**: 注文イベント＋幽霊在庫/破損品。
- **MWS クエリ**: OrderAgent → `indices={structured,spatial,semantic,temporal}, filters={sku,entity_id}, projection=text+provenance/numeric, qor={freshness=strict}`；物理観測が WMS を上書き。
- **成功基準/指標**: 注文精度、幽霊在庫起因の失敗削減、E2E 成功率、調達/通知/ERP更新の正確性。
- **受け入れテスト**: 破損品注入で物理確認が WMS を上書き／調達再発注＋顧客通知＋ERPクローズが実行。
- **CLI**: `scenario run --name order_to_fulfillment`。

---

## 4. トレーサビリティ・マトリクス

| シナリオ | 主仮説 | 主機構 | シナリオ固有メトリクス |
|---|---|---|---|
| 1 保全引き継ぎ | H1,H3,H8 | 融合検索/検索拡張VLA/引き継ぎ/監査 | 異形態スキル転移成立・監査完全性 |
| 2 記録調停 | H4,H9 | 物理↔記録/多観測者融合/書き戻し | 調停精度・融合誤差<単一・陳腐率 |
| 3 弱信号 | H5 | consolidation/相関/standing query | クラスタ純度・圧縮↔recall保持 |
| 4 立ち上げ転移 | H1 | スキル伝播/多インスタンス/連合 | 転移ゲイン・コールドスタート時間 |
| 5 インシデント | H6,H2 | standing query/好奇心/協調 | 応答レイテンシ・計画失敗率低下 |
| 6 反実安全 | H6 | 反実仮想sim検索/安全 | 破綻回避率・ロールアウトコスト |
| 7 受注充足E2E | H4,H8 | クロスシステム/物理↔デジタル | 幽霊在庫失敗削減・E2E成功率 |

---

## 5. 実装順序と受け入れゲート

`PROJECT.md` のロードマップに整合させる。

1. **共通フレームワーク**（§1〜§2）— BaseScenario・登録・関連性ラベル自動生成・監査・mock 経路。ゲート: 空シナリオが完走し manifest を残す。
2. **シナリオ1（縦の貫通線）** — 全層を一度貫く。ゲート: end-to-end が mock で成功＋監査完全＋指標出力。
3. **シナリオ2・3** — 新規性（調停・弱信号）。ゲート: 各受け入れテスト緑。
4. **シナリオ4** — 転移（Phase 2 実験と兼用）。ゲート: A/B で転移ゲイン測定。
5. **シナリオ6** — sim ネイティブ・デモ映え。ゲート: 反実仮想回避を実証。
6. **シナリオ5** — 反応性＋好奇心の協調。ゲート: 発火・偵察・融合計画。
7. **シナリオ7** — クロスシステム E2E。ゲート: 物理上書き＋外部連携完遂。

各シナリオは `CLAUDE.md` §15 の DoD を満たして初めて完了とみなす（ruff/型/pytest 緑、ゴールデンテスト、seed・manifest、クラウド境界・依存方向・埋め込み空間の遵守）。

---

## 6. シナリオ共通の受け入れチェックリスト

- [ ] `scenario run --name <key> --seed 0` が mock・鍵なしで完走する
- [ ] 同一 seed で決定的（再実行で同一結果）
- [ ] `runs/<RUN_ID>/` に manifest・metrics・audit.jsonl を出力
- [ ] 真値由来の関連性ラベルで検索指標（Recall@k 等）を算出
- [ ] シナリオ固有の成功基準を満たすアサーションを持つ
- [ ] クラウド呼び出しは embedding/agents のみ・ホットパスにクラウド埋め込みなし
- [ ] 依存方向・埋め込み空間規律を遵守