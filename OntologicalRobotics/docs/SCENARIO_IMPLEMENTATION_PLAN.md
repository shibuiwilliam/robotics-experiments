# ORX シナリオ実装計画（SCENARIO_IMPLEMENTATION_PLAN）

> SCENARIOS.md v1.0（T8〜T14）を実装するための依存順作業分解。
> 優先順位: PROJECT.md > CLAUDE.md > SCENARIOS.md > 実装プロンプト。
> 各項目は `S{n}/T{suite}/X{infra}/H{hyp}` でタグ付け。進捗は `docs/PROGRESS.md` に追記する。
> **本計画は承認待ち。承認まではシナリオ実装に着手しない。**

## 0. 前提監査の結果（2026-06-13）

P0〜P2 の前提はすべて充足（実際は P0〜P5 完了済み、158 tests green・完全オフライン）。
基盤の欠落なし。X1〜X6・`orx scenario` CLI・シナリオレポート・`normative.ttl` は本計画で新設する
**スコープ内の新規作業**であり、前提の欠落ではない。

充足済みで再利用する資産:
- 評価方法論（PROJECT.md §7.2/§7.3）: `exp/episode.py`（記録→反実仮想リプレイ）、
  `exp/stats.py`（McNemar/Wilcoxon/ブートストラップ）、`oracle/{truth,fidelity}.py`、T4掃引。
- 条件機構: `exp/episode.py` の `CONDITIONS`（OR-full / OR-no-identity / OR-no-belief）、
  T2の `OR-reference`（決定的リファレンスソルバ）、T2の B0/B1 文脈構築（`exp/suites/t2.py`）。
- 同一性（C5, D1スコア）、能力台帳（`exp/suites/t3.py`）、SOP（`business/sop.py`）。

## 1. 設計決定（着手前に承認が必要）

### D1 — ディレクトリ規約の解釈（**要承認**）
SCENARIOS.md §3.3 は `tasks/suites/s{N}_{slug}/` に「タスク生成器・採点器・反証テスト」を置くと
規定する。しかし `orx scenario` CLI はインストール済みパッケージ `orx` から**インポート**して
シナリオ論理を実行する必要があり、`tasks/`（パッケージ外）の Python はCLIから import できない
（src-layout の技術的制約）。既存 T1〜T7 の生成器・採点器も `src/orx/exp/suites/` にある。

- **推奨**: スコープを分離する。
  - シナリオの**コード**（型付き: 生成器・採点器・条件別リファレンスソルバ・反証ヘルパ）→
    `src/orx/exp/suites/s{N}_{slug}/`（importable サブパッケージ、T1〜T7と一貫）。
  - `tasks/suites/s{N}_{slug}/` → シナリオの**宣言的仕様**（README・CQ参照・固定フィクスチャ）。
    PROJECT.md §11 が `tasks/suites/` を「T1〜T7定義」に充てる意図とも整合。
  - pytest 反証・真値導出テスト → `tests/scenarios/s{N}/`（§3.3 通り）。
  - configs → `configs/world/s{N}_*.yaml`, `configs/experiments/s{N}_*.yaml`（§3.3 通り）。
- 代替A: `tasks/suites/` を sys.path/namespace でimportable化 → CLI（installed package）から見えず脆い。不採用推奨。
- 代替B: scenario用に新トップ階層 `src/orx/scenarios/` → `orx.exp.suites` と二系統になり一貫性低下。

### D2 — 反証テストの方法論（**要承認**）
プロンプト要件「B0/B1 が stub モードで実行可能、かつ真に答えられるなら失敗する（リギング禁止）」を
満たすため、**LLM層ではなく情報/表現層で反証する**:
- 各条件が許す**情報・ツールのみ**を使う「条件別リファレンスソルバ」を実装する
  （B0=生データダンプ, B1=ロボット個別スキーマ＋ツール（共通オントロジー無し）, OR-full=世界グラフ
  ＋アイデンティティ・スレッド）。既存 T2 の `OR-reference` ソルバの一般化。
