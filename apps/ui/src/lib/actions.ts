/**
 * アクション承認の純粋関数（設計書 §9.2、M8-22、ADR-0021）。
 *
 * **承認の要否を再実装しない。** 要否は契約が決める（`medium` 以上は常に必須、`low` は条件）。UI は
 * 条件をそのまま言う。**契約の写しを持たない**（契約を変えるたびに UI が古くなる）。
 *
 * **失敗した判断は自動では再試行されない。** 理由を読み、新しい判断として再投入する。「待てば
 * 直る」と読ませる文言を作らない。
 */

import type {
  ActionRequest,
  ActionType,
  DecisionStatus,
  Discrepancy,
} from "./types";

/** 入力の検査結果。 */
export interface ActionCheck {
  readonly valid: boolean;
  readonly problems: readonly string[];
}

/**
 * 実行要求を検査する。**理由が無いものを受け付けない**（理由の無い実行は監査で読めない）。
 * 上書きは、有効にしたなら理由を書かせる（空の理由で安全ガードを外させない）。
 */
export function checkAction(
  request: ActionRequest,
  known: readonly ActionType[],
): ActionCheck {
  const problems: string[] = [];
  if (request.actionType === "") {
    problems.push("アクションを選ぶ");
  } else if (!known.some((item) => item.actionType === request.actionType)) {
    problems.push("契約に無いアクション型（契約は API が持つ）");
  }
  if (request.target === "") problems.push("対象を選ぶ");
  if (request.reason.trim() === "") {
    problems.push("理由は必須（理由の無い実行は監査で読めない）");
  }
  if (request.overrideReason !== null && request.overrideReason.trim() === "") {
    problems.push("前提未達を上書きするなら理由を書く（安全ガードを外す操作）");
  }
  return { valid: problems.length === 0, problems };
}

/** 承認の要否の説明（契約の条件をそのまま言う）。 */
export function approvalNotice(contract: ActionType): string {
  switch (contract.approval) {
    case "always":
      return `承認が必須（リスク級 ${contract.riskClass}）。この投入が承認になる · 承認者のロール: ${contract.approvalRole}`;
    case "if_severity_high":
      return `起因の不一致の深刻度が高いとき承認が必要。この投入が承認になる · 承認者のロール: ${contract.approvalRole}`;
    case "never":
      return "承認は不要（リスク級 low）";
  }
}

/** 実行要求を作る。対象・深刻度・不一致 ID は不一致から入れる（人が書き換えない）。 */
export function buildActionRequest(params: {
  readonly decisionId: string;
  readonly discrepancy: Discrepancy;
  readonly actionType: string;
  readonly target: string;
  readonly approvedBy: string;
  readonly reason: string;
  readonly overrideReason: string | null;
}): ActionRequest {
  return {
    decisionId: params.decisionId,
    actionType: params.actionType,
    target: params.target,
    approvedBy: params.approvedBy,
    reason: params.reason.trim(),
    severity: params.discrepancy.severity,
    correlationId: params.discrepancy.discrepancyId,
    overrideReason: params.overrideReason === null ? null : params.overrideReason.trim(),
  };
}

/** 適用の要約（JSON 文字列）を安全に読む。読めなければ空。 */
export function parseArtifacts(artifacts: string): Readonly<Record<string, unknown>> {
  try {
    const parsed: unknown = JSON.parse(artifacts);
    return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

/** 判断 1 件の表示。 */
export interface DecisionView {
  readonly decisionId: string;
  readonly state: DecisionStatus["state"];
  readonly label: string;
  /** 人が読む説明。**失敗の理由は整形せずそのまま**入れる。 */
  readonly message: string;
  /** 新しい判断として再投入できるか（失敗のとき）。 */
  readonly canResubmit: boolean;
}

/** 判断の状態を人が読む形にする。 */
export function describeDecision(status: DecisionStatus): DecisionView {
  if (status.state === "failed") {
    return {
      decisionId: status.decisionId,
      state: status.state,
      label: "失敗",
      message: `${status.error ?? "理由が記録されていない"}`,
      canResubmit: true,
    };
  }
  if (status.state === "applied") {
    const artifacts = parseArtifacts(status.artifacts);
    const parts: string[] = [];
    if (typeof artifacts["expectation_id"] === "string") {
      parts.push(`期待を作った（${String(artifacts["expectation_id"]).slice(0, 8)}）`);
    }
    if (artifacts["overridden"] === true) parts.push("前提未達を上書きして実行");
    return {
      decisionId: status.decisionId,
      state: status.state,
      label: "実行した",
      message: parts.length === 0 ? "適用した" : parts.join(" · "),
      canResubmit: false,
    };
  }
  return {
    decisionId: status.decisionId,
    state: status.state,
    label: "適用待ち",
    message: "learning-worker が処理する（受け付けただけで、実行はまだ）",
    canResubmit: false,
  };
}

/** 失敗した判断の再投入の案内（同じ判断は再試行されない）。 */
export const RESUBMIT_NOTE =
  "失敗した判断は自動では再試行されない。理由を読み、前提が整ってから新しい判断として投入する。";

/** 適用待ちが残っているか（状態の自動更新を続けるか）。 */
export function hasPending(statuses: readonly DecisionStatus[]): boolean {
  return statuses.some((status) => status.state === "pending");
}
