"""オントロジー語彙（生成物 — 手編集しない）。

``make gen`` が ``ontology/`` の TTL から生成する。不変条件 5 により、コードは IRI を直書きせず
必ずこのモジュールから参照する。

- ``vocab.core.<LocalName>`` — L1 中核（``https://sbp.example/core#``）
- ``vocab.ent.<LocalName>`` — ドメイン・企業層（``https://sbp.example/ent#``）

生成元オントロジー版: 0.4.0
"""

from __future__ import annotations

from typing import Final

ONTOLOGY_VERSION: Final = "0.4.0"
"""この語彙を生成したオントロジーの版（アサーションの ``ontology_version`` に入れる）。"""

CORE_NAMESPACE: Final = "https://sbp.example/core#"
ENT_NAMESPACE: Final = "https://sbp.example/ent#"


class _Core:
    """L1 中核語彙（``https://sbp.example/core#``）。"""

    __slots__ = ()

    ActionEvent: Final = "https://sbp.example/core#ActionEvent"
    """アクション事象（class, L1）

    基盤経由で実行されたアクション。実行時に Expectation を生成し、効果が自動検証される（設計書 §9.2）。
    """

    ActionType: Final = "https://sbp.example/core#ActionType"
    """アクション型（class, L1）

    実行可能なアクションの型。前提・期待効果・リスク級・必要承認・補償を持つ意味契約（設計書 §9.1）。
    """

    AggregateAll: Final = "https://sbp.example/core#AggregateAll"
    """全部分集約（individual, L1）

    全部分で成立して初めて全体で成立する（稼働のような「全系が揃って初めて成り立つ」状態）。
    """

    AggregateAny: Final = "https://sbp.example/core#AggregateAny"
    """部分和集約（individual, L1）

    いずれかの部分で成立すれば全体で成立する（故障・異常のような「どこか一つ壊れれば全体が壊れている」状態）。
    """

    AggregationRule: Final = "https://sbp.example/core#AggregationRule"
    """集約規則（class, L1）

    部分−全体の集約の型。個体は sbp:AggregateAll と sbp:AggregateAny の 2 つのみ。
    """

    Anchor: Final = "https://sbp.example/core#Anchor"
    """アンカー（class, L1）

    同一性束縛の基準点。固定（較正済みカメラ・ゲート・リーダ）／可搬（タグ）／推論（時空間連続性＋業務文脈）の 3 種（設計書 §5.4）。
    """

    Assertion: Final = "https://sbp.example/core#Assertion"
    """アサーション（class, L1）

    二時制・様相・確信度・来歴を備えた信念文。基盤が所有する唯一のもの（不変条件 1）。
    """

    Asset: Final = "https://sbp.example/core#Asset"
    """資産（class, L1）

    設備・機器。業務側では資産台帳の資産 ID で識別される。
    """

    BusinessEvent: Final = "https://sbp.example/core#BusinessEvent"
    """業務事象（class, L1）

    業務システムのトランザクション。業務統合層が Record から変換し、信頼性メタを伴う。
    """

    Carrier: Final = "https://sbp.example/core#Carrier"
    """搬送体（class, L1）

    パレット・コンテナ・車両など、物品を載せて動く物理実体。
    """

    DataSource: Final = "https://sbp.example/core#DataSource"
    """情報源（class, L1）

    業務システムまたはセンサ群。時刻ずれ分布・誤記率・系統誤差からなる信頼性モデルと時計プロファイルを持つ。
    """

    Discrepancy: Final = "https://sbp.example/core#Discrepancy"
    """不一致（class, L1）

    物理と業務の食い違いを表す一級オブジェクト。五類型（存在・状態・時間・同一性・規範）を持ち、解決結果が学習信号になる（設計原則 P5）。
    """

    EventType: Final = "https://sbp.example/core#EventType"
    """事象型（class, L1）

    事象の型（StartCmd, Fault, GoodsReceipt）。initiates / terminates でフルーエントの変化を規定し、JSON Schema の生成元になる。
    """

    Expectation: Final = "https://sbp.example/core#Expectation"
    """期待（class, L1）

    アクション実行時に生成される、期待効果の分布と期限を持つ検証対象。満たされれば介入データ、満たされなければ不一致になる。
    """

    FluentType: Final = "https://sbp.example/core#FluentType"
    """フルーエント型（class, L1）

    時間とともに成立・不成立が変わる状態述語の型（Running, Present, Occupied）。事象計算の holdsAt の対象。
    """

    Forecast: Final = "https://sbp.example/core#Forecast"
    """予測（class, L1）

    地平付きの予測。起点（horizonOrigin）と先行時間（leadTime）を持ち、様相は predicted。
    """

    IdentityClaim: Final = "https://sbp.example/core#IdentityClaim"
    """同一性主張（class, L1）

    物理トラックと業務 ID の束縛主張。証拠・確信度・有効期間を持ち、撤回可能（撤回時は不一致類型 4 を起票する）。
    """

    Location: Final = "https://sbp.example/core#Location"
    """場所（class, L1）

    施設・建屋・ライン・セル・ドック・棚。施設座標系の上で幾何を持つ。
    """

    MaterialItem: Final = "https://sbp.example/core#MaterialItem"
    """物品（class, L1）

    部品・製品・在庫単位。SKU とロットで識別され、数量は QUDT の量で表す。
    """

    Norm: Final = "https://sbp.example/core#Norm"
    """規範（class, L1）

    規程・契約条項・安全規則。Datalog / SHACL 規則にコンパイルされ、違反は様相 normative のアサーションになる。
    """

    OntologyVersion: Final = "https://sbp.example/core#OntologyVersion"
    """オントロジー版（class, L1）

    オントロジーの版。semver で管理し、MAJOR は ADR と再接地計画を要する。
    """

    Person: Final = "https://sbp.example/core#Person"
    """人（class, L1）

    人。役割は Situation 内で付与し、個人特定属性は原則保持しない（設計書 §11.1、不変条件 9）。
    """

    PhysicalEntity: Final = "https://sbp.example/core#PhysicalEntity"
    """物理実体（class, L1）

    物理的に存在し、時間を通じて同一性を保つ実体の総称。観測の対象であり、業務記録が指す対象でもある。
    """

    PhysicalEvent: Final = "https://sbp.example/core#PhysicalEvent"
    """物理事象（class, L1）

    観測または推定された物理世界の事象。接地層の事象昇格が生成し、様相は inferred（不変条件 3）。
    """

    PlanItem: Final = "https://sbp.example/core#PlanItem"
    """計画項目（class, L1）

    計画・スケジュールの項目。様相 planned のアサーションになり、確信度は当該計画種の遵守率事前分布を用いる。
    """

    Probe: Final = "https://sbp.example/core#Probe"
    """プローブ（class, L1）

    潜在状態から概念成立確率または属性値分布を出力する学習器の登録。昇格には AUROC・ECE・整合残差の AND 条件を要する。
    """

    Record: Final = "https://sbp.example/core#Record"
    """業務記録（class, L1）

    伝票・作業指示・検査票など業務システムの記録。誤りうる主張として扱い、信頼性モデルを適用する（設計書 §7.3）。
    """

    Situation: Final = "https://sbp.example/core#Situation"
    """状況（class, L1）

    時空間領域・参加者・関係・解決状態を束ねる単位。イベント群の相関 ID の対象であり、類似状況検索の単位でもある。
    """

    Track: Final = "https://sbp.example/core#Track"
    """トラック（class, L1）

    知覚が生成する時空間軌跡と外観埋め込み。Person のトラックを個人識別に用いてはならない（設計書 §5.4）。
    """

    UnidentifiedPhysicalObject: Final = "https://sbp.example/core#UnidentifiedPhysicalObject"
    """未同定物理個体（class, L1）

    知覚されたが業務 ID に束縛されていない物理個体。「業務が知らない物」を表現するために必要（設計書 §5.4）。
    """

    WorldModelVersion: Final = "https://sbp.example/core#WorldModelVersion"
    """ワールドモデル版（class, L1）

    WM の版。潜在仕様と学習来歴を持ち、プローブとの互換性行列で管理する。ロードは版指定必須。
    """

    Zone: Final = "https://sbp.example/core#Zone"
    """区域（class, L1）

    述語的な領域（危険区域、保管区画）。位置分布との包含確率から locatedIn を導く（設計書 §5.5）。
    """

    aggregatesAs: Final = "https://sbp.example/core#aggregatesAs"
    """集約規則（property, L1）

    部分のフルーエントが全体のフルーエントへどう集約されるか。all（全部分が成立して初めて全体が成立）または any（いずれかの部分で全体が成立）。設計書 §8.1 の関係制約の因子ポテンシャルを決める。
    """

    assetId: Final = "https://sbp.example/core#assetId"
    """資産 ID（property, L1）

    業務側の資産台帳における識別子。安定 IRI の派生元になる。
    """

    boundTo: Final = "https://sbp.example/core#boundTo"
    """束縛される（property, L1）

    トラックが束縛された物理実体。必ず IdentityClaim を経由し、撤回可能であること。
    """

    concerns: Final = "https://sbp.example/core#concerns"
    """対象とする（property, L1）

    不一致が対象とするアサーションまたは物理実体。
    """

    conditionedOn: Final = "https://sbp.example/core#conditionedOn"
    """条件とする（property, L1）

    予測が条件付けられた計画項目またはアサーション。計画変更時の再予測トリガに使う。
    """

    confidence: Final = "https://sbp.example/core#confidence"
    """確信度（property, L1）

    較正済みの確信度。真偽フルーエントでは成立確率。生スコアは証拠側に置く（不変条件 7）。
    """

    containedIn: Final = "https://sbp.example/core#containedIn"
    """含まれる（property, L1）

    場所の包含階層（棚 → 区画 → 建屋）。locatedIn の推移的導出に用いる。
    """

    discrepancyKind: Final = "https://sbp.example/core#discrepancyKind"
    """不一致類型（property, L1）

    五類型の番号（1 存在 / 2 状態 / 3 時間 / 4 同一性 / 5 規範）。設計書 §8.2 の検出規則に対応する。
    """

    evidenceFor: Final = "https://sbp.example/core#evidenceFor"
    """根拠である（property, L1）

    アサーションの根拠。説明サービスが 3 ホップ以内で到達できるようにする（非機能要求の監査性）。
    """

    expects: Final = "https://sbp.example/core#expects"
    """期待する（property, L1）

    アクション実行が生成する期待。期限までに検証される。
    """

    geometry: Final = "https://sbp.example/core#geometry"
    """幾何（property, L1）

    施設座標系上のポリゴンまたはボリューム。GeoSPARQL の幾何を用いる。
    """

    groundedBy: Final = "https://sbp.example/core#groundedBy"
    """接地される（property, L1）

    概念または属性を潜在状態に接地するプローブ。接地の実装を指す。
    """

    hasParticipant: Final = "https://sbp.example/core#hasParticipant"
    """参加者を持つ（property, L1）

    Situation または事象に参加する物理実体。役割付きで修飾できる。
    """

    horizonOrigin: Final = "https://sbp.example/core#horizonOrigin"
    """予測起点（property, L1）

    予測を行った時点。同一対象・同一有効時間に異なる起点の予測が共存する（設計書 §6.4）。
    """

    inSituation: Final = "https://sbp.example/core#inSituation"
    """所属状況（property, L1）

    この文が属する Situation。類似状況検索と説明の文脈に使う。
    """

    initiates: Final = "https://sbp.example/core#initiates"
    """開始する（property, L1）

    事象型がフルーエント型を成立させる事象計算の規則。条件と遅延分布は ontology/process/*.lp に書く。
    """

    layer: Final = "https://sbp.example/core#layer"
    """層（property, L1）

    概念が属するオントロジー層（L1 中核 / L2 ドメイン標準 / L3 企業 / L4 アプリ）。変更頻度とオーナーを規定する。
    """

    leadTime: Final = "https://sbp.example/core#leadTime"
    """先行時間（property, L1）

    予測起点から対象時刻までの長さ。地平別の精度追跡に使う。
    """

    locatedIn: Final = "https://sbp.example/core#locatedIn"
    """位置する（property, L1）

    物理実体の空間位置。位置分布と Zone 幾何の包含確率から導かれる確率的述語（設計書 §5.5）。
    """

    modality: Final = "https://sbp.example/core#modality"
    """様相（property, L1）

    事実の身分。observed / inferred / predicted / planned / normative / hypothetical の 6 値のみ（不変条件 3）。
    """

    mutuallyExclusiveWith: Final = "https://sbp.example/core#mutuallyExclusiveWith"
    """排他である（property, L1）

    同一有効時間に共に成立できないフルーエント型の対。衝突は拒否ではなく不一致候補（類型 2）の証拠になる。
    """

    ontologyVersion: Final = "https://sbp.example/core#ontologyVersion"
    """オントロジー版（property, L1）

    この文を作ったときのオントロジー版。版を無視した比較を防ぐためクエリは必ずこれを返す。
    """

    owner: Final = "https://sbp.example/core#owner"
    """オーナー（property, L1）

    概念の定義・採否・非推奨に責任を持つ主体。L1 は基盤オントロジスト、L2/L3 は概念オーナー（設計書 §11.3）。
    """

    partOf: Final = "https://sbp.example/core#partOf"
    """部分である（property, L1）

    物理実体の部分−全体関係。BFO の part of の下位プロパティとして宣言し、再定義しない（設計原則 P10）。信念更新では「強い関係」として実体間結合の因子になる（設計書 §8.1）。
    """

    probeStatus: Final = "https://sbp.example/core#probeStatus"
    """プローブ状態（property, L1）

    プローブの身分（candidate / active / deprecated）。active 化は昇格ゲートの AND 条件を要する。
    """

    provenance: Final = "https://sbp.example/core#provenance"
    """来歴（property, L1）

    生成活動・エージェント・モデル版・証拠集合をまとめた PROV バンドルの参照。
    """

    quantity: Final = "https://sbp.example/core#quantity"
    """数量（property, L1）

    単位付きの数量。QUDT の QuantityValue を用い、裸の数値を使わない。
    """

    scenario: Final = "https://sbp.example/core#scenario"
    """シナリオ（property, L1）

    What-if シナリオの識別子。hypothetical の文はこれを必ず持ち、主信念から隔離される（不変条件 3）。
    """

    sku: Final = "https://sbp.example/core#sku"
    """SKU（property, L1）

    在庫管理単位の識別子。
    """

    sourceReliability: Final = "https://sbp.example/core#sourceReliability"
    """情報源信頼性（property, L1）

    適用した信頼性モデルの参照。記録由来の確信度の根拠になる。
    """

    subjectType: Final = "https://sbp.example/core#subjectType"
    """主体型（property, L1）

    フルーエント型が述語として適用される実体のクラス。
    """

    targets: Final = "https://sbp.example/core#targets"
    """対象とする（property, L1）

    アクションまたは業務記録が対象とする物理実体。
    """

    terminates: Final = "https://sbp.example/core#terminates"
    """終了する（property, L1）

    事象型がフルーエント型を不成立にする事象計算の規則。
    """

    txFrom: Final = "https://sbp.example/core#txFrom"
    """記録開始（property, L1）

    基盤がそう信じ始めた時刻。as-of クエリはこの値で絞る。
    """

    txTo: Final = "https://sbp.example/core#txTo"
    """記録終了（property, L1）

    基盤がそう信じるのをやめた時刻。訂正はこのトリプルの追加で表し、文を削除しない（不変条件 2）。
    """

    validFrom: Final = "https://sbp.example/core#validFrom"
    """有効開始（property, L1）

    現実にそうであった期間の始端（この時刻を含む）。右開区間 [valid_from, valid_to) の左端。
    """

    validInterval: Final = "https://sbp.example/core#validInterval"
    """有効区間（property, L1）

    主張が有効な時間区間。右開 [start, end) として扱う（不変条件 8）。
    """

    validTo: Final = "https://sbp.example/core#validTo"
    """有効終了（property, L1）

    現実にそうであった期間の終端（この時刻を含まない）。継続中はこのトリプルを置かない。
    """


