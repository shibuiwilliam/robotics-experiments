/**
 * 証拠の三源並置の純粋関数（M8-17、ADR-0020）。
 *
 * 重点:
 *  1. 寄与が無い点は「記録されていない」で、寄与 0（寄与なし）と区別される。
 *  2. 割合は生の大きさから出す。全部 0 のときに NaN を作らない。
 *  3. 並置は源ごとの事実だけを出し、どちらが正しいかを示さない。
 *  4. 区間の無い不一致は、推測で区間を作らず「並置できない」と理由を返す。
 */

import { describe, expect, it } from "vitest";

import {
  evidenceTargets,
  pointRows,
  shares,
  sourceColumns,
} from "../src/lib/evidence";
import type {
  BeliefEvidence,
  Discrepancy,
  EvidenceContribution,
  EvidencePoint,
} from "../src/lib/types";

const RUNNING = "https://sbp.example/ent#Running";
const PUMP = "https://sbp.example/site/siteA/asset/P-101";

function contribution(
  overrides: Partial<EvidenceContribution> = {},
): EvidenceContribution {
  return {
    observed: 0,
    record: 0,
    plan: 0,
    dominant: null,
    prior: 0.5,
    sourceEventId: "evt-1",
    excludedRecords: 0,
    ...overrides,
  };
}

function point(overrides: Partial<EvidencePoint> = {}): EvidencePoint {
  return {
    assertionId: "a-1",
    at: "2026-11-07T09:00:00Z",
    probability: 0.8,
    origin: "update",
    contribution: contribution({ observed: 1 }),
    ...overrides,
  };
}

function evidence(overrides: Partial<BeliefEvidence> = {}): BeliefEvidence {
  return {
    entity: PUMP,
    fluent: RUNNING,
    start: "2026-11-07T09:00:00Z",
    end: "2026-11-07T10:00:00Z",
    points: [],
    planned: [],
    contributionsTruncatedAfter: null,
    ...overrides,
  };
}

function discrepancy(overrides: Partial<Discrepancy> = {}): Discrepancy {
  return {
    discrepancyId: "d-1",
    kind: 2,
    status: "Open",
    subjects: [PUMP],
    severity: 0.5,
    detectedAt: "2026-11-07T09:30:00Z",
    detector: "kind2",
    assertions: [RUNNING],
    evidence: [],
    assigneeRole: null,
    ontologyVersion: "0.6.0",
    intervalStart: "2026-11-07T09:00:00Z",
    intervalEnd: "2026-11-07T09:10:00Z",
    fluentRefs: [RUNNING],
    ...overrides,
  };
}

describe("shares", () => {
  it("生の大きさを割合にする（合計が 1）", () => {
    const result = shares(contribution({ observed: 2, record: 1, plan: 1 }));
    expect(result.observed).toBeCloseTo(0.5);
    expect(result.record).toBeCloseTo(0.25);
    expect(result.observed + result.record + result.plan).toBeCloseTo(1);
  });

  it("全部 0 のときは NaN を作らず 0 を返す", () => {
    expect(shares(contribution())).toEqual({ observed: 0, record: 0, plan: 0 });
  });
});

describe("sourceColumns", () => {
  it("寄与が 1 点も記録されていなければ、観測・記録は「記録されていない」（寄与なしではない）", () => {
    const columns = sourceColumns(
      evidence({ points: [point({ contribution: null }), point({ contribution: null })] }),
    );
    expect(columns.find((c) => c.source === "observed")?.state).toBe("unrecorded");
    expect(columns.find((c) => c.source === "record")?.state).toBe("unrecorded");
    expect(columns.find((c) => c.source === "observed")?.note).toContain("記録されていない");
  });

  it("点が 1 つも無いときは「信念が無い」と言い、寄与なしとは言わない", () => {
    const columns = sourceColumns(evidence());
    expect(columns.find((c) => c.source === "observed")?.state).toBe("no-beliefs");
  });

  it("記録のある点があれば、効かなかった源は「寄与なし」と明示する", () => {
    const columns = sourceColumns(
      evidence({
        points: [point({ contribution: contribution({ observed: 2, dominant: "observed" }) })],
      }),
    );
    expect(columns.find((c) => c.source === "observed")?.state).toBe("contributed");
    expect(columns.find((c) => c.source === "record")?.state).toBe("none");
    expect(columns.find((c) => c.source === "record")?.note).toBe("寄与なし");
  });

  it("支配的だった点の数を源ごとに数える", () => {
    const columns = sourceColumns(
      evidence({
        points: [
          point({ assertionId: "a", contribution: contribution({ observed: 2, dominant: "observed" }) }),
          point({ assertionId: "b", contribution: contribution({ record: 3, dominant: "record" }) }),
          point({ assertionId: "c", contribution: contribution({ record: 1, observed: 0.5, dominant: "record" }) }),
        ],
      }),
    );
    expect(columns.find((c) => c.source === "record")?.dominantCount).toBe(2);
    expect(columns.find((c) => c.source === "observed")?.dominantCount).toBe(1);
  });

  it("記録のある点と無い点が混ざるとき、記録されていない点の数を出す", () => {
    const columns = sourceColumns(
      evidence({
        points: [
          point({ contribution: contribution({ plan: 1, dominant: "plan" }) }),
          point({ assertionId: "b", contribution: null, origin: "coupling" }),
        ],
      }),
    );
    expect(columns.find((c) => c.source === "plan")?.unrecordedPoints).toBe(1);
  });

  it("計画の源は区間と重なる計画の数を出し、無ければ「計画なし」と言う", () => {
    const none = sourceColumns(evidence()).find((c) => c.source === "plan");
    expect(none?.plannedSpans).toBe(0);
    const some = sourceColumns(
      evidence({ planned: [{ start: "2026-11-07T08:00:00Z", end: null, adherence: 0.9 }] }),
    ).find((c) => c.source === "plan");
    expect(some?.plannedSpans).toBe(1);
  });

  it("どちらが正しいかを示す語を作らない", () => {
    const text = JSON.stringify(
      sourceColumns(evidence({ points: [point({ contribution: contribution({ record: 3, dominant: "record" }) })] })),
    );
    expect(text).not.toContain("正しい");
  });
});

