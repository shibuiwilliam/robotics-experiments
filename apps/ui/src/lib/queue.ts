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
  Discrepancy,
  DiscrepancyState,
  Resolution,
  ResolutionOutcome,
} from "./types";

/**
 * キューに出す状態（解決済み・抑制済みは出さない）。
 *
 * **API も終端状態を既定で除く**（ADR-0013）が、ここでも絞る——`includeTerminal: true` で
 * 履歴を読んだときに、同じ一覧をキューとして表示しないためである。
 */
const ACTIONABLE: ReadonlySet<DiscrepancyState> = new Set<DiscrepancyState>([
  "Open",
  "Triaged",
  "Assigned",
]);

/** 人が手を付けるべき不一致か。 */
export function isActionable(discrepancy: Discrepancy): boolean {
  return ACTIONABLE.has(discrepancy.status);
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
    return left.detectedAt < right.detectedAt ? -1 : 1;
  });
}

/** キューに出す一覧（対象を絞ってから並べる）。 */
export function visibleQueue(
  discrepancies: readonly Discrepancy[],
): Discrepancy[] {
  return queueOrder(discrepancies.filter(isActionable));
}

/** 根拠の 1 行（アサーションか証拠か、どの IRI か）。 */
export interface ReferenceRow {
  readonly label: "根拠アサーション" | "証拠（イベント・記録）";
  readonly iris: readonly string[];
  /** 参照が無い種別（「見ていない」ことを空欄で誤魔化さないために明示する）。 */
  readonly isEmpty: boolean;
}

/**
 * 不一致が参照しているものを並べる。**空の種別も行として出す。**
 *
 * 空を行から落とすと、「根拠が無かった」のか「見ていない」のかが区別できない。
 *
 * **三源（観測 / 記録 / 計画）の並置はここでは作れない。** 不一致は参照先を IRI で持ち、
 * 信念の本体を引くには別のクエリが要る。さらに**源は様相では分かれない**——業務記録由来の
 * 信念も推定を経ると `inferred` になり、三源の区別は来歴の `contrib` にある。設計書 §8.2 の
 * 「証拠並置」を実現するには、アサーション ID で信念を引く API と来歴から源を判定する経路が
 * 必要で、別タスク（M8-17）にした。
 */
export function referenceRows(discrepancy: Discrepancy): ReferenceRow[] {
  return [
    {
      label: "根拠アサーション",
      iris: discrepancy.assertions,
      isEmpty: discrepancy.assertions.length === 0,
    },
    {
      label: "証拠（イベント・記録）",
      iris: discrepancy.evidence,
      isEmpty: discrepancy.evidence.length === 0,
    },
  ];
}

/**
 * 解決結果の説明（人が選ぶときに「何が起きるか」を見せる）。
 *
 * **API の語彙そのものを鍵にする**（`ResolutionOutcome`）。UI 独自の語彙を持つと、
 * 対応表を書いた人が居なくなった時点でずれる（所見 83）。
 *
 * 括弧の中は**実際に起こること**で、ADR-0012 の「自動適用するもの / 記録するだけのもの」に
 * 対応している——人に「適用される」と思わせない。
 */
export const RESOLUTION_EFFECTS: Readonly<Record<ResolutionOutcome, string>> = {
  physical_correct:
    "記録訂正の提案を作り、情報源の信頼性更新を記録する（書き戻しは既定 dry）",
  record_correct:
    "プローブの負例バッグを記録する（再学習は昇格ゲートを通る別の仕事で、自動では走らない）",
  both_wrong:
    "概念の候補として記録する（オントロジーへの追加は人の手作業。自動で語彙は増えない）",
  undetermined: "追加観測を要求する（作業キューに積まれ、スケジューラが効用で選ぶ）",
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
  if (resolution.note.trim().length < 10) {
    problems.push(
      "理由を 10 文字以上書くこと。種別だけでは学習信号にならない（なぜそう判断したかが残らない）",
    );
  }
  if (resolution.resolvedBy.trim() === "") {
    problems.push("解決者が必要（来歴に human-review として残る）");
  }
  if (!UUID_PATTERN.test(resolution.discrepancyId)) {
    // API は UUID を要求する（文字列を許すと学習信号が元の不一致に辿れない。所見 77）
    problems.push("不一致の識別子が UUID でない（API が受け付けない）");
  }
  if (resolution.decisionId.trim() === "") {
    problems.push("冪等の鍵が必要（再送で同じ判断が 2 回適用されるのを防ぐ）");
  }
  return { valid: problems.length === 0, problems };
}

/** UUID の形（API の `discrepancy_id` は UUID。所見 77）。 */
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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
  const raised = Date.parse(discrepancy.detectedAt);
  const current = Date.parse(now);
  if (Number.isNaN(raised) || Number.isNaN(current)) return false;
  return current - raised > staleAfterHours * 3_600_000;
}
