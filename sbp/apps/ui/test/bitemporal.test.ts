/**
 * 二時制の表示ロジックのテスト（M7-04）。
 *
 * **重点は「2 つの軸が別の答えを返すこと」である。** 軸を 1 本にまとめた実装でも、片方の軸
 * しか動かさないテストは通る。訂正をまたいで両軸を動かし、答えが違うことを確かめる。
 */

import { describe, expect, it } from "vitest";

import {
  asOf,
  bitemporalSlice,
  containsInstant,
  excludeHypothetical,
  formatConfidence,
  hasNoSource,
  isSuperseded,
  sourceContribution,
  transactionTicks,
  validAt,
} from "../src/lib/bitemporal";
import type { Belief, Modality } from "../src/lib/types";

function belief(overrides: Partial<Belief> & { assertionId: string }): Belief {
  return {
    subject: "urn:x:P-101",
    predicate: "ent:Running",
    object: "true",
    validFrom: "2026-06-10T09:00:00Z",
    validTo: null,
    txFrom: "2026-06-10T09:00:00Z",
    txTo: null,
    modality: "INFERRED" as Modality,
    confidence: 0.9,
    provenance: "prov:b1",
    ontologyVersion: "0.5.0",
    ...overrides,
  };
}

/**
 * 訂正の場面。09:00 に「稼働中」と信じ、10:00 に「停止していた」へ訂正した。
 *
 * - 旧文: tx [09:00, 10:00)、valid [09:00, …)、object = true
 * - 新文: tx [10:00, …)、valid [09:00, …)、object = false
 */
const CORRECTED: readonly Belief[] = [
  belief({
    assertionId: "a-old",
    object: "true",
    txFrom: "2026-06-10T09:00:00Z",
    txTo: "2026-06-10T10:00:00Z",
  }),
  belief({
    assertionId: "a-new",
    object: "false",
    txFrom: "2026-06-10T10:00:00Z",
    txTo: null,
  }),
];

describe("containsInstant", () => {
  it("右開区間である（終端は含まない）", () => {
    expect(containsInstant("2026-06-10T09:00:00Z", "2026-06-10T10:00:00Z", "2026-06-10T09:00:00Z")).toBe(true);
    expect(containsInstant("2026-06-10T09:00:00Z", "2026-06-10T10:00:00Z", "2026-06-10T10:00:00Z")).toBe(false);
  });

  it("終端が null なら開いている", () => {
    expect(containsInstant("2026-06-10T09:00:00Z", null, "2030-01-01T00:00:00Z")).toBe(true);
  });

  it("開始より前は入らない", () => {
    expect(containsInstant("2026-06-10T09:00:00Z", null, "2026-06-10T08:59:59Z")).toBe(false);
  });
});

describe("as-of と valid-at は別の軸である", () => {
  it("as-of を訂正前に戻すと、訂正前の判断が返る", () => {
    const before = asOf(CORRECTED, "2026-06-10T09:30:00Z");
    expect(before.map((row) => row.object)).toEqual(["true"]);
  });

  it("as-of を訂正後にすると、訂正後の判断が返る", () => {
    const after = asOf(CORRECTED, "2026-06-10T10:30:00Z");
    expect(after.map((row) => row.object)).toEqual(["false"]);
  });

  it("valid-at を戻しても、今の知識で答える（訂正を反映済み）", () => {
    // 現在の取引時刻で、09:30 時点について問う → 訂正後の "false" が返る
    const slice = bitemporalSlice(CORRECTED, {
      transactionTime: "2026-06-10T11:00:00Z",
      validTime: "2026-06-10T09:30:00Z",
    });
    expect(slice.map((row) => row.object)).toEqual(["false"]);
  });

  it("同じ valid-at でも as-of が違えば答えが違う（軸を 1 本にまとめていない証拠）", () => {
    const query = { validTime: "2026-06-10T09:30:00Z" };
    const past = bitemporalSlice(CORRECTED, {
      ...query,
      transactionTime: "2026-06-10T09:30:00Z",
    });
    const present = bitemporalSlice(CORRECTED, {
      ...query,
      transactionTime: "2026-06-10T11:00:00Z",
    });
    expect(past.map((row) => row.object)).toEqual(["true"]);
    expect(present.map((row) => row.object)).toEqual(["false"]);
  });

  it("valid-at は有効区間で絞る", () => {
    const rows = [
      belief({ assertionId: "x", validFrom: "2026-06-10T09:00:00Z", validTo: "2026-06-10T09:30:00Z" }),
      belief({ assertionId: "y", validFrom: "2026-06-10T09:30:00Z", validTo: null }),
    ];
    expect(validAt(rows, "2026-06-10T09:15:00Z").map((r) => r.assertionId)).toEqual(["x"]);
    expect(validAt(rows, "2026-06-10T09:30:00Z").map((r) => r.assertionId)).toEqual(["y"]);
  });
});