- 反証テストは「B0/B1 ソルバが指定クエリで**構造的に**不完全（参照連鎖が情報内に存在しない）」かつ
  「OR-full ソルバが成功」をアサート。LLM不要・決定的・stubで常時CI実行可能。
- これは完全な反証（理想的推論器でも B0 は解けない＝情報経路が無い）であり、リギングではない。
- LLMエージェント（T2形式）での測定は**追加の live 計測**として上に載せる（任意・要コスト承認）。

### D3〜D5 — シナリオ固有の意味論（各シナリオ着手時に2〜3案を提示して承認）
- **D3 (S2)**: `orx-st:possiblyContaminatedBy` の推移閉包セマンティクス（媒体の向き付き伝播、
  洗浄リセットの時刻意味論）。S2着手時に提示。
- **D4 (S6/S7)**: 視覚署名距離のパラメータ化（埋め込みコサイン閾値 vs クラス分離margin）。S6着手時。
- **D5 (S5)**: deontic規則のエンコード（SHACL制約 vs SPARQL ASK vs ルール表）。S5（Tier C）着手時。

軽微な決定は `docs/design/decisions.md` に随時記録する。

## 2. 共通インフラ X1〜X6（横断機能・再利用前提）

| ID | タグ | 内容 | 配置（推奨D1） | 利用 | テスト |
|----|------|------|----------------|------|--------|
| X1 | X1/C1,C10 | 割込み指示イベント＋再計画トリガ。`InterruptEvent{at_time, kind, payload}` を記録/リプレイに統合 | `common/schemas.py`, `exp/episode.py`, `sim/world.py` | S1,S3 | 注入と発火順の決定性 |
| X2 | X2/C1,C4 | 接触イベント蒸留: MuJoCo接触 → `orx-st:ContactEvent`（両当事者・時刻・来歴） | `sim/world.py`（接触読出）, `perception/contacts.py`（蒸留） | S2,S5 | 既知配置での接触検出の正しさ |
| X3 | X3/C2,C6 | 状態リセットイベント語彙（洗浄/施錠/認証）。TTL失効と統合 | `common/schemas.py`, ontology, `kg` | S2,S5 | リセットで状態主張が失効 |
| X4 | X4/C5 | ID無し個体の同一性解決経路（時空間×視覚署名のみ／台帳資産への anchoredTo） | `anchoring/anchorer.py` 拡張（既存ID無し経路の一般化＋台帳アンカー） | S4,S6,S7 | 記号無しでの同一性F1 |
| X5 | X5/C1 | 人間アクタ（規範・優先・引き渡しの相手）。簡易エージェント個体 | `sim/world.py`, `common/config.py` | S5,S7 | 接近・優先の決定性 |
| X6 | X6/C10 | 非対称コスト採点＋確信度閾値の人間委譲（escalation） | `exp/stats.py` または `exp/suites/_cost.py` | S6 | コスト行列・委譲の手計算一致 |

X1〜X4 は Tier A で実装（S1→X1, S2→X2/X3, S6→X4/X6）。X5/X6 は該当シナリオ着手時。
**コピペ禁止**: X-infra は単一実装を共有（X2はS2/S5、X4はS4/S6/S7で再利用）。

## 3. CLI・レポート拡張（Tier A 着手と同時）

| ID | タグ | 内容 |
|----|------|------|
| CLI-1 | C10 | `orx scenario list`（登録シナリオ一覧＋Tier/仮説/ステータス） |
| CLI-2 | C10 | `orx scenario demo s{n}`（stub・オフライン・エンドツーエンド＋シナリオレポート印字） |
| CLI-3 | C10 | `orx scenario run <experiment-config>`（条件×シードの実験、既存 exp 基盤へ委譲） |
| RPT-1 | C10 | `orx report` のシナリオテンプレート: 3必須節 = ①失敗予言の検証結果 ②頑健性曲線 ③トークン効率（SCENARIOS.md §6） |

