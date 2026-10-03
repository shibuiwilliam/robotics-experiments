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
  fetchBeliefs,
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
    discrepancyId: "d-1",
    kind: "RECORD_WRONG" as const,
    reason: "MES の完了打刻が実際の停止より 20 分遅れていた",
    resolvedBy: "urn:x:person/op1",
  };

  it("commands 経由で投入する（GraphQL の mutation ではない）", async () => {
    const fetchMock = stubFetch({ body: { detail: "受け付けた" } });
    const result = await submitResolution("http://api", resolution);
    expect(result.accepted).toBe(true);
    expect(String(fetchMock.mock.calls[0]![0])).toContain("/commands/resolve_discrepancy");
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
