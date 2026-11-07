/**
 * アクション承認の純粋関数（M8-22、ADR-0021）。
 *
 * 重点:
 *  1. 理由の無い実行を受け付けない。上書きは理由を書かせる。
 *  2. 承認の要否を再実装せず、契約の条件をそのまま言う。
 *  3. 失敗の理由を整形せず見せ、再投入を促す（自動で再試行されると読ませない）。
 *  4. API の契約（snake_case、override_reason は上書きしないとき送らない）。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { submitAction } from "../src/lib/api";
import {
  RESUBMIT_NOTE,
  approvalNotice,
  buildActionRequest,
  checkAction,
  describeDecision,
  hasPending,
  parseArtifacts,
} from "../src/lib/actions";
import type { ActionRequest, ActionType, DecisionStatus, Discrepancy } from "../src/lib/types";

const INSPECT = "https://sbp.example/ent#CreateEmergencyInspection";
const PUMP = "https://sbp.example/site/siteA/asset/P-101";

function contract(overrides: Partial<ActionType> = {}): ActionType {
  return {
    actionType: INSPECT,
    targets: "https://sbp.example/ent#Pump",
    riskClass: "medium",
    approval: "always",
    approvalRole: "https://sbp.example/role/maintenance-planner",
    preconditions: [{ face: "physical", descriptions: ["稼働していない"] }],
    effects: [{ face: "business", effects: [{ description: "作業指示が起票される", within: "PT1H" }] }],
    compensation: "https://sbp.example/ent#CancelInspection",
    rateLimit: { window: "PT1H", max: 1 },
    ...overrides,
  };
}

function request(overrides: Partial<ActionRequest> = {}): ActionRequest {
  return {
    decisionId: "d-1",
    actionType: INSPECT,
    target: PUMP,
    approvedBy: "https://sbp.example/id/person/dev",
    reason: "異常振動の点検",
    severity: 0.8,
    correlationId: "0f8fad5b-d9cb-469f-a165-70867728950e",
    overrideReason: null,
    ...overrides,
  };
}

function status(overrides: Partial<DecisionStatus> = {}): DecisionStatus {
  return {
    decisionId: "d-1",
    kind: "action",
    subject: PUMP,
    decidedBy: "https://sbp.example/id/person/dev",
    state: "applied",
    receivedAt: "2026-11-08T09:00:00Z",
    appliedAt: "2026-11-08T09:00:01Z",
    failedAt: null,
    error: null,
    artifacts: JSON.stringify({ expectation_id: "e2a1b3c4-0000", overridden: false }),
    attempts: 1,
    ...overrides,
  };
}

describe("checkAction", () => {
  it("そろっていれば通る", () => {
    expect(checkAction(request(), [contract()]).valid).toBe(true);
  });

  it("理由が空白だけなら通さない（理由の無い実行は監査で読めない）", () => {
    const result = checkAction(request({ reason: "   " }), [contract()]);
    expect(result.valid).toBe(false);
    expect(result.problems.join()).toContain("理由は必須");
  });

  it("契約に無いアクション型は通さない（契約は API が持つ）", () => {
    const result = checkAction(request({ actionType: "urn:x:nope" }), [contract()]);
    expect(result.valid).toBe(false);
    expect(result.problems.join()).toContain("契約に無い");
  });

  it("アクション未選択・対象未選択を通さない", () => {
    expect(checkAction(request({ actionType: "" }), [contract()]).valid).toBe(false);
    expect(checkAction(request({ target: "" }), [contract()]).valid).toBe(false);
  });

  it("上書きを有効にして理由が空なら通さない（安全ガードを外す操作）", () => {
    const result = checkAction(request({ overrideReason: "  " }), [contract()]);
    expect(result.valid).toBe(false);
    expect(result.problems.join()).toContain("上書き");
  });

  it("上書きしない（null）なら理由は要らない", () => {
    expect(checkAction(request({ overrideReason: null }), [contract()]).valid).toBe(true);
  });
});

describe("approvalNotice", () => {
  it("常に必須の契約はロールとともに承認が必須だと言う", () => {
    const text = approvalNotice(contract({ approval: "always" }));
    expect(text).toContain("承認が必須");
    expect(text).toContain("maintenance-planner");
  });

  it("条件付きの契約は深刻度が高いときだと条件をそのまま言う（判定を再実装しない）", () => {
    const text = approvalNotice(contract({ approval: "if_severity_high", riskClass: "low" }));
    expect(text).toContain("深刻度が高いとき");
  });

  it("不要な契約はその旨を言う", () => {
    expect(approvalNotice(contract({ approval: "never", riskClass: "low" }))).toContain("不要");
  });
});

describe("buildActionRequest", () => {
  const discrepancy: Discrepancy = {
    discrepancyId: "0f8fad5b-d9cb-469f-a165-70867728950e",
    kind: 2,
    status: "Open",
    subjects: [PUMP],
    severity: 0.77,
    detectedAt: "2026-11-08T09:00:00Z",
    detector: "kind2",
    assertions: [],
    evidence: [],
    assigneeRole: null,
    ontologyVersion: "0.6.0",
    intervalStart: null,
    intervalEnd: null,
    fluentRefs: [],
  };

  it("深刻度と不一致 ID は不一致から入れる（人が書き換えない）", () => {
    const built = buildActionRequest({
      decisionId: "d-1",
      discrepancy,
      actionType: INSPECT,
      target: PUMP,
      approvedBy: "p",
      reason: "  点検  ",
      overrideReason: null,
    });
    expect(built.severity).toBe(0.77);
    expect(built.correlationId).toBe(discrepancy.discrepancyId);
    expect(built.reason).toBe("点検");
  });
});

describe("describeDecision", () => {
  it("失敗は理由を整形せずそのまま出し、再投入できる", () => {
    const view = describeDecision(
      status({
        state: "failed",
        appliedAt: null,
        failedAt: "2026-11-08T09:00:01Z",
        error: "ActionError: X on Y failed its preconditions: physical: 稼働していない",
        artifacts: "{}",
      }),
    );
    expect(view.label).toBe("失敗");
    expect(view.message).toBe(
      "ActionError: X on Y failed its preconditions: physical: 稼働していない",
    );
    expect(view.canResubmit).toBe(true);
  });

  it("失敗の案内は自動で再試行されないと言い、待てば直るとは言わない", () => {
    expect(RESUBMIT_NOTE).toContain("自動では再試行されない");
    expect(RESUBMIT_NOTE).not.toContain("待てば");
  });

  it("失敗でも理由が無ければ、無いと言う（空欄にしない）", () => {
    const view = describeDecision(status({ state: "failed", error: null }));
    expect(view.message).toBe("理由が記録されていない");
  });

  it("実行済みは期待の ID と上書きの有無を出す", () => {
    const view = describeDecision(
      status({ artifacts: JSON.stringify({ expectation_id: "abcdef12-xxxx", overridden: true }) }),
    );
    expect(view.message).toContain("abcdef12");
    expect(view.message).toContain("上書き");
    expect(view.canResubmit).toBe(false);
  });

  it("適用待ちは受け付けただけで実行はまだだと言う", () => {
    const view = describeDecision(status({ state: "pending", appliedAt: null, artifacts: "{}" }));
    expect(view.label).toBe("適用待ち");
    expect(view.message).toContain("実行はまだ");
  });
});

describe("parseArtifacts / hasPending", () => {
  it("壊れた JSON でも落ちず空を返す", () => {
    expect(parseArtifacts("not json")).toEqual({});
    expect(parseArtifacts("[1,2]")).toEqual({});
  });

  it("適用待ちが残っているときだけ true", () => {
    expect(hasPending([status({ state: "pending" })])).toBe(true);
    expect(hasPending([status(), status({ state: "failed" })])).toBe(false);
  });
});

describe("submitAction", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function stub(response: { ok?: boolean; status?: number; body: unknown }) {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: response.ok ?? true,
      status: response.status ?? 200,
      json: async () => response.body,
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("API の契約どおり snake_case で送り、上書きしないときは override_reason を送らない", async () => {
    const fetchMock = stub({ body: { accepted: true, detail: "受け付けた" } });
    await submitAction("http://api", request());
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body)) as Record<string, unknown>;
    expect(body["decision_id"]).toBe("d-1");
    expect(body["action_type"]).toBe(INSPECT);
    expect(body["approved_by"]).toBe("https://sbp.example/id/person/dev");
    expect(body["correlation_id"]).toBe("0f8fad5b-d9cb-469f-a165-70867728950e");
    expect("override_reason" in body).toBe(false);
    expect(fetchMock.mock.calls[0]![0]).toBe("http://api/commands/execute_action");
  });

  it("上書きするときだけ override_reason を送る", async () => {
    const fetchMock = stub({ body: { accepted: true } });
    await submitAction("http://api", request({ overrideReason: "現場確認済み" }));
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body)) as Record<string, unknown>;
    expect(body["override_reason"]).toBe("現場確認済み");
  });

  it("不一致が無い（correlation_id が null）なら送らない", async () => {
    const fetchMock = stub({ body: { accepted: true } });
    await submitAction("http://api", request({ correlationId: null }));
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body)) as Record<string, unknown>;
    expect("correlation_id" in body).toBe(false);
  });

  it("API が弾いたら受け付けていないと返し、理由を失わない", async () => {
    stub({ ok: false, status: 409, body: { detail: "unknown command" } });
    const result = await submitAction("http://api", request());
    expect(result).toEqual({ accepted: false, detail: "unknown command" });
  });
});
