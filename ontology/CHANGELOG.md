# オントロジー変更履歴

semver の判定基準（CLAUDE.md「オントロジー変更の手順」）:

- **MAJOR**: クラス・関係の削除、意味変更。ADR と再接地計画が必須。
- **MINOR**: 加法的な追加。既存プローブ・クエリ・`schemas/` の後方互換を契約テストで証明する。
- **PATCH**: 注釈（label / definition / コメント）のみの変更。

非推奨は `owl:deprecated true` と `dcterms:isReplacedBy` を付け、2 MINOR 版残す。

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
