# オントロジー変更履歴

semver の判定基準（CLAUDE.md「オントロジー変更の手順」）:

- **MAJOR**: クラス・関係の削除、意味変更。ADR と再接地計画が必須。
- **MINOR**: 加法的な追加。既存プローブ・クエリ・`schemas/` の後方互換を契約テストで証明する。
- **PATCH**: 注釈（label / definition / コメント）のみの変更。

非推奨は `owl:deprecated true` と `dcterms:isReplacedBy` を付け、2 MINOR 版残す。

## 0.5.0 — 2026-10-03

MINOR（加法的な追加）。既存のクラス・関係・形状に変更はない。

### 追加（中核 — `ontology/core/sbp-core.ttl`）

- `sbp:Role`（Class）: 承認・配賦の宛先になる業務ロール。BFO の role（BFO_0000023）の**下位
  クラス**として宣言し、再定義しない（設計原則 P10）。承認者を個人ではなくロールで指定するのは、
  担当交代でアクションが実行不能にならないようにするためである（設計書 §9.3）。
- `sbp:compensatedBy`（ObjectProperty）: ActionType → ActionType。補償が宣言されていない
  アクション型は登録を拒否する（アクション層ルール）ため、**関係をオントロジー側に持つ**。
  YAML 側だけに持つと、補償の有無がオントロジーから読めない。

### 追加（ドメイン — `ontology/domain/equipment-maintenance.ttl`）

- アクション型 4 個体: `CreateEmergencyInspection` ⇄ `CancelWorkOrder`、
  `CorrectOperationLog` ⇄ `RevertOperationLogCorrection`（互いに補償関係）。
- ロール 2 個体: `MaintenanceLead`, `ProductionSupervisor`。

意味契約の本体（前提・期待効果・リスク級・承認条件）は `actions/*.yaml` に置く。閾値の調整が
オントロジーの版上げになるのを避けるためで、オントロジーに置くのは「存在」と「補償関係」だけ。

## 0.4.0 — 2026-10-03

MINOR（加法的な追加）。既存のフルーエント型・事象型・形状に変更はないため、プローブ・
クエリ・`schemas/` の後方互換は保たれる（契約テスト `test_ontology_compat.py` で確認）。

### 追加（中核 — `ontology/core/sbp-core.ttl`）

実体間結合（設計書 §8.1「関係制約」）を宣言で持つために必要な 2 プロパティ + 1 クラス +
2 個体。コード側（`sbp.reasoning.coupling`）は結合の構造をここから読む。

- `sbp:partOf`（ObjectProperty, TransitiveProperty）: 物理実体の部分−全体関係。BFO の
  `part of`（BFO_0000050）の**下位プロパティ**として宣言し、再定義しない（設計原則 P10）。
  設計書 §8.1 の「強い関係」の一方である。
- `sbp:aggregatesAs`（ObjectProperty）: FluentType → AggregationRule。部分の状態が全体へ
  どう集約されるかを**フルーエント型ごとに**宣言する。`Running` は all（全部が動いて初めて
  系が動いている）、`Faulted` は any（どこか一つ壊れれば系は壊れている）——この違いを
  コードの条件分岐ではなくオントロジーに置く。
- `sbp:AggregationRule`（Class）と個体 `sbp:AggregateAll` / `sbp:AggregateAny`。

### 追加（ドメイン — `ontology/domain/equipment-maintenance.ttl`）

- 既存 7 フルーエント型に `sbp:aggregatesAs` を付与（注釈的追加。型は増やさない）。

## 0.3.0 — 2026-10-03

semver 判定: **MINOR**（``initiates`` / ``terminates`` の加法的追加のみ。既存の型・関係の
削除や意味変更はない）。既存プローブは無く、`schemas/` の生成物も変わらないため後方互換の
破壊はない。再接地は不要。

### 追加（L2 設備保全 — `Accessible` を変化させる事象）

M2-06（事象昇格）の実装中に、SynthWorld の不変条件テスト（「合成世界は事象計算で説明できる
状態変化しか作らない」）が**オントロジーの欠落**を検出した。`Accessible` は FluentType として
定義され、設計書 §9.1 でアクションの物理前提（`P(Accessible(?pump)) >= 0.8`）に使われるのに、
**それを成立・不成立にする事象型が 1 つも無かった**。変化させられないフルーエントは
事象駆動の信念更新に乗らず、アクションの前提としても永久に初期値のままになる。

物理に即して次を追加した。

- `StartCmd` → `terminates Accessible`（稼働中は人が近づけない）
- `StopCmd` → `initiates Accessible`（停止すれば近づける）
- `Fault` → `initiates Accessible`（故障停止でも近づけるようになる）
- `MaintStart` → `initiates Accessible`