describe("上限で切れた読み出し", () => {
  const after = "2026-11-07T09:30:00Z";
  const cut = evidence({
    contributionsTruncatedAfter: after,
    points: [
      point({ assertionId: "a", at: "2026-11-07T09:10:00Z", contribution: contribution({ observed: 2, dominant: "observed" }) }),
      point({ assertionId: "b", at: "2026-11-07T09:40:00Z", contribution: null }),
    ],
  });

  it("切れた後の点は「読めていない」で、「記録されていない」と言わない", () => {
    const rows = pointRows(cut.points, cut);
    expect(rows[0]?.contribution).toBe("記録あり");
    expect(rows[1]?.contribution).toBe("読めていない（上限）");
  });

  it("列は読めていない点の数を持ち、記録されていない点には数えない", () => {
    const column = sourceColumns(cut).find((c) => c.source === "observed");
    expect(column?.unreadPoints).toBe(1);
    expect(column?.unrecordedPoints).toBe(0);
  });

  it("切れていなければ、同じ点は「記録されていない」", () => {
    const rows = pointRows(cut.points, { contributionsTruncatedAfter: null });
    expect(rows[1]?.contribution).toBe("記録されていない");
  });
});

describe("記録のある点と無い点が混ざる列", () => {
  it("記録のある点で 0 でも、記録されていない点があれば「寄与なし」に言い添える", () => {
    const mixed = evidence({
      points: [
        point({ assertionId: "a", contribution: contribution({ observed: 1, dominant: "observed" }) }),
        point({ assertionId: "b", contribution: null }),
      ],
    });
    const column = sourceColumns(mixed).find((c) => c.source === "plan");
    expect(column?.state).toBe("none");
    expect(column?.note).toContain("記録のある点のみ");
    expect(column?.note).toContain("記録されていない点 1 件");
  });
});

describe("pointRows", () => {
  it("結合が書いた信念は出どころを「結合」と出し、寄与は記録されていないと言う", () => {
    const rows = pointRows([point({ origin: "coupling", contribution: null })]);
    expect(rows[0]?.origin).toBe("結合");
    expect(rows[0]?.contribution).toBe("記録されていない");
  });

  it("寄与のある点は割合を出す", () => {
    const rows = pointRows([
      point({ contribution: contribution({ observed: 1, record: 3, dominant: "record" }) }),
    ]);
    expect(rows[0]?.shares?.record).toBeCloseTo(0.75);
    expect(rows[0]?.dominant).toBe("record");
  });

  it("除外した記録があれば隔離が働いたことを出す", () => {
    const rows = pointRows([point({ contribution: contribution({ observed: 1, excludedRecords: 2 }) })]);
    expect(rows[0]?.excludedRecords).toBe(2);
  });
});

describe("evidenceTargets", () => {
  const now = "2026-11-07T12:00:00Z";

  it("主体 × フルーエントの組を、不一致の区間で作る", () => {
    const targets = evidenceTargets(discrepancy(), now);
    expect(targets).toEqual({
      kind: "targets",
      targets: [
        {
          entity: PUMP,
          fluent: RUNNING,
          start: "2026-11-07T09:00:00Z",
          end: "2026-11-07T09:10:00Z",
        },
      ],
    });
  });

  it("終端が無い（継続中）なら現在までを区間にする", () => {
    const result = evidenceTargets(discrepancy({ intervalEnd: null }), now);
    expect(result.kind === "targets" && result.targets[0]?.end).toBe(now);
  });

  it("区間が記録されていなければ、区間を推測せず理由を返す", () => {
    const result = evidenceTargets(discrepancy({ intervalStart: null }), now);
    expect(result.kind).toBe("unavailable");
    expect(result.kind === "unavailable" && result.reason).toContain("区間");
  });

  it("API が決めたフルーエント型（fluentRefs）だけを鍵にする", () => {
    // イベント型（ent#StopCmd）は IRI の形がフルーエントと同じ。形で判定すると信念を引けない鍵を作る
    const result = evidenceTargets(
      discrepancy({
        assertions: ["https://sbp.example/ent#StopCmd"],
        fluentRefs: [],
      }),
      now,
    );
    expect(result.kind).toBe("unavailable");
    expect(result.kind === "unavailable" && result.reason).toContain("フルーエント");
  });

  it("複数の主体は全部並べる（類型 4 の競合は 2 つの実体の話）", () => {
    const result = evidenceTargets(discrepancy({ subjects: [PUMP, "https://sbp.example/site/siteA/asset/P-102"] }), now);
    expect(result.kind === "targets" && result.targets.length).toBe(2);
  });
});