describe("transactionTicks", () => {
  it("信念が変わった時刻だけを刻む（等間隔にしない）", () => {
    expect(transactionTicks(CORRECTED)).toEqual([
      "2026-06-10T09:00:00Z",
      "2026-06-10T10:00:00Z",
    ]);
  });

  it("開いている区間の終端は目盛りにならない", () => {
    const ticks = transactionTicks([belief({ assertionId: "a" })]);
    expect(ticks).toEqual(["2026-06-10T09:00:00Z"]);
  });

  it("空の入力は空の目盛り（スライダを無効にできる）", () => {
    expect(transactionTicks([])).toEqual([]);
  });
});

describe("sourceContribution", () => {
  it("件数を数える（確信度を按分しない）", () => {
    const rows = [
      belief({ assertionId: "o1", modality: "OBSERVED", confidence: 0.1 }),
      belief({ assertionId: "o2", modality: "OBSERVED", confidence: 0.99 }),
      belief({ assertionId: "r1", modality: "INFERRED" }),
    ];
    expect(sourceContribution(rows)).toEqual({ observed: 2, recorded: 1, planned: 0 });
  });

  it("証拠が無い源には値が付かない（「計画も見た」と誤表示しない）", () => {
    const rows = [belief({ assertionId: "o1", modality: "OBSERVED" })];
    expect(sourceContribution(rows).planned).toBe(0);
  });

  it("どの源にも証拠が無いことを判定できる", () => {
    expect(hasNoSource(sourceContribution([]))).toBe(true);
    expect(hasNoSource(sourceContribution([belief({ assertionId: "a" })]))).toBe(false);
  });

  it("predicted / normative は三源に数えない", () => {
    const rows = [
      belief({ assertionId: "p", modality: "PREDICTED" }),
      belief({ assertionId: "n", modality: "NORMATIVE" }),
    ];
    expect(hasNoSource(sourceContribution(rows))).toBe(true);
  });
});

describe("excludeHypothetical", () => {
  it("既定で仮説を落とす（不変条件 3）", () => {
    const rows = [
      belief({ assertionId: "real" }),
      belief({ assertionId: "what-if", modality: "HYPOTHETICAL" }),
    ];
    expect(excludeHypothetical(rows).map((r) => r.assertionId)).toEqual(["real"]);
  });
});

describe("formatConfidence", () => {
  it("較正済みの値を百分率で出す", () => {
    expect(formatConfidence(0.873)).toBe("87.3%");
  });

  it("範囲外は丸めて隠さない（較正されていない値が来たことを見せる）", () => {
    expect(formatConfidence(1.5)).toContain("範囲外");
    expect(formatConfidence(-0.1)).toContain("範囲外");
    expect(formatConfidence(Number.NaN)).toContain("範囲外");
  });
});

describe("isSuperseded", () => {
  it("tx が閉じた文を「訂正済み」と判定する（削除ではない）", () => {
    expect(isSuperseded(CORRECTED[0]!)).toBe(true);
    expect(isSuperseded(CORRECTED[1]!)).toBe(false);
  });
});