あわせて `make ontology-check` に検査を 1 つ加えた（項目 7）: **すべての FluentType は
少なくとも 1 つの EventType に変化させられること**。これは既存の `EventTypeShape`
（すべての EventType は少なくとも 1 つの FluentType を変化させる）の双対である。片側だけ
検査していたために今回の欠落が通っていた。

## 0.2.0 — 2026-10-03

semver 判定: **MINOR**（加法的追加のみ。削除・意味変更なし）。既存プローブ・クエリは無いため
後方互換の破壊はなく、再接地も不要。

### 追加（L1 中核 — 信念メタデータの述語。ADR-0005）

RDF 1.2 の triple term と `rdf:reifies` でアサーションを表すため、reifier に付ける述語を定義した。
開いている区間は `validTo` / `txTo` のトリプルを**置かない**ことで表す（null リテラルを使わない）。

- 二時制: `validFrom`, `validTo`, `txFrom`, `txTo`
- 身分と確信度: `modality`, `confidence`
- 来歴: `provenance`, `sourceReliability`, `ontologyVersion`
- 予測: `horizonOrigin`, `leadTime`
- 隔離と文脈: `scenario`, `inSituation`
- 様相の個体（`modality` の値域、不変条件 3 の 6 値）: `Observed`, `Inferred`, `Predicted`,
  `Planned`, `Normative`, `Hypothetical`

用語数は 78 → 91。

## 0.1.0 — 2026-10-03

初版。semver 判定: 初版のため該当なし。

### 追加（L1 中核 — `ontology/core/sbp-core.ttl`）

- 注釈プロパティ: `layer`, `owner`
- 物理実体: `PhysicalEntity`, `Asset`, `MaterialItem`, `Person`, `Carrier`,
  `UnidentifiedPhysicalObject`, `Location`, `Zone`, `Anchor`
- 生起物: `PhysicalEvent`, `BusinessEvent`, `ActionEvent`, `Situation`
- ICE: `Assertion`, `FluentType`, `EventType`, `Record`, `PlanItem`, `Norm`, `ActionType`,
  `Expectation`, `Discrepancy`, `IdentityClaim`, `Track`, `Probe`, `WorldModelVersion`,
  `OntologyVersion`, `DataSource`, `Forecast`
- 関係: `hasParticipant`, `locatedIn`, `containedIn`, `initiates`, `terminates`,
  `mutuallyExclusiveWith`, `subjectType`, `groundedBy`, `evidenceFor`, `concerns`, `boundTo`,
  `expects`, `conditionedOn`, `targets`
- データ・幾何・量: `assetId`, `sku`, `discrepancyKind`, `probeStatus`, `geometry`, `quantity`,
  `validInterval`

採用した標準（再定義していない）: BFO 2020（上位区分）、IAO（ICE と is_about）、
SOSA/SSN（観測・仮想センサ）、PROV-O（来歴）、OWL-Time（時間）、GeoSPARQL（空間）、QUDT（単位）。

### 追加（L2/L3 設備保全 — `ontology/domain/equipment-maintenance.ttl`）

初期ドメインは ADR-0002 により製造・設備保全。

- 物理実体: `RotatingEquipment`, `Pump`, `ProductionLine`, `SparePart`
- 業務記録: `WorkOrder`, `OperationLog`, `MaintenancePlan`
- 観測可能量: `VibrationRms`, `MotorCurrent`, `BearingTemperature`
- フルーエント型（7）: `Running`, `UnderMaintenance`, `Faulted`, `VibrationAnomaly`,
  `Accessible`, `Inspected`, `Present`
- 事象型（9）: `StartCmd`, `StopCmd`, `Fault`, `MaintStart`, `MaintEnd`, `AnomalyOnset`,
  `AnomalyCleared`, `GoodsIssue`, `GoodsReceipt`
- 情報源: `CmmsSource`, `MesSource`, `ErpSource`

型数は 29（設計原則 P6「初期 20〜30 型」の範囲内）。

### 追加（形状 — `ontology/shapes/core-shapes.ttl`）

- `sh:Violation`（構造制約 → 隔離キュー）: `AssetShape`, `IdentityClaimShape`,
  `FluentTypeShape`, `EventTypeShape`, `DiscrepancyShape`, `ProbeShape`
- `sh:Warning`（整合制約 → 不一致候補）: `MutuallyExclusiveFluentShape`,
  `ExclusiveFluentCollisionShape`

### 追加（事象計算 — `ontology/process/equipment-maintenance.lp`）

initiates / terminates、排他制約、条件付き発火（保全中は起動指令で稼働に入らない）、
遅延分布の宣言（設計書 §3.8 のパラメータ）、慣性規則、排他違反の明示的導出、
事象昇格の逆写像（候補事象型の列挙）。