入力検証・`--help`・失敗時非零終了は既存CLI規約に合わせる。

## 4. Tier A（S1 → S2 → S6）→ マイルストーン M-Scenario-A

### S1 — ロット回収（T8） `S1/T8/X1/H2,H6,H7`
- **前提**: P2完了, X1。
- **X-infra**: X1（割込み）。
- **語彙**: `orx-biz:Lot`, `orx-biz:memberOfLot`, `orx-biz:RecallOrder`, `orx-st:QuarantineZone`
  （+ SHACL + 新CQ：「ロットLの現在地」「回収対象の列挙」）。
- **業務データ**: WMS に lot テーブル（lot↔SKU↔個装ID）、回収指示テーブル（`business/db.py` 拡張）。
- **世界**: `configs/world/s1_lot_recall.yaml`（既存倉庫＋ロット属性＋ `quarantine` ゾーン＋進行中搬送）。
- **コード**: `src/orx/exp/suites/s1_lot_recall/{generator,scorer,reference,falsification}.py`。
- **真値導出（oracle）**: `oracle/scenarios/s1.py::recall_truth(sim_truth, wms) -> RecallTruth`
  （個装ID×lotテーブル結合で回収対象集合＋位置。ORコア非依存）。
- **タスク（T8）**: (a) 回収対象の全件列挙（位置・状態込み）, (b) 進行中タスクとの衝突判定＋再計画,
  (c) 隔離搬送の完遂, (d) 事後監査クエリ「回収対象が辿った位置履歴」。
- **反証テスト**: B0ソルバ（生ダンプ）が把持中/搬送中の箱のロット帰属を解けず recall<1、
  B1ソルバが観測個体↔WMSレコード対応に失敗、OR-full ソルバが recall=1。
- **指標**: 列挙 precision/recall、隔離完遂率、隔離完了時間、誤隔離数、トークン効率（H7）。
- **劣化ノブ**: `id_read_failure_rate`、割込みタイミング。
- **demo**: `uv run orx scenario demo s1`。
- **受入基準（§4 逐語）**: 「ノイズ0で列挙F1=1.0かつ完遂率100%。反証テストが green（B0/B1が指定
  クエリで失敗）。」

### S2 — アレルゲン交差汚染（T9） `S2/T9/X2,X3/H5`
- **前提**: X2, X3。**決定D3を着手時に承認取得**。
- **語彙**: `orx-st:ContactEvent`, `orx-cap:CleaningEvent`, `orx-biz:AllergenClass`,
  導出述語 `orx-st:possiblyContaminatedBy`（時刻依存・導出専用）（+ SHACL + CQ）。
- **世界**: `configs/world/s2_allergen.yaml`（物質タグ、トレイ＝中間媒体、洗浄ステーション、接触）。
- **真値導出（oracle）**: `oracle/scenarios/s2.py::contamination_closure(contacts, cleanings, t)`
  毎ティックの真の推移閉包。世界グラフ導出集合とトリプル単位照合（忠実度採点）。
- **タスク（T9）**: (a) 任意時刻の汚染可能性集合, (b) 「この把持は許可されるか」事前判定,
  (c) 汚染ゼロ制約下の最短段取り遂行, (d) 違反ゼロ完遂。
- **反証テスト**: B0/B1ソルバが推移閉包を計算できず(b)で偽陰性（汚染グリッパ使用許可）、
  OR−belief（来歴・時刻無し）が洗浄リセットを反映できず、OR-full が違反0。
- **指標**: 汚染集合 precision/recall（偽陰性を重く）、違反件数、生産スループット。
- **劣化ノブ**: **新規 `contact_miss_rate`**（接触見落とし→H5主検証）、洗浄観測遅延。
- **demo**: `uv run orx scenario demo s2`。
- **受入基準（§4 逐語）**: 「ノイズ0で汚染集合F1=1.0・違反0。見落とし率掃引で OR-full と OR−belief
  の頑健性曲線が分離。」

