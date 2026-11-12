/**
 * 期待ボードと概念 curation の表示ロジック（設計書 §9.3、§11.2、M7-04）。
 *
 * **期待ボードで見せたいのは「誰のせいか」である。** 期待が破れたとき、記号側（T から
 * 導いた帰結）と WM 側（rollout から得た予測）のどちらが外れたかで原因が違う:
 *
 * | 記号 | WM | 読み |
 * |---|---|---|
 * | 外れ | 当たり | 規則・オントロジーが現実とずれている |
 * | 当たり | 外れ | WM の予測が劣化している（再学習・再接地の対象） |
 * | 外れ | 外れ | アクションが実際に失敗した |
 * | 当たり | 当たり | 成立（Fulfilled） |
 *
 * この表を UI に出さずに「違反 3 件」とだけ出すと、**毎回人が証拠を開いて同じ判断をする**。
 */

import type { ConceptProposal, Expectation, ExpectationStatus } from "./types";

/** 期待が破れたときの原因の読み。 */
export type BlameTarget =
  | "symbolic"
  | "worldmodel"
  | "action"
  | "none"
  | "unknown";

/**
 * 期待の違反を誰に帰属させるか。
 *
 * **宣言していない面を「当たり」として数えない。** 片方の面しか効果を宣言していない期待で
 * 両面の判定を要求すると、観測していない面が常に `null` になって全てが `unknown` になる。
 */
export function blameFor(expectation: Expectation): BlameTarget {
  const { symbolicVerdict, rolloutVerdict } = expectation;
  if (symbolicVerdict === null && rolloutVerdict === null) return "unknown";
  if (symbolicVerdict === false && rolloutVerdict === false) return "action";
  if (symbolicVerdict === false) return "symbolic";
  if (rolloutVerdict === false) return "worldmodel";
  return "none";
}

/** 帰属の説明（人が次に何をするかが分かる形で書く）。 */
export const BLAME_EXPLANATION: Readonly<Record<BlameTarget, string>> = {
  symbolic:
    "規則・オントロジーが現実とずれている。事象計算規則か ActionType の効果宣言を見直す",
  worldmodel:
    "WM の予測が外れた。潜在シフトと整合残差を確認し、再学習・再接地の対象にする",
  action: "アクションが実際に失敗した。補償を実行し、物理側の前提を確認する",
  none: "両面が成立した（期待どおり）",
  unknown: "判定できる面が無い。効果の宣言か観測の経路を確認する",
};

/** ボードの列（状態ごとに束ねる）。 */
export interface BoardColumn {
  readonly status: ExpectationStatus;
  readonly expectations: readonly Expectation[];
}

/** 列の並び。**Violated を先に出す**（手を付けるべきものが上）。 */
const COLUMN_ORDER: readonly ExpectationStatus[] = [
  "Violated",
  "Undetermined",
  "Active",
  "Created",
  "Fulfilled",
  "Learned",
];

/**
 * 期待をボードの列に分ける。
 *
 * **空の列も出す。** 「違反 0 件」と「違反の列が無い」は違う情報である——後者は、違反を
 * 検出する経路が動いていない可能性を含む。
 */
export function board(
  expectations: readonly Expectation[],
): readonly BoardColumn[] {
  return COLUMN_ORDER.map((status) => ({
    status,
    expectations: expectations.filter(
      (expectation) => expectation.status === status,
    ),
  }));
}

/** 期限切れが近い期待（`Active` のうち、残り時間が閾値を切ったもの）。 */
export function dueSoon(
  expectations: readonly Expectation[],
  now: string,
  withinMinutes = 30,
): readonly Expectation[] {
  const current = Date.parse(now);
  if (Number.isNaN(current)) return [];
  return expectations.filter((expectation) => {
    if (expectation.status !== "Active") return false;
    // `Created` は期限を持たない（効果がまだ無い）。`Active` でも null はありうるので守る
    if (expectation.deadline === null) return false;
    const deadline = Date.parse(expectation.deadline);
    if (Number.isNaN(deadline)) return false;
    const remaining = deadline - current;
    return remaining > 0 && remaining <= withinMinutes * 60_000;
  });
}

/** 概念提案が採否を判断できる形か。 */
export interface CurationCheck {
  readonly ready: boolean;
  readonly problems: readonly string[];
}

/**
 * 概念提案を検査する（設計書 §11.2）。
 *
 * **却下した代替案が無い提案を採択させない。** 「この新概念が必要だ」という主張は、
 * 既存の概念で説明できないことを示して初めて意味を持つ。代替案の検討が無い提案は、
 * 単に「クラスタが見つかった」と言っているだけである。
 */
export function checkProposal(proposal: ConceptProposal): CurationCheck {
  const problems: string[] = [];
  if (proposal.rejectedAlternatives.length === 0) {
    problems.push(
      "却下した代替案が無い。既存概念で説明できないことを示さない提案は採否を判断できない（設計書 §11.2）",
    );
  }
  if (proposal.definition.trim().length < 20) {
    problems.push("定義が短すぎる（なぜこの区別が必要かを 20 文字以上で）");
  }
  if (proposal.name.trim() === "") {
    problems.push("ローカル名が無い（オントロジーに入る名前が決まっていない）");
  }
  if (proposal.supportCount < 10) {
    problems.push(
      `支持する標本が ${proposal.supportCount} 件しかない（偶然のクラスタと区別できない）`,
    );
  }
  if (proposal.coverage > 0.5) {
    problems.push(
      `既存概念での被覆率が ${(proposal.coverage * 100).toFixed(0)}% ある（新概念でなく既存概念の調整で足りる可能性）`,
    );
  }
  return { ready: problems.length === 0, problems };
}
