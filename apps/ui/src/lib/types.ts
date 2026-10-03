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

/** 不一致の類型（設計書 §8.1 の 5 類型）。 */
export type DiscrepancyKind =
  | "EXISTENCE"
  | "TIME"
  | "STATE"
  | "IDENTITY"
  | "QUANTITY";

/** 不一致の状態（`sbp.reasoning.reconciliation.discrepancy` の状態機械）。 */
export type DiscrepancyState =
  | "OPEN"
  | "TRIAGED"
  | "ASSIGNED"
  | "RESOLVED"
  | "LEARNED"
  | "SUPPRESSED";

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

/** 不一致 1 件。証拠を並置するために 3 源をそのまま持つ。 */
export interface Discrepancy {
  readonly discrepancyId: string;
  readonly kind: DiscrepancyKind;
  readonly state: DiscrepancyState;
  readonly subject: string;
  readonly severity: number;
  readonly raisedAt: string;
  readonly observedEvidence: readonly Belief[];
  readonly recordedEvidence: readonly Belief[];
  readonly plannedEvidence: readonly Belief[];
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
export type ResolutionKind =
  /** 記録が誤り → 情報源の信頼性を下げる。 */
  | "RECORD_WRONG"
  /** 観測が誤り → プローブに負のラベルを出す。 */
  | "OBSERVATION_WRONG"
  /** どちらも正しく、定義が食い違っている → 概念の候補になる。 */
  | "DEFINITION_MISMATCH"
  /** 系統的な偏り（月末バッチ、シフト境界など） → 抑制規則の候補。 */
  | "SYSTEMATIC_BIAS"
  /** 判断できない → 追加観測を要求する。 */
  | "NEEDS_OBSERVATION";

/** 解決の入力（これが学習信号になる）。 */
export interface Resolution {
  readonly discrepancyId: string;
  readonly kind: ResolutionKind;
  /** 人が書く理由。**必須**——理由の無い解決は学習信号にならない。 */
  readonly reason: string;
  readonly resolvedBy: string;
}
