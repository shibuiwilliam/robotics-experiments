/**
 * 証拠の三源並置（設計書 §8.2、§10.3、M8-17、ADR-0020）。
 *
 * **並置の単位は寄与である。** 三源は様相では分かれない——業務記録由来の信念も推定を経て
 * `inferred` になる（所見 84）。信念の各点が「観測・記録・計画のどれがどれだけ事後を動かしたか」
 * を持ち、その時系列を源ごとに並べる。
 *
 * **「記録されていない」と「寄与なし」を区別する。** 寄与が `null` の点（この変更より前の信念、
 * 実体間の結合が書いた信念）を 0 に丸めると、画面は「観測も記録も計画も効いていない」と
 * 誤読させる（所見 32）。
 *
 * **どちらが正しいかを示さない。** 並べるだけで、判断は人がする（ADR-0008）。
 */

import type {
  BeliefEvidence,
  Discrepancy,
  EvidenceContribution,
  EvidencePoint,
  EvidenceSource,
} from "./types";

/** 源の並び（設計書 §8.1 の `contrib: {observed, record, plan}` の順）。 */
export const EVIDENCE_SOURCES: readonly EvidenceSource[] = ["observed", "record", "plan"];

/** 源の表示名。 */
export const SOURCE_LABEL: Readonly<Record<EvidenceSource, string>> = {
  observed: "観測（プローブ）",
  record: "業務記録",
  plan: "計画",
};

/** 三源の割合（合計が 1。全部 0 のときだけ全部 0）。 */
export interface Shares {
  readonly observed: number;
  readonly record: number;
  readonly plan: number;
}

/**
 * 生の大きさ（対数尤度比の絶対値）を割合にする。**全部 0 のときに NaN を作らない。**
 */
export function shares(contribution: EvidenceContribution): Shares {
  const total = contribution.observed + contribution.record + contribution.plan;
  if (total <= 0) return { observed: 0, record: 0, plan: 0 };
  return {
    observed: contribution.observed / total,
    record: contribution.record / total,
    plan: contribution.plan / total,
  };
}

/** 源ごとの要約の状態。 */
export type ColumnState =
  /** 区間に信念が無い（並べる相手が無い）。 */
  | "no-beliefs"
  /** 信念はあるが、寄与が 1 点も記録されていない（寄与なし**ではない**）。 */
  | "unrecorded"
  /** 寄与が記録されていて、この源は 0 だった。 */
  | "none"
  /** 寄与が記録されていて、この源が動かした。 */
  | "contributed";

/** 3 列の 1 列（源 1 つの要約）。 */
export interface SourceColumn {
  readonly source: EvidenceSource;
  readonly label: string;
  readonly state: ColumnState;
  /** 人が読む説明（空欄にしない）。 */
  readonly note: string;
  /** 寄与が記録された点での平均の割合（0〜1）。 */
  readonly meanShare: number;
  /** この源が最も事後を動かした点の数。 */
  readonly dominantCount: number;
  /** 寄与が記録されていない点の数（記録のある点と混ざるとき）。 */
  readonly unrecordedPoints: number;
  /** 区間と重なる計画の数（計画の列だけ意味を持つ。他は 0）。 */
  readonly plannedSpans: number;
}

/**
 * 三源を 3 列に要約する。**どの列も空欄にしない**（状態と説明を必ず持つ）。
 */
