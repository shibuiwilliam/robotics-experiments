/**
 * 二時制の表示ロジック（M7-04）。**ここが UI の中心である。**
 *
 * 信念ビューの as-of スライダは「いつ時点でそう信じていたか」を動かす。有効時刻
 * （`validAt`、いつの話か）とは別の軸であり、**混ぜると答えが変わる**:
 *
 * - `asOf` を 1 時間前に戻す → 「1 時間前の自分が、今の時刻について何を信じていたか」
 * - `validAt` を 1 時間前に戻す → 「今の自分が、1 時間前について何を信じているか」
 *
 * 後者は訂正を反映済みで、前者は反映していない。UI で軸を 1 本にすると、この違いが
 * 消えて「過去を今の知識で書き換えた」表示になる——二時制を持っている理由が失われる。
 *
 * 区間はすべて右開 `[start, end)`（CLAUDE.md 不変条件 8）。`null` は開いている区間を表す。
 */

import type { Belief, SourceContribution } from "./types";

/** 右開区間に時刻が入るか。`end` が `null` なら開いている（将来にわたって成立）。 */
export function containsInstant(
  start: string,
  end: string | null,
  instant: string,
): boolean {
  if (instant < start) return false;
  if (end === null) return true;
  return instant < end;
}

/**
 * `asOf` 時点で記録されていた信念だけを残す。
 *
 * 取引時間が `[txFrom, txTo)` で、`asOf` がそこに入るものだけ。**訂正前の文が返る**のが
 * 正しい挙動である（訂正は旧文の `txTo` を閉じて新文を足す、不変条件 2）。
 */
export function asOf(beliefs: readonly Belief[], instant: string): Belief[] {
  return beliefs.filter((belief) =>
    containsInstant(belief.txFrom, belief.txTo, instant),
  );
}

/** `validAt` 時点について述べている信念だけを残す。 */
export function validAt(beliefs: readonly Belief[], instant: string): Belief[] {
  return beliefs.filter((belief) =>
    containsInstant(belief.validFrom, belief.validTo, instant),
  );
}

/**
 * 二時制で絞る（両軸を同時に指定する）。
 *
 * 引数を 2 つ取るのが要点である。片方だけの関数を UI から呼べるようにすると、
 * 「どちらの軸で絞ったか」が呼び出し側の記憶になる。
 */
export function bitemporalSlice(
  beliefs: readonly Belief[],
  { transactionTime, validTime }: { transactionTime: string; validTime: string },
): Belief[] {
  return validAt(asOf(beliefs, transactionTime), validTime);
}

/**
 * as-of スライダの目盛り。**実際に信念が変わった時刻だけを刻む。**
 *
 * 等間隔の目盛りにすると、何も起きていない区間を延々と動かすことになり、変化した瞬間を
 * 飛ばす。`txFrom` の集合がそのまま「信念が変わった時刻」である。
 */
export function transactionTicks(beliefs: readonly Belief[]): string[] {
  const ticks = new Set<string>();
  for (const belief of beliefs) {
    ticks.add(belief.txFrom);
    if (belief.txTo !== null) ticks.add(belief.txTo);
  }
  return [...ticks].sort();
}

/**
 * 三源の寄与（設計書 §7.1）。確信度の重みではなく**どの様相の証拠が何件あったか**を返す。
 *
 * 確信度を按分しない。三源の寄与は「結論のうち何割がこの源のせいか」ではなく
 * 「どの源からの証拠を見たか」であり、按分すると証拠が 1 件も無い源にも値が付いて
 * 「計画も見た」という誤った表示になる。
 */
export function sourceContribution(
  beliefs: readonly Belief[],
): SourceContribution {
  let observed = 0;
  let recorded = 0;
  let planned = 0;
  for (const belief of beliefs) {
    if (belief.modality === "OBSERVED") observed += 1;
    else if (belief.modality === "INFERRED") recorded += 1;
    else if (belief.modality === "PLANNED") planned += 1;
  }
  return { observed, recorded, planned };
}

/** 三源のどれにも証拠が無いか（表示を「寄与なし」にする判定）。 */
export function hasNoSource(contribution: SourceContribution): boolean {
  return (
    contribution.observed === 0 &&
    contribution.recorded === 0 &&
    contribution.planned === 0
  );
}

/**
 * 仮説を既定で除外する（不変条件 3、ADR-0008）。
 *
 * 混ぜて表示すると「決まったこと」として読まれる。明示的に切り替えたときだけ出す。
 */
export function excludeHypothetical(beliefs: readonly Belief[]): Belief[] {
  return beliefs.filter((belief) => belief.modality !== "HYPOTHETICAL");
}

/** 確信度の表示（較正済みの値だけが `confidence` に入る、不変条件 7）。 */
export function formatConfidence(confidence: number): string {
  if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
    // 範囲外は丸めて隠さない。較正されていない値が流れてきたことを見せる
    return `範囲外 (${confidence})`;
  }
  return `${(confidence * 100).toFixed(1)}%`;
}

/**
 * 訂正された文か（`txTo` が閉じている）。
 *
 * 「削除された」ではなく「訂正された」と表示するための判定。追記専用なので文は消えない。
 */
export function isSuperseded(belief: Belief): boolean {
  return belief.txTo !== null;
}
