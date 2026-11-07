/**
 * 不一致キューと期待ボードのテスト（M7-04）。
 *
 * 重点: 並び順が深刻度（起票時刻ではない）、証拠が無い源も行として出ること、理由の無い
 * 解決を受け付けないこと、違反の帰属が記号面と WM 面の組み合わせで決まること。
 */

import { describe, expect, it } from "vitest";

import {
  BLAME_EXPLANATION,
  blameFor,
  board,
  checkProposal,
  dueSoon,
} from "../src/lib/expectations";
import {
  checkResolution,
  referenceRows,
  isActionable,
  isStale,
  queueOrder,
  visibleQueue,
} from "../src/lib/queue";
import type {
  ConceptProposal,
  Discrepancy,
  Expectation,
  Resolution,
} from "../src/lib/types";

function discrepancy(overrides: Partial<Discrepancy> & { discrepancyId: string }): Discrepancy {
  return {
    // API の契約そのもの（番号の類型・API の状態名・複数主体・IRI の参照）。M8-16 で揃えた
    kind: 2,
    status: "Open",
    subjects: ["urn:x:P-101"],
    severity: 0.5,
    detectedAt: "2026-06-10T09:00:00Z",
    detector: "kind2",
    assertions: [],
    evidence: [],
    assigneeRole: null,
    ontologyVersion: "0.5.0",
    intervalStart: "2026-06-10T09:00:00Z",
    intervalEnd: "2026-06-10T09:10:00Z",
    fluentRefs: [],
    ...overrides,
  };
}

function expectation(overrides: Partial<Expectation> & { expectationId: string }): Expectation {
  return {
    // API の契約そのもの（M8-18 で揃えた。`target` / `createdAt` / `method` / `effectCount`）
    status: "Active",
    actionType: "ent:StopCmd",
    target: "urn:x:P-101",
    createdAt: "2026-06-10T09:00:00Z",
    deadline: "2026-06-10T10:00:00Z",
    symbolicVerdict: null,
    rolloutVerdict: null,
    method: null,
    effectCount: 1,
    ontologyVersion: "0.5.0",
    ...overrides,
  };
}

describe("queueOrder", () => {
  it("深刻度の降順（起票時刻順ではない）", () => {
    const rows = [
      discrepancy({ discrepancyId: "old-mild", severity: 0.2, detectedAt: "2026-06-01T00:00:00Z" }),
      discrepancy({ discrepancyId: "new-severe", severity: 0.9, detectedAt: "2026-06-10T00:00:00Z" }),
    ];
    expect(queueOrder(rows).map((r) => r.discrepancyId)).toEqual(["new-severe", "old-mild"]);
  });

  it("深刻度が同じなら古い順（放置を可視化する）", () => {
    const rows = [
      discrepancy({ discrepancyId: "newer", severity: 0.5, detectedAt: "2026-06-10T00:00:00Z" }),
      discrepancy({ discrepancyId: "older", severity: 0.5, detectedAt: "2026-06-01T00:00:00Z" }),
    ];
    expect(queueOrder(rows).map((r) => r.discrepancyId)).toEqual(["older", "newer"]);
  });

  it("入力を破壊しない（React の state をその場で並べ替えると再描画されない）", () => {
    const rows = [
      discrepancy({ discrepancyId: "a", severity: 0.1 }),
      discrepancy({ discrepancyId: "b", severity: 0.9 }),
    ];
    queueOrder(rows);
    expect(rows.map((r) => r.discrepancyId)).toEqual(["a", "b"]);
  });
});

describe("visibleQueue", () => {
  it("解決済み・抑制済みは出さない", () => {
    const rows = [
      discrepancy({ discrepancyId: "open", status: "Open" }),
      discrepancy({ discrepancyId: "assigned", status: "Assigned" }),
      discrepancy({ discrepancyId: "resolved", status: "Resolved" }),
      discrepancy({ discrepancyId: "suppressed", status: "Suppressed" }),
      discrepancy({ discrepancyId: "learned", status: "Learned" }),
    ];
    expect(visibleQueue(rows).map((r) => r.discrepancyId).sort()).toEqual([
      "assigned",
      "open",
    ]);
  });

  it("triaged は手を付ける対象である", () => {
    expect(isActionable(discrepancy({ discrepancyId: "t", status: "Triaged" }))).toBe(true);
  });
});