### S6 — リサイクル選別（T13） `S6/T13/X4,X6/H4`
- **前提**: X4, X6。**決定D4を着手時に承認取得**。
- **語彙・データ**: 規制オントロジー（製品クラス→処理経路規則; `orx-biz:` 拡張）、コスト行列コンフィグ
  （+ SHACL + CQ）。
- **世界**: `configs/world/s6_recycling.yaml`（外観クラス別物体カタログ、選別レーン、委譲シュート＝X6）。
- **真値導出（oracle）**: `oracle/scenarios/s6.py::correct_routing(catalog, rules)`
  真クラス×規制規則で正解経路を機械導出。
- **タスク（T13）**: (a) 視覚接地（候補クラス＋確信度）, (b) 規制推論で経路決定,
  (c) 確信度閾値で人間委譲, (d) コスト加重スコアで選別完遂。
- **反証テスト**: **OR-sym**（記号のみ）がID無し物体で接地不能（全件委譲→スループット崩壊）、
  **OR-vec**（ベクトルのみ）が規制推論不能で誤レーン（高コスト誤り）、OR-full のみ両立。
  → **新規アブレーション条件 OR-sym / OR-vec を `exp/episode.py` の CONDITIONS に追加**。
- **指標**: コスト加重誤分類スコア、見逃し率（高コスト誤り）、委譲率＋スループット、Brier・較正曲線。
- **劣化ノブ**: `occlusion_rate`, `pose_noise_sigma`（視覚難度）。
- **demo**: `uv run orx scenario demo s6`。
- **受入基準（§4 逐語）**: 「コスト加重スコアで OR-full が両アブレーションを有意に上回る。較正曲線の報告。」

### M-Scenario-A（Tier A ゲート）
S1・S2・S6 の各ゲート（下記 §7）が閉じた後、**3シナリオ×全条件の比較レポート**を
`orx report` で生成しユーザーに提示する（SCENARIOS.md §5）。

## 5. Tier B（S3 → S7 → S4）※ Tier A ゲート完了後

### S3 — 多ベンダー製造ライン（T10） `S3/T10/X1/H1,H3`
- **前提**: P3完了, X1。**再利用大**（T3/T5/T6 の業務文脈化）。
- 語彙: `orx-cap:ProcessRequirement` ＋ 能力契約マッチング述語。SOPに工程要求（重量帯/素材/精度）構造化。
- 世界: 品種パラメータ化ワーク、故障の「経年劣化モード」（成功率漸減）、ベンダーD合成スキーマ参入。
- 真値導出: `oracle/scenarios/s3.py`（工程要求×真能力で最良割当）。
- 反証: B1が語彙不一致で工程要求×能力突合せに失敗、能力統計無しが劣化機体へ割当継続。
- 受入基準（§4 逐語）: 「T3/T5/T6 の合成タスクと整合した結果が業務文脈でも再現されること。」

### S7 — 介護・所有関係（T14） `S7/T14/X4,X5/H2,H4,H6`
- **前提**: X4, X5。**決定D4（距離パラメータ）を流用/確認**。S6資産（X4）再利用。
- 語彙: `orx-biz:Person`, `orx-biz:owns`（情報的関係・観測不可能）, `orx-biz:assignedRoom`（+SHACL+CQ）。
- 世界: 類似外観小物群（視覚署名距離を制御パラメータ化）、居室、最終目撃履歴。
- 真値導出: `oracle/scenarios/s7.py`（真の所有者種付け↔正解個体）。
- 反証: B0/B1が「誰のものか」の情報経路を持たない、OR-vecが所有を扱えず類似品で誤配送。
- 受入基準（§4 逐語）: 「類似度掃引で OR-full の優位領域を曲線で特定。」

