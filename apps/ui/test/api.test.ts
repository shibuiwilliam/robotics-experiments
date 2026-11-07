/**
 * API クライアントのテスト（M7-04）。
 *
 * 重点: エンドポイントを既定値で埋めないこと（不変条件 12）、GraphQL の `errors` を
 * 無視しないこと（ABAC で落とされた行を「存在しない行」と読まないため）。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  ConfigurationError,
  fetchBeliefEvidence,
  fetchBeliefs,
  fetchDiscrepancies,
  fetchExpectations,
  fetchProposals,
  resolveApiUrl,
  submitResolution,
} from "../src/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(
  response: { ok?: boolean; status?: number; body: unknown },
): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: response.ok ?? true,
    status: response.status ?? 200,
    json: async () => response.body,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("resolveApiUrl", () => {
  it("環境変数から読む", () => {
    expect(resolveApiUrl({ VITE_SBP_API_URL: "http://localhost:8000" })).toBe(
      "http://localhost:8000",
    );
  });

  it("末尾のスラッシュを落とす（二重スラッシュの URL を作らない）", () => {
    expect(resolveApiUrl({ VITE_SBP_API_URL: "http://localhost:8000/" })).toBe(
      "http://localhost:8000",
    );
  });

  it("未設定なら落とす（既定値で localhost を埋めない）", () => {
    // 既定値を入れると、本番ビルドが黙って開発環境を向く（不変条件 12）
    expect(() => resolveApiUrl({})).toThrow(ConfigurationError);
    expect(() => resolveApiUrl({ VITE_SBP_API_URL: "   " })).toThrow(ConfigurationError);
  });
});

describe("fetchBeliefEvidence", () => {
  const params = {
    entity: "urn:x:P-101",
    fluent: "https://sbp.example/ent#Running",
    start: "2026-11-07T09:00:00Z",
    end: "2026-11-07T10:00:00Z",
  };

  it("寄与の無い点は null のまま返す（0 に丸めない）", async () => {
    stubFetch({
      body: {
        data: {
          beliefEvidence: {
            ...params,
            points: [
              { assertionId: "a", at: params.start, probability: 0.8, origin: "update", contribution: null },
            ],
            planned: [],
          },
        },
      },
    });
    const evidence = await fetchBeliefEvidence("http://api", params);
    expect(evidence.points[0]?.contribution).toBeNull();
  });

  it("鍵（実体・フルーエント・区間）を変数で送る", async () => {
    const fetchMock = stubFetch({
      body: { data: { beliefEvidence: { ...params, points: [], planned: [] } } },
    });
    await fetchBeliefEvidence("http://api", params);
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body)) as {
      variables: Record<string, unknown>;
    };
    expect(body.variables).toEqual(params);
  });

  it("errors があれば落とす（認可で落とされた並置を「証拠なし」と読ませない）", async () => {
    stubFetch({
      body: { data: null, errors: [{ message: "ABAC が設定されていない" }] },
    });
    await expect(fetchBeliefEvidence("http://api", params)).rejects.toThrow(ApiError);
  });
});

describe("fetchBeliefs", () => {
  it("信念を返す", async () => {
    stubFetch({ body: { data: { beliefs: [{ assertionId: "a" }] } } });
    const rows = await fetchBeliefs("http://api", { subject: "P-101" });
    expect(rows.length).toBe(1);
  });

  it("既定の様相は inferred（計画と観測と推定を混ぜない）", async () => {
    const fetchMock = stubFetch({ body: { data: { beliefs: [] } } });
    await fetchBeliefs("http://api", {});
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body)) as {
      variables: Record<string, unknown>;
    };
    expect(body.variables["modality"]).toBe("INFERRED");
  });

  it("data があっても errors があれば落とす", async () => {
    // GraphQL は部分的な成功を返せる。黙って data を使うと、認可で落とされた行が
    // 「存在しない行」として表示される
    stubFetch({
      body: { data: { beliefs: [] }, errors: [{ message: "ABAC が設定されていない" }] },
    });
    await expect(fetchBeliefs("http://api", {})).rejects.toThrow(ApiError);
  });

  it("HTTP が失敗したら落とす（空の成功として描かない）", async () => {
    stubFetch({ ok: false, status: 503, body: {} });
    await expect(fetchBeliefs("http://api", {})).rejects.toThrow(/503/);
  });

  it("data が無い応答も落とす", async () => {
    stubFetch({ body: {} });
    await expect(fetchBeliefs("http://api", {})).rejects.toThrow(ApiError);
  });
});

describe("submitResolution", () => {
  const resolution = {
    decisionId: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    discrepancyId: "0f8fad5b-d9cb-469f-a165-70867728950e",
    outcome: "physical_correct" as const,
    note: "MES の完了打刻が実際の停止より 20 分遅れていた",
    resolvedBy: "urn:x:person/op1",
  };

  it("commands 経由で投入する（GraphQL の mutation ではない）", async () => {
    const fetchMock = stubFetch({ body: { detail: "受け付けた" } });
    const result = await submitResolution("http://api", resolution);
    expect(result.accepted).toBe(true);
    expect(String(fetchMock.mock.calls[0]![0])).toContain("/commands/resolve_discrepancy");
  });

  it("API の契約（snake_case）で送る", async () => {
    // **以前は camelCase を送っており、API の extra="forbid" で弾かれる形だった**（所見 83）。
    // UI の型が API の契約と一致していることを、送信の形で固定する
    const fetchMock = stubFetch({ body: { detail: "受け付けた" } });
    await submitResolution("http://api", resolution);
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body)) as Record<
      string,
      unknown
    >;
    expect(Object.keys(body).sort()).toEqual([
      "decision_id",
      "discrepancy_id",
      "note",
      "outcome",
      "resolved_by",
    ]);
    expect(body["outcome"]).toBe("physical_correct");
  });

  it("拒否されたら理由を返す（黙って成功にしない）", async () => {
    stubFetch({ ok: false, status: 409, body: { detail: "承認が必要" } });
    const result = await submitResolution("http://api", resolution);
    expect(result.accepted).toBe(false);
    expect(result.detail).toBe("承認が必要");
  });

  it("理由が無い応答でも status が分かる", async () => {
    stubFetch({ ok: false, status: 500, body: {} });
    const result = await submitResolution("http://api", resolution);
    expect(result.detail).toContain("500");
  });
});


describe("fetchDiscrepancies", () => {
  it("GraphQL の資源を読む（ストリームではない）", async () => {
    // **以前はイベントストリームを読んでいた**（`/stream/{topic}`）。しかし起票イベントは
    // 通知であって不一致そのものではなく、`payload` が Discrepancy である前提は成立しない
    // （所見 62 / 83）
    const fetchMock = stubFetch({ body: { data: { discrepancies: [] } } });
    await fetchDiscrepancies("http://api");
    expect(String(fetchMock.mock.calls[0]![0])).toContain("/graphql");
    expect(String(fetchMock.mock.calls[0]![0])).not.toContain("/stream/");
  });

  it("既定で終端状態を除く（履歴でキューが埋まらない）", async () => {
    const fetchMock = stubFetch({ body: { data: { discrepancies: [] } } });
    await fetchDiscrepancies("http://api");
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body)) as {
      variables: Record<string, unknown>;
    };
    expect(body.variables["includeTerminal"]).toBe(false);
  });

  it("類型で絞れる（どの類型が増えたかを見る）", async () => {
    const fetchMock = stubFetch({ body: { data: { discrepancies: [] } } });
    await fetchDiscrepancies("http://api", { kind: 2 });
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body)) as {
      variables: Record<string, unknown>;
    };
    expect(body.variables["kind"]).toBe(2);
  });

  it("errors があれば落とす（認可で落とされた行を空として描かない）", async () => {
    stubFetch({
      body: {
        data: { discrepancies: [] },
        errors: [{ message: "ABAC が設定されていない" }],
      },
    });
    await expect(fetchDiscrepancies("http://api")).rejects.toThrow(ApiError);
  });

  it("不一致を返す", async () => {
    stubFetch({
      body: {
        data: {
          discrepancies: [
            {
              discrepancyId: "0f8fad5b-d9cb-469f-a165-70867728950e",
              kind: 2,
              status: "Open",
              subjects: ["urn:x:P-101"],
              severity: 0.77,
              detectedAt: "2026-06-10T09:00:00Z",
              detector: "kind2",
              assertions: [],
              evidence: ["urn:sbp:record:A"],
              assigneeRole: null,
              ontologyVersion: "0.5.0",
            },
          ],
        },
      },
    });
    const rows = await fetchDiscrepancies("http://api");
    expect(rows.length).toBe(1);
    expect(rows[0]!.kind).toBe(2);
    expect(rows[0]!.status).toBe("Open");
  });
});


describe("fetchExpectations", () => {
  it("GraphQL の資源を読む（ストリームではない。M8-18）", async () => {
    // 期待の全体はイベントの封筒に入らない（expected_effects / verification / history を
    // 入れる場所が無い。所見 62 / 86）
    const fetchMock = stubFetch({ body: { data: { expectations: [] } } });
    await fetchExpectations("http://api");
    const url = String(fetchMock.mock.calls[0]![0]);
    expect(url).toContain("/graphql");
    expect(url).not.toContain("/stream/");
  });

  it("既定で終端状態を除く（履歴でボードが埋まらない）", async () => {
    const fetchMock = stubFetch({ body: { data: { expectations: [] } } });
    await fetchExpectations("http://api");
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body)) as {
      variables: Record<string, unknown>;
    };
    expect(body.variables["includeTerminal"]).toBe(false);
  });

  it("期限が null の期待も返せる（Created は効果をまだ持たない）", async () => {
    stubFetch({
      body: {
        data: {
          expectations: [
            {
              expectationId: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
              status: "Created",
              actionType: "ent:CreateEmergencyInspection",
              target: "urn:x:P-101",
              createdAt: "2026-06-10T09:00:00Z",
              deadline: null,
              symbolicVerdict: null,
              rolloutVerdict: null,
              method: null,
              effectCount: 0,
              ontologyVersion: "0.5.0",
            },
          ],
        },
      },
    });
    const rows = await fetchExpectations("http://api");
    expect(rows[0]!.deadline).toBeNull();
    expect(rows[0]!.status).toBe("Created");
  });
});

describe("fetchProposals", () => {
  const proposal = {
    proposalId: "9a8b7c6d-5e4f-4321-9876-543210fedcba",
    name: "VibrationAnomaly",
    definition: "既存の Faulted では説明できない振動の増大が停止の前段に現れる",
    superclass: "Fluent",
    status: "proposed",
    rejectedAlternatives: ["Faulted の閾値調整"],
    supportCount: 548,
    coverage: 0.0,
    signalCount: 2,
    proposedAt: "2026-06-10T09:00:00Z",
    curationNote: "",
  };

  it("GraphQL の proposals を読む（M8-19）", async () => {
    // **以前は存在しないトピックを読んでいた**（`central.reasoning.concept`。所見 85）
    const fetchMock = stubFetch({ body: { data: { proposals: [proposal] } } });
    const rows = await fetchProposals("http://api");

    expect(rows).toHaveLength(1);
    expect(rows[0]!.name).toBe("VibrationAnomaly");
    expect(String(fetchMock.mock.calls[0]![0])).toBe("http://api/graphql");
  });

  it("既定で採否が入ったものを除く", async () => {
    const fetchMock = stubFetch({ body: { data: { proposals: [] } } });
    await fetchProposals("http://api");
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1].body)) as {
      variables: Record<string, unknown>;
    };
    expect(body.variables["includeCurated"]).toBe(false);
  });

  it("読めなければ空を返さず失敗する（所見 32）", async () => {
    // 空を返すと「提案が無い」と読まれ、発見ループが動いていないことと区別できない
    stubFetch({ body: { errors: [{ message: "概念提案の読み出しが設定されていない" }] } });
    await expect(fetchProposals("http://api")).rejects.toThrow(ApiError);
  });
});