describe("referenceRows", () => {
  it("参照が無い種別も行として出す", () => {
    const rows = referenceRows(discrepancy({ discrepancyId: "a" }));
    expect(rows.map((row) => row.label)).toEqual([
      "根拠アサーション",
      "証拠（イベント・記録）",
    ]);
    expect(rows.every((row) => row.isEmpty)).toBe(true);
  });

  it("「根拠が無かった」と「見ていない」を区別できる", () => {
    // 存在の不一致はまさに片側が無い場面であり、行が消えると判断材料が消える
    const rows = referenceRows(
      discrepancy({
        discrepancyId: "a",
        kind: 1,
        assertions: ["urn:sbp:assertion:1"],
      }),
    );
    expect(rows[0]!.isEmpty).toBe(false);
    expect(rows[0]!.iris).toEqual(["urn:sbp:assertion:1"]);
    expect(rows[1]!.isEmpty).toBe(true);
  });
});

describe("checkResolution", () => {
  function resolution(overrides: Partial<Resolution> = {}): Resolution {
    return {
      // API の契約そのもの（M8-16 で揃えた。snake_case への変換は api.ts が行う）
      decisionId: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
      discrepancyId: "0f8fad5b-d9cb-469f-a165-70867728950e",
      outcome: "physical_correct",
      note: "MES の完了打刻が実際の停止より 20 分遅れていた",
      resolvedBy: "urn:x:person/op1",
      ...overrides,
    };
  }

  it("理由があれば通る", () => {
    expect(checkResolution(resolution()).valid).toBe(true);
  });

  it("理由が無いと通さない（種別だけでは学習信号にならない）", () => {
    const check = checkResolution(resolution({ note: "ミス" }));
    expect(check.valid).toBe(false);
    expect(check.problems.join()).toContain("学習信号");
  });

  it("空白だけの理由も通さない", () => {
    expect(checkResolution(resolution({ note: "          " })).valid).toBe(false);
  });

  it("解決者が必要（来歴に human-review として残る）", () => {
    const check = checkResolution(resolution({ resolvedBy: "" }));
    expect(check.valid).toBe(false);
    expect(check.problems.join()).toContain("解決者");
  });

  it("UUID でない不一致 ID を通さない（API が受け付けない。所見 77）", () => {
    const check = checkResolution(resolution({ discrepancyId: "d-1" }));
    expect(check.valid).toBe(false);
    expect(check.problems.join()).toContain("UUID");
  });

  it("冪等の鍵が必要（再送で 2 回適用されるのを防ぐ）", () => {
    const check = checkResolution(resolution({ decisionId: "" }));
    expect(check.valid).toBe(false);
    expect(check.problems.join()).toContain("冪等");
  });

  it("問題は全部返す（1 つ直すたびに次が出る形にしない）", () => {
    const check = checkResolution(
      resolution({
        note: "",
        resolvedBy: "",
        discrepancyId: "",
        decisionId: "",
      }),
    );
    expect(check.problems.length).toBe(4);
  });
});

describe("isStale", () => {
  it("72 時間を超えた不一致を滞留と見る", () => {
    const row = discrepancy({ discrepancyId: "a", detectedAt: "2026-06-01T00:00:00Z" });
    expect(isStale(row, "2026-06-10T00:00:00Z")).toBe(true);
    expect(isStale(row, "2026-06-02T00:00:00Z")).toBe(false);
  });

  it("時刻が壊れていたら滞留と言わない（偽の警告を出さない）", () => {
    const row = discrepancy({ discrepancyId: "a", detectedAt: "not-a-time" });
    expect(isStale(row, "2026-06-10T00:00:00Z")).toBe(false);
  });
});

describe("blameFor", () => {
  it("記号面だけ外れたら規則・オントロジーを疑う", () => {
    const row = expectation({
      expectationId: "e",
      symbolicVerdict: false,
      rolloutVerdict: true,
    });
    expect(blameFor(row)).toBe("symbolic");
    expect(BLAME_EXPLANATION[blameFor(row)]).toContain("事象計算規則");
  });

  it("rollout 面だけ外れたら WM を疑う", () => {
    expect(
      blameFor(expectation({ expectationId: "e", symbolicVerdict: true, rolloutVerdict: false })),
    ).toBe("worldmodel");
  });

  it("両面が外れたらアクションが失敗した", () => {
    expect(
      blameFor(expectation({ expectationId: "e", symbolicVerdict: false, rolloutVerdict: false })),
    ).toBe("action");
  });

  it("両面が成立したら帰属先は無い", () => {
    expect(
      blameFor(expectation({ expectationId: "e", symbolicVerdict: true, rolloutVerdict: true })),
    ).toBe("none");
  });

  it("どちらも未判定なら unknown（null を「成立」として扱わない）", () => {
    expect(blameFor(expectation({ expectationId: "e" }))).toBe("unknown");
  });

  it("片面しか判定が無くても、その面が成立なら帰属先は無い", () => {
    // 効果を 1 面しか宣言していない期待では、もう片面が null のままになる
    expect(
      blameFor(expectation({ expectationId: "e", symbolicVerdict: true, rolloutVerdict: null })),
    ).toBe("none");
  });
});