class _Ent:
    """ドメイン・企業層の語彙（``https://sbp.example/ent#``）。"""

    __slots__ = ()

    Accessible: Final = "https://sbp.example/ent#Accessible"
    """接近可能（fluent_type, L2）

    人が安全に接近できる状態。アクションの物理前提（設計書 §9.1）に用いる。
    """

    AnomalyCleared: Final = "https://sbp.example/ent#AnomalyCleared"
    """異常解消（event_type, L2）

    振動異常の解消。VibrationAnomaly を終了する。
    """

    AnomalyOnset: Final = "https://sbp.example/ent#AnomalyOnset"
    """異常発現（event_type, L2）

    振動異常の発現。VibrationAnomaly を開始する。
    """

    BearingTemperature: Final = "https://sbp.example/ent#BearingTemperature"
    """軸受温度（observable_property, L2）

    軸受の温度。劣化の遅い時間スケール（L-slow 潜在）に対応する。
    """

    CmmsSource: Final = "https://sbp.example/ent#CmmsSource"
    """CMMS（individual, L3）

    保全管理システム。作業指示の完了記録は実作業より遅れて入力されることがある。
    """

    ErpSource: Final = "https://sbp.example/ent#ErpSource"
    """ERP（individual, L3）

    基幹システム。予備品在庫を管理する。入庫は同日内に計上されるが月末一括計上の系統誤差を持つ。
    """

    Fault: Final = "https://sbp.example/ent#Fault"
    """故障（event_type, L2）

    設備の故障。Running を終了し Faulted と Accessible を開始する（遅延 Exponential(1/5s)）。停止指令との区別は振動と業務文脈の尤度で行う。
    """

    Faulted: Final = "https://sbp.example/ent#Faulted"
    """故障中（fluent_type, L2）

    設備が故障して停止している状態。StopCmd による計画停止と区別する。
    """

    GoodsIssue: Final = "https://sbp.example/ent#GoodsIssue"
    """出庫（event_type, L2）

    予備品の出庫。Present を終了する。在庫記録と物理観測の食い違いの起点になる。
    """

    GoodsReceipt: Final = "https://sbp.example/ent#GoodsReceipt"
    """入庫（event_type, L2）

    予備品の入庫。Present を開始する。
    """

    Inspected: Final = "https://sbp.example/ent#Inspected"
    """点検済み（fluent_type, L2）

    点検が完了した状態。緊急点検アクションの期待効果（Expectation）の対象。
    """

    MaintEnd: Final = "https://sbp.example/ent#MaintEnd"
    """保全終了（event_type, L2）

    保全作業の終了。UnderMaintenance を終了し、Faulted も解消して Inspected を開始する。
    """

    MaintStart: Final = "https://sbp.example/ent#MaintStart"
    """保全開始（event_type, L2）

    保全作業の開始。UnderMaintenance と Accessible を開始する。
    """

    MaintenancePlan: Final = "https://sbp.example/ent#MaintenancePlan"
    """保全計画（class, L3）

    計画保全の予定項目。様相 planned のアサーションになり、遵守率の事前分布を伴う。
    """

    MesSource: Final = "https://sbp.example/ent#MesSource"
    """MES（individual, L3）

    製造実行システム。稼働実績の時刻ずれは ±5 分程度（設計書 §3.6）。
    """

    MotorCurrent: Final = "https://sbp.example/ent#MotorCurrent"
    """電動機電流（observable_property, L2）

    電動機の電流。稼働・停止の主要な観測量。
    """

    OperationLog: Final = "https://sbp.example/ent#OperationLog"
    """稼働実績（class, L3）

    MES の稼働実績。時刻ずれを持つ主張として扱う（既定の許容プロファイルは ±5 分）。
    """

    Present: Final = "https://sbp.example/ent#Present"
    """存在する（fluent_type, L2）

    物品が指定の場所に存在する状態。存在不一致（類型 1、幽霊在庫）の判定対象。
    """

    ProductionLine: Final = "https://sbp.example/ent#ProductionLine"
    """生産ライン（class, L3）

    設備が設置される生産ラインの区画。停止コストの算定単位であり深刻度モデルに影響する。
    """

    Pump: Final = "https://sbp.example/ent#Pump"
    """ポンプ（class, L3）

    流体を移送する回転機。設計書 §14 のワークドエグザンプルの対象（P-101）。
    """

    RotatingEquipment: Final = "https://sbp.example/ent#RotatingEquipment"
    """回転機（class, L2）

    回転部を持つ設備。振動・電流から状態を推定でき、Koopman 作用素モデルの対象になる（設計書 §4.2）。
    """

    Running: Final = "https://sbp.example/ent#Running"
    """稼働中（fluent_type, L2）

    設備が稼働している状態。UnderMaintenance と排他（設計書 §3.8）。
    """

    SparePart: Final = "https://sbp.example/ent#SparePart"
    """予備品（class, L3）

    保全に用いる部品。在庫記録とカメラ観測の食い違い（幽霊在庫、不一致類型 1）の対象になる。
    """

    StartCmd: Final = "https://sbp.example/ent#StartCmd"
    """起動指令（event_type, L2）

    設備の起動指令。UnderMaintenance でないときに限り Running を開始する（遅延 LogNormal(ln 30s, 0.5)）。稼働中は人が近づけないため Accessible を終了する。
    """

    StopCmd: Final = "https://sbp.example/ent#StopCmd"
    """停止指令（event_type, L2）

    設備の計画停止指令。Running を終了し、停止により人が近づけるようになるため Accessible を開始する（遅延 LogNormal(ln 10s, 0.4)）。
    """

    UnderMaintenance: Final = "https://sbp.example/ent#UnderMaintenance"
    """保全中（fluent_type, L2）

    設備が保全作業中の状態。作業指示の完了記録が弱ラベルになる。
    """

    VibrationAnomaly: Final = "https://sbp.example/ent#VibrationAnomaly"
    """振動異常（fluent_type, L2）

    振動が正常域を外れている状態。設計書 §14 で停止原因の切り分けに用いられる補助フルーエント。
    """

    VibrationRms: Final = "https://sbp.example/ent#VibrationRms"
    """振動実効値（observable_property, L2）

    振動加速度の実効値。異常の早期指標であり、停止原因（StopCmd か Fault か）の切り分けに使う。
    """

    WorkOrder: Final = "https://sbp.example/ent#WorkOrder"
    """作業指示（class, L3）

    CMMS の作業指示。完了記録は弱ラベル（UnderMaintenance の時間バッグ）の生成元になる（設計書 §5.2）。
    """


