/**
 * 不一致キューの並べ替えと証拠の並置（設計書 §8.2、§12.2、M7-04）。
 *
 * **並べ替えの基準は深刻度（事業価値 × 不一致の大きさ）であり、起票時刻ではない。**
 * 時刻順にすると、古い軽微な不一致が重大な新規より上に並ぶ。深刻度が同じなら古い方を先に
 * 出す（放置を可視化する）。
 *
 * **証拠は並置するだけで、どちらが正しいかを示さない。** UI が「観測が正しい」と暗示すると、
 * 人の判断が基盤の推定の追認になり、学習信号として機能しなくなる。
 */

import type {
  Belief,
  Discrepancy,
  DiscrepancyState,
  Resolution,
  ResolutionKind,
} from "./types";

/** キューに出す状態（解決済み・抑制済みは出さない）。 */
const ACTIONABLE: ReadonlySet<DiscrepancyState> = new Set([
  "OPEN",
  "TRIAGED",
  "ASSIGNED",
]);

/** 人が手を付けるべき不一致か。 */
export function isActionable(discrepancy: Discrepancy): boolean {
  return ACTIONABLE.has(discrepancy.state);
}

/**
 * キューの順序。深刻度の降順、同じなら起票の古い順。
 *
 * 入力を破壊しない（`toSorted` 相当を自前で書く。React の state をその場で並べ替えると
 * 再描画されない）。
 */
export function queueOrder(
  discrepancies: readonly Discrepancy[],
): Discrepancy[] {
  return [...discrepancies].sort((left, right) => {
    if (left.severity !== right.severity) return right.severity - left.severity;
    return left.raisedAt < right.raisedAt ? -1 : 1;
  });
}

/** キューに出す一覧（対象を絞ってから並べる）。 */
export function visibleQueue(
  discrepancies: readonly Discrepancy[],
): Discrepancy[] {
  return queueOrder(discrepancies.filter(isActionable));
}

/** 証拠を並べた 1 行（観測 / 記録 / 計画のうち、この時刻に何があったか）。 */
export interface EvidenceRow {
  readonly label: "観測" | "記録" | "計画";
  readonly beliefs: readonly Belief[];
  /** 証拠が無い源（「見ていない」ことを空欄で誤魔化さないために明示する）。 */
  readonly isEmpty: boolean;
}

/**
 * 三源を並置する。**証拠が無い源も行として出す。**
 *
 * 空の源を行から落とすと、「記録が無かった」のか「記録を見ていない」のかが区別できない。
 * 不一致の判断で最も重要な情報がそこにあることが多い（存在の不一致はまさに片側が無い）。
 */
export function evidenceRows(discrepancy: Discrepancy): EvidenceRow[] {
  return [
    {
      label: "観測",
      beliefs: discrepancy.observedEvidence,
      isEmpty: discrepancy.observedEvidence.length === 0,
    },
    {
      label: "記録",
      beliefs: discrepancy.recordedEvidence,
      isEmpty: discrepancy.recordedEvidence.length === 0,
    },
    {
      label: "計画",
      beliefs: discrepancy.plannedEvidence,
      isEmpty: discrepancy.plannedEvidence.length === 0,
    },
  ];
}

/** 解決種別の説明（人が選ぶときに「何が起きるか」を見せる）。 */
export const RESOLUTION_EFFECTS: Readonly<Record<ResolutionKind, string>> = {
  RECORD_WRONG: "情報源の信頼性を下げる（この系の記録を今後やや疑う）",
  OBSERVATION_WRONG: "プローブに負のラベルを出す（次の学習で修正される）",
  DEFINITION_MISMATCH: "概念の候補として記録する（新しい区別が必要かを検討する）",
  SYSTEMATIC_BIAS: "抑制規則の候補にする（同種の不一致を起票しなくなる）",
  NEEDS_OBSERVATION: "追加観測を要求する（判断を保留し、観測の予定を立てる）",
};

/** 解決の入力が成立しているか。 */
export interface ResolutionCheck {
  readonly valid: boolean;
  readonly problems: readonly string[];
}

/**
 * 解決の入力を検証する。
 *
 * **理由を必須にするのが要点である。** 種別だけでは学習信号にならない——「記録が誤り」
 * という分類は、何を見てそう判断したかが残らないと、後で信頼性モデルの更新を説明できない。
 * 設計書 §8.3 の「解決入力が学習信号になる」は、理由まで含めての話である。
 */
export function checkResolution(resolution: Resolution): ResolutionCheck {
  const problems: string[] = [];
  if (resolution.reason.trim().length < 10) {
    problems.push(
      "理由を 10 文字以上書くこと。種別だけでは学習信号にならない（なぜそう判断したかが残らない）",
    );
  }
  if (resolution.resolvedBy.trim() === "") {
    problems.push("解決者が必要（来歴に human-review として残る）");
  }
  if (resolution.discrepancyId.trim() === "") {
    problems.push("どの不一致の解決かが必要");
  }
  return { valid: problems.length === 0, problems };
}

/**
 * 滞留の警告。**起票から時間が経った不一致を目立たせる。**
 *
 * 深刻度で並べると、軽微な不一致は永遠に画面の下に残る。それ自体は正しいが、放置が
 * 見えなくなるのは別の問題である（照合が「起票して終わり」になる兆候）。
 */
export function isStale(
  discrepancy: Discrepancy,
  now: string,
  staleAfterHours = 72,
): boolean {
  const raised = Date.parse(discrepancy.raisedAt);
  const current = Date.parse(now);
  if (Number.isNaN(raised) || Number.isNaN(current)) return false;
  return current - raised > staleAfterHours * 3_600_000;
}