describe("board", () => {
  it("違反を先頭の列に置く", () => {
    expect(board([])[0]!.status).toBe("Violated");
  });

  it("空の列も出す（「違反 0 件」と「違反の列が無い」は違う）", () => {
    const columns = board([expectation({ expectationId: "a", status: "Fulfilled" })]);
    const violated = columns.find((column) => column.status === "Violated");
    expect(violated).toBeDefined();
    expect(violated!.expectations).toEqual([]);
  });

  it("全状態に列がある", () => {
    expect(board([]).length).toBe(6);
  });
});

describe("dueSoon", () => {
  it("検証中で期限が 30 分以内のものを返す", () => {
    const rows = [
      expectation({ expectationId: "soon", deadline: "2026-06-10T10:00:00Z" }),
      expectation({ expectationId: "later", deadline: "2026-06-10T12:00:00Z" }),
    ];
    expect(dueSoon(rows, "2026-06-10T09:45:00Z").map((r) => r.expectationId)).toEqual(["soon"]);
  });

  it("期限を過ぎたものは「近い」ではない（別の問題である）", () => {
    const rows = [expectation({ expectationId: "past", deadline: "2026-06-10T09:00:00Z" })];
    expect(dueSoon(rows, "2026-06-10T09:45:00Z")).toEqual([]);
  });

  it("検証中以外は数えない", () => {
    const rows = [
      expectation({ expectationId: "done", status: "Fulfilled", deadline: "2026-06-10T10:00:00Z" }),
    ];
    expect(dueSoon(rows, "2026-06-10T09:45:00Z")).toEqual([]);
  });
});

describe("checkProposal", () => {
  function proposal(overrides: Partial<ConceptProposal> = {}): ConceptProposal {
    return {
      proposalId: "p-1",
      name: "VibrationAnomaly",
      definition: "既存の Faulted では説明できない振動の増大が停止の前段に現れる",
      superclass: "Fluent",
      status: "proposed",
      rejectedAlternatives: ["Faulted の閾値調整", "UnderMaintenance の拡張"],
      supportCount: 548,
      coverage: 0.0,
      signalCount: 2,
      proposedAt: "2026-06-10T09:00:00Z",
      curationNote: "",
      ...overrides,
    };
  }

  it("代替案と根拠と標本が揃えば採否を判断できる", () => {
    expect(checkProposal(proposal()).ready).toBe(true);
  });

  it("却下した代替案が無い提案は採択させない", () => {
    const check = checkProposal(proposal({ rejectedAlternatives: [] }));
    expect(check.ready).toBe(false);
    expect(check.problems.join()).toContain("却下した代替案");
  });

  it("標本が少ない提案は偶然のクラスタと区別できない", () => {
    const check = checkProposal(proposal({ supportCount: 3 }));
    expect(check.ready).toBe(false);
    expect(check.problems.join()).toContain("偶然");
  });

  it("既存概念での被覆率が高いなら新概念でなくてよい", () => {
    const check = checkProposal(proposal({ coverage: 0.8 }));
    expect(check.ready).toBe(false);
    expect(check.problems.join()).toContain("既存概念");
  });

  it("定義が短い提案は通さない", () => {
    expect(checkProposal(proposal({ definition: "必要" })).ready).toBe(false);
  });

  it("ローカル名が無い提案は通さない", () => {
    // **オントロジーに入る名前が決まっていない提案は採択できない**（採択は MINOR 版の起票に
    // 進む。M6-04）
    const check = checkProposal(proposal({ name: "  " }));
    expect(check.ready).toBe(false);
    expect(check.problems.join()).toContain("ローカル名");
  });
});


describe("dueSoon（期限が無い期待）", () => {
  it("期限が null の期待は「近い」ではない（Created は効果をまだ持たない）", () => {
    // 以前の型は deadline を必須にしていたので、`Created` を表現できなかった（所見 86）
    const rows = [expectation({ expectationId: "fresh", status: "Active", deadline: null })];
    expect(dueSoon(rows, "2026-06-10T09:45:00Z")).toEqual([]);
  });
});