core: Final = _Core()
"""L1 中核語彙。"""

ent: Final = _Ent()
"""ドメイン・企業層の語彙。"""

ALL_IRIS: Final[frozenset[str]] = frozenset(
    (
        "https://sbp.example/core#ActionEvent",
        "https://sbp.example/core#ActionType",
        "https://sbp.example/core#AggregateAll",
        "https://sbp.example/core#AggregateAny",
        "https://sbp.example/core#AggregationRule",
        "https://sbp.example/core#Anchor",
        "https://sbp.example/core#Assertion",
        "https://sbp.example/core#Asset",
        "https://sbp.example/core#BusinessEvent",
        "https://sbp.example/core#Carrier",
        "https://sbp.example/core#DataSource",
        "https://sbp.example/core#Discrepancy",
        "https://sbp.example/core#EventType",
        "https://sbp.example/core#Expectation",
        "https://sbp.example/core#FluentType",
        "https://sbp.example/core#Forecast",
        "https://sbp.example/core#IdentityClaim",
        "https://sbp.example/core#Location",
        "https://sbp.example/core#MaterialItem",
        "https://sbp.example/core#Norm",
        "https://sbp.example/core#OntologyVersion",
        "https://sbp.example/core#Person",
        "https://sbp.example/core#PhysicalEntity",
        "https://sbp.example/core#PhysicalEvent",
        "https://sbp.example/core#PlanItem",
        "https://sbp.example/core#Probe",
        "https://sbp.example/core#Record",
        "https://sbp.example/core#Situation",
        "https://sbp.example/core#Track",
        "https://sbp.example/core#UnidentifiedPhysicalObject",
        "https://sbp.example/core#WorldModelVersion",
        "https://sbp.example/core#Zone",
        "https://sbp.example/core#aggregatesAs",
        "https://sbp.example/core#assetId",
        "https://sbp.example/core#boundTo",
        "https://sbp.example/core#concerns",
        "https://sbp.example/core#conditionedOn",
        "https://sbp.example/core#confidence",
        "https://sbp.example/core#containedIn",
        "https://sbp.example/core#discrepancyKind",
        "https://sbp.example/core#evidenceFor",
        "https://sbp.example/core#expects",
        "https://sbp.example/core#geometry",
        "https://sbp.example/core#groundedBy",
        "https://sbp.example/core#hasParticipant",
        "https://sbp.example/core#horizonOrigin",
        "https://sbp.example/core#inSituation",
        "https://sbp.example/core#initiates",
        "https://sbp.example/core#layer",
        "https://sbp.example/core#leadTime",
        "https://sbp.example/core#locatedIn",
        "https://sbp.example/core#modality",
        "https://sbp.example/core#mutuallyExclusiveWith",
        "https://sbp.example/core#ontologyVersion",
        "https://sbp.example/core#owner",
        "https://sbp.example/core#partOf",
        "https://sbp.example/core#probeStatus",
        "https://sbp.example/core#provenance",
        "https://sbp.example/core#quantity",
        "https://sbp.example/core#scenario",
        "https://sbp.example/core#sku",
        "https://sbp.example/core#sourceReliability",
        "https://sbp.example/core#subjectType",
        "https://sbp.example/core#targets",
        "https://sbp.example/core#terminates",
        "https://sbp.example/core#txFrom",
        "https://sbp.example/core#txTo",
        "https://sbp.example/core#validFrom",
        "https://sbp.example/core#validInterval",
        "https://sbp.example/core#validTo",
        "https://sbp.example/ent#Accessible",
        "https://sbp.example/ent#AnomalyCleared",
        "https://sbp.example/ent#AnomalyOnset",
        "https://sbp.example/ent#BearingTemperature",
        "https://sbp.example/ent#CmmsSource",
        "https://sbp.example/ent#ErpSource",
        "https://sbp.example/ent#Fault",
        "https://sbp.example/ent#Faulted",
        "https://sbp.example/ent#GoodsIssue",
        "https://sbp.example/ent#GoodsReceipt",
        "https://sbp.example/ent#Inspected",
        "https://sbp.example/ent#MaintEnd",
        "https://sbp.example/ent#MaintStart",
        "https://sbp.example/ent#MaintenancePlan",
        "https://sbp.example/ent#MesSource",
        "https://sbp.example/ent#MotorCurrent",
        "https://sbp.example/ent#OperationLog",
        "https://sbp.example/ent#Present",
        "https://sbp.example/ent#ProductionLine",
        "https://sbp.example/ent#Pump",
        "https://sbp.example/ent#RotatingEquipment",
        "https://sbp.example/ent#Running",
        "https://sbp.example/ent#SparePart",
        "https://sbp.example/ent#StartCmd",
        "https://sbp.example/ent#StopCmd",
        "https://sbp.example/ent#UnderMaintenance",
        "https://sbp.example/ent#VibrationAnomaly",
        "https://sbp.example/ent#VibrationRms",
        "https://sbp.example/ent#WorkOrder",
    )
)
"""この語彙が定義する全 IRI。``validate_iri`` の既知語彙チェックに使う。"""

FLUENT_TYPES: Final[frozenset[str]] = frozenset(
    (
        "https://sbp.example/ent#Accessible",
        "https://sbp.example/ent#Faulted",
        "https://sbp.example/ent#Inspected",
        "https://sbp.example/ent#Present",
        "https://sbp.example/ent#Running",
        "https://sbp.example/ent#UnderMaintenance",
        "https://sbp.example/ent#VibrationAnomaly",
    )
)
"""フルーエント型の IRI（事象計算の holdsAt の対象）。"""

EVENT_TYPES: Final[frozenset[str]] = frozenset(
    (
        "https://sbp.example/ent#AnomalyCleared",
        "https://sbp.example/ent#AnomalyOnset",
        "https://sbp.example/ent#Fault",
        "https://sbp.example/ent#GoodsIssue",
        "https://sbp.example/ent#GoodsReceipt",
        "https://sbp.example/ent#MaintEnd",
        "https://sbp.example/ent#MaintStart",
        "https://sbp.example/ent#StartCmd",
        "https://sbp.example/ent#StopCmd",
    )
)
"""事象型の IRI（JSON Schema の生成元）。"""
