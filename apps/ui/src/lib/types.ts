/**
 * API が返す形（設計書 §10.1 の GraphQL スキーマに対応、M7-04）。
 *
 * GraphQL のコード生成を入れていない（ADR-0008）。クエリが 4 本で mutation が無いので、
 * 手で書いたほうが依存を増やさずに済む。**スキーマとずれたら実行時に落ちる**ので、
 * `test/` で API のスキーマ文字列と突き合わせる代わりに、受信時の検証関数を通す。
 */

/** 様相（`sbp.core.modality.Modality` と一致させる。ずれると API の指定が別の意味になる）。 */
export type Modality =
  | "OBSERVED"
  | "INFERRED"
  | "PREDICTED"
  | "PLANNED"
  | "NORMATIVE"
  | "HYPOTHETICAL";

/**
 * 不一致の類型（設計書 §8.2 の五類型）。
 *
 * **API は番号で返す**（`sbp.reasoning.reconciliation.DiscrepancyKind` は `IntEnum` で、
 * 番号はオントロジーの `sbp:discrepancyKind` と一致している）。文字列の別名を UI で持つと
 * **ずれる**——実際にずれていた（M8-16 の前は `QUANTITY` という存在しない類型を持ち、
 * 実在する `NORM`（規範）が無かった。所見 83）。
 */
export type DiscrepancyKind = 1 | 2 | 3 | 4 | 5;

/** 類型番号 → 表示名（番号が正典。ここは表示のためだけにある）。 */
export const DISCREPANCY_KIND_LABEL: Readonly<Record<DiscrepancyKind, string>> =
  {
    1: "存在",
    2: "状態",
    3: "時刻",
    4: "同一性",
    5: "規範",
  };

/**
 * 不一致の状態（`sbp.reasoning.reconciliation.DiscrepancyStatus` の値）。
 *
 * **API が返す文字列そのもの**（`Open` / `Triaged` / …）。大文字の別名を持つと変換が
 * 必要になり、変換を忘れた経路で一致判定が静かに外れる（所見 83）。
 */
export type DiscrepancyState =
  | "Open"
  | "Triaged"
  | "Assigned"
  | "Resolved"
  | "Learned"
  | "Suppressed";

/** 期待の状態（`sbp.reasoning.expectation`）。 */
export type ExpectationStatus =
  | "CREATED"
  | "ACTIVE"
  | "FULFILLED"
  | "VIOLATED"
  | "UNDETERMINED"
  | "LEARNED";

/** 信念 1 件。 */
export interface Belief {
  readonly assertionId: string;
  readonly subject: string;
  readonly predicate: string;
  readonly object: string;
  readonly validFrom: string;
  readonly validTo: string | null;
  readonly txFrom: string;
  readonly txTo: string | null;
  readonly modality: Modality;
  readonly confidence: number;
  readonly provenance: string;
  readonly ontologyVersion: string;
}

/** 三源の寄与（設計書 §7.1。観測・記録・計画）。 */
export interface SourceContribution {
  readonly observed: number;
  readonly recorded: number;
  readonly planned: number;
}

/**
 * 不一致 1 件（GraphQL の `discrepancies` が返す形。ADR-0013）。
 *
 * **証拠は IRI で返る。** 以前の型は `observedEvidence: Belief[]` のように三源の信念が
 * 埋まっている前提だったが、**その経路は存在しなかった**（イベントに全体が入らないので
 * ストリームからも取れない。所見 62 / 83）。いまは不一致が根拠にしたアサーションと証拠の
 * IRI が返るので、信念の本体を引くのは別のクエリになる（三源の並置は M8-17）。
 *
 * **主体は複数ありうる**（類型 4 の競合クレームなど）。起票イベントは 1 つしか主体を選べない
 * が（パーティションキーの制約、ADR-0010）、本体は全部持つ。
 */
export interface Discrepancy {
  readonly discrepancyId: string;
  readonly kind: DiscrepancyKind;
  readonly status: DiscrepancyState;
  readonly subjects: readonly string[];
  readonly severity: number;
  readonly detectedAt: string;
  readonly detector: string;
  /** 根拠になった信念アサーションの IRI。 */
  readonly assertions: readonly string[];
  /** 根拠になった証拠（イベント・記録）の IRI。 */
  readonly evidence: readonly string[];
  readonly assigneeRole: string | null;
  readonly ontologyVersion: string;
}

/** 期待 1 件。 */
export interface Expectation {
  readonly expectationId: string;
  readonly status: ExpectationStatus;
  readonly actionType: string;
  readonly subject: string;
  readonly deadline: string;
  readonly symbolicVerdict: boolean | null;
  readonly rolloutVerdict: boolean | null;
}

/** 概念提案 1 件（`sbp.reasoning.discovery.ConceptProposal`）。 */
export interface ConceptProposal {
  readonly proposalId: string;
  readonly label: string;
  readonly rationale: string;
  readonly supportCount: number;
  readonly coverage: number;
  /** 却下した代替案。**空の提案は採否を判断できない**（設計書 §11.2）。 */
  readonly rejectedAlternatives: readonly string[];
}

/**
 * 解決の種別（設計書 §8.3）。
 *
 * 「正しい値」を入力させない。不一致の解決は**どの前提が崩れていたか**の分類であり、
 * 真偽の宣言ではない（ADR-0008）。選んだ種別がそのまま学習信号の種類を決める。
 */
/**
 * 解決結果（`sbp.reasoning.reconciliation.ResolutionOutcome` の値そのもの）。
 *
 * **API の語彙に合わせる。** 以前は UI 独自の 5 値（`RECORD_WRONG` /
 * `SYSTEMATIC_BIAS` など）を持っており、**API はそれを受け付けなかった**（`extra="forbid"`
 * で弾かれる。所見 83）。設計書 §8.2 の表は 4 値で、抑制（`SYSTEMATIC_BIAS` 相当）は
 * 解決結果ではなく**別の遷移**（`Open → Suppressed`）である。
 *
 * 名前の向きにも注意する: `physical_correct` は「物理が正しい」= 記録が誤っていた、である。
 */
export type ResolutionOutcome =
  /** 物理が正しい（記録が誤っていた）→ 記録訂正の提案 + 情報源の信頼性更新。 */
  | "physical_correct"
  /** 記録が正しい（観測・推定が誤っていた）→ プローブの負例バッグ。 */
  | "record_correct"
  /** どちらも誤り → オントロジーの欠落候補（概念候補）。 */
  | "both_wrong"
  /** 判断できない → 追加観測の要求。 */
  | "undetermined";

/**
 * 解決の入力（これが学習信号になる。`/commands/resolve_discrepancy` の引数）。
 *
 * **`decisionId` は冪等の鍵である。** UI が生成し、再送（ネットワークの再試行、人の二度押し）
 * では同じ値を送る——違う値を送ると、同じ判断が 2 回適用されて書き戻しが 2 回走る
 * （ADR-0012）。
 */
export interface Resolution {
  readonly decisionId: string;
  readonly discrepancyId: string;
  readonly outcome: ResolutionOutcome;
  /** 人が書く理由。**必須**——理由の無い解決は学習信号にならない（API も空を弾く）。 */
  readonly note: string;
  readonly resolvedBy: string;
}