### S4 — プラント点検（T11） `S4/T11/X4/H2,H5`
- **前提**: X4（台帳資産への ID無しアンカー）。S7と並行可。
- 語彙: 資産台帳（位置ラフ座標・系統トポロジ）、点検SOP。`orx-biz:Asset` 等（+SHACL+CQ）。
- 世界: プリミティブ設備群（外観バリエーション）、2視点ロボット、異常の視覚表現、矛盾観測。
- 真値導出: `oracle/scenarios/s4.py`（知覚個体↔台帳資産の真対応、真異常状態）。
- 反証: B0/B1が台帳↔観測対応を作れず「V-205の状態は」に無回答、OR-symが外観バリエで対応失敗。
- 受入基準（§4 逐語）: 「台帳対応付けが位置ノイズ掃引下で OR-full > アブレーション。」

## 6. Tier C（S5）※ Tier B ゲート完了後・ユーザー明示が無い限り着手しない

### S5 — 病院内搬送（T12） `S5/T12/X2,X3,X5/規範層,H5`
- **前提**: P5, 規範層（新規 `ontology/domains/normative.ttl`）。**決定D5を着手時に承認取得**。
- 新設: `normative.ttl`（義務/禁止/許可、適用条件＝物分類×区画×時間帯×機体認証）、計画時規範チェックAPI。
- X2, X3, X5。custody連鎖の来歴記録、監査クエリ。
- 真値導出: `oracle/scenarios/s5.py`（許可搬送集合、custody連鎖の真値）。
- 反証: 規範層無しが施錠区画外を通過（違反）、来歴無しが監査クエリに完全回答できない。
- 受入基準（§4 逐語）: 「違反ゼロ＋監査完全回答＋効率劣化の定量化。**監査可能性**という独自評価軸の確立。」

## 7. シナリオ・ゲート（各シナリオ共通のハードゲート）

各シナリオは以下が**すべて**満たされて初めて完了（プロンプト §4）:
1. 全テストスイートがオフラインで green。
2. SCENARIOS.md §4 の**受入基準**を満たす測定値を `docs/PROGRESS.md` に記録。
3. **反証テストが green**: B0/B1（および指定アブレーション）が指定クエリ/タスクで失敗し、
   OR-full が成功（すべて stub モードで実行可能）。
4. `uv run orx scenario demo s{n}` がオフラインでエンドツーエンド実行＋レポート印字。
5. CLAUDE.md §7 規約でコミット（メッセージに `S{n}/T{suite}`）。

## 8. テスト戦略（既存4種＋シナリオ必須種別）

- 既存: 単体 / CQ回帰 / SHACL検証 / リプレイ同一性 / アーキテクチャ不変条件（import-linter, AST）。
- **追加（tests/scenarios/s{N}/）**: ①反証テスト（B0/B1構造的失敗）②真値導出ユニットテスト
  （手構築フィクスチャ対 oracle）③シナリオ統合（縮小版エンドツーエンド）。
- 語彙追加は**必ずCQ追加とセット**（CQ無き語彙はリグレッション; SCENARIOS.md §6）。
- import-linter 契約を拡張: `oracle.scenarios` も ORコア非import を強制。

## 9. 新規依存

- 想定なし（既存 mujoco/pyoxigraph/pyshacl/lancedb/scipy/pydantic/typer で完結）。
  もし必要が生じれば理由を添えてコミットメッセージに明記し、重量級は事前承認を得る。

## 10. リスクと前倒し対応

- 接触蒸留（X2）の性能: 物理ステップ毎の接触読出はシムループ内I/O禁止（不変条件4）に抵触しうる。
  → 接触は知覚ティック境界でバッチ読出し、蒸留は知覚周期で行う。
- 反証の「リギング」疑念: 条件別ソルバは各条件が許す情報のみを引数に取り、それ以外へアクセス
  しないことをAST/型で担保（B0ソルバは世界グラフを引数に取らない、等）。
- OR-sym/OR-vec の追加で条件マトリクスが増える → `CONDITIONS` を宣言的に保ち、レポートが
  全条件を自動列挙するよう設計。