export function sourceColumns(evidence: BeliefEvidence): readonly SourceColumn[] {
  const recorded = evidence.points.filter(
    (point): point is EvidencePoint & { contribution: EvidenceContribution } =>
      point.contribution !== null,
  );
  const unrecordedPoints = evidence.points.length - recorded.length;
  return EVIDENCE_SOURCES.map((source) => {
    const plannedSpans = source === "plan" ? evidence.planned.length : 0;
    const dominantCount = recorded.filter((p) => p.contribution.dominant === source).length;
    const base = {
      source,
      label: SOURCE_LABEL[source],
      dominantCount,
      unrecordedPoints,
      plannedSpans,
    };
    if (evidence.points.length === 0) {
      return {
        ...base,
        state: "no-beliefs" as const,
        note: "この区間に信念が無い（寄与を並べる相手が無い）",
        meanShare: 0,
      };
    }
    if (recorded.length === 0) {
      return {
        ...base,
        state: "unrecorded" as const,
        note: "寄与は記録されていない（寄与なしではない）",
        meanShare: 0,
      };
    }
    const meanShare =
      recorded.reduce((sum, point) => sum + shares(point.contribution)[source], 0) /
      recorded.length;
    const total = recorded.reduce((sum, point) => sum + point.contribution[source], 0);
    if (total <= 0) {
      return { ...base, state: "none" as const, note: "寄与なし", meanShare: 0 };
    }
    return {
      ...base,
      state: "contributed" as const,
      note: `平均 ${(meanShare * 100).toFixed(0)}%・最も動かした点 ${dominantCount} 件`,
      meanShare,
    };
  });
}

/** 時系列の表の 1 行。 */
export interface PointRow {
  readonly assertionId: string;
  readonly at: string;
  readonly probability: number;
  /** 出どころ（前向きフィルタ = 更新 / 実体間の結合）。 */
  readonly origin: "更新" | "結合";
  /** 寄与の有無の表示（`null` は「記録されていない」。0 ではない）。 */
  readonly contribution: "記録あり" | "記録されていない";
  readonly shares: Shares | null;
  readonly dominant: EvidenceSource | null;
  /** 監査隔離で除外した記録の数。 */
  readonly excludedRecords: number;
}

/** 信念の点を表の行にする。 */
export function pointRows(points: readonly EvidencePoint[]): readonly PointRow[] {
  return points.map((point) => ({
    assertionId: point.assertionId,
    at: point.at,
    probability: point.probability,
    origin: point.origin === "coupling" ? "結合" : "更新",
    contribution: point.contribution === null ? "記録されていない" : "記録あり",
    shares: point.contribution === null ? null : shares(point.contribution),
    dominant: point.contribution?.dominant ?? null,
    excludedRecords: point.contribution?.excludedRecords ?? 0,
  }));
}

/** 並置の鍵 1 つ（実体 × フルーエント × 区間）。 */
export interface EvidenceTarget {
  readonly entity: string;
  readonly fluent: string;
  readonly start: string;
  readonly end: string;
}

export type EvidenceTargets =
  | { readonly kind: "targets"; readonly targets: readonly EvidenceTarget[] }
  | { readonly kind: "unavailable"; readonly reason: string };

/** フルーエント型らしい IRI（`…#Running`。イベント型の `…/ent:Running` や ID は除く）。 */
const FLUENT_IRI = /^https?:\/\/[^\s#]+#[A-Za-z0-9_]+$/;

/**
 * 不一致から並置の鍵を作る。**区間やフルーエントを推測しない**——足りなければ理由を返す。
 *
 * 照合規則は `assertions` にフルーエント型（類型 1・2）やイベント型（類型 3）を入れる。
 * アサーション ID ではない（所見 125）ので、引けるのは主体 × フルーエント × 区間である。
 * 終端が無い（継続中）なら `now` までを区間にする。
 */
export function evidenceTargets(discrepancy: Discrepancy, now: string): EvidenceTargets {
  if (discrepancy.intervalStart === null) {
    return { kind: "unavailable", reason: "不一致の区間が記録されていない（区間を推測しない）" };
  }
  if (discrepancy.subjects.length === 0) {
    return { kind: "unavailable", reason: "主体が無い" };
  }
  const fluents = discrepancy.assertions.filter((iri) => FLUENT_IRI.test(iri));
  if (fluents.length === 0) {
    return {
      kind: "unavailable",
      reason: "根拠にフルーエント型が無い（イベント型や ID では信念を引けない）",
    };
  }
  const start = discrepancy.intervalStart;
  const end = discrepancy.intervalEnd ?? now;
  return {
    kind: "targets",
    targets: discrepancy.subjects.flatMap((entity) =>
      fluents.map((fluent) => ({ entity, fluent, start, end })),
    ),
  };
}
