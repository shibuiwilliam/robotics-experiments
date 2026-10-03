/**
 * API クライアント（GraphQL は `fetch` に POST するだけ、ADR-0008）。
 *
 * **エンドポイントをソースに書かない**（不変条件 12）。`import.meta.env.VITE_SBP_API_URL`
 * から読み、未設定なら起動時に落とす——既定値で `localhost` を埋めると、本番ビルドが
 * 黙って開発環境を向く。
 *
 * **mutation を呼ばない。** API 層に mutation が無く、副作用は `/commands/*` を通る
 * （設計書 §9.2 の承認フローを迂回させない）。
 */

import type {
  Belief,
  ConceptProposal,
  Discrepancy,
  Expectation,
  Modality,
  Resolution,
} from "./types";

/** 設定の誤り（起動時に落とす）。 */
export class ConfigurationError extends Error {}

/** API の返したエラー。 */
export class ApiError extends Error {}

/** エンドポイントを解決する。 */
export function resolveApiUrl(
  env: Record<string, string | undefined>,
): string {
  const url = env["VITE_SBP_API_URL"];
  if (url === undefined || url.trim() === "") {
    throw new ConfigurationError(
      "VITE_SBP_API_URL が未設定。既定値を埋めないのは、本番ビルドが黙って開発環境を向くのを防ぐため（不変条件 12）",
    );
  }
  return url.replace(/\/$/, "");
}

/** GraphQL の応答。 */
interface GraphQLResponse<T> {
  readonly data?: T;
  readonly errors?: readonly { readonly message: string }[];
}

/**
 * GraphQL を 1 回呼ぶ。
 *
 * **`errors` を無視しない。** GraphQL は部分的な成功を返せるので、`data` があっても
 * `errors` が付いていることがある。黙って `data` を使うと、認可で落とされた行が
 * 「存在しない行」として表示される（ABAC が効いているのに効いていないように見える）。
 */
async function graphql<T>(
  apiUrl: string,
  query: string,
  variables: Record<string, unknown> = {},
): Promise<T> {
  const response = await fetch(`${apiUrl}/graphql`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ query, variables }),
  });
  if (!response.ok) {
    throw new ApiError(`GraphQL が ${response.status} を返した`);
  }
  const payload = (await response.json()) as GraphQLResponse<T>;
  if (payload.errors !== undefined && payload.errors.length > 0) {
    throw new ApiError(payload.errors.map((error) => error.message).join("; "));
  }
  if (payload.data === undefined) {
    throw new ApiError("GraphQL の応答に data が無い");
  }
  return payload.data;
}

const BELIEF_FIELDS = `
  assertionId subject predicate object
  validFrom validTo txFrom txTo
  modality confidence provenance ontologyVersion
`;

/** 信念を二時制・様相・確信度で絞って読む。 */
export async function fetchBeliefs(
  apiUrl: string,
  params: {
    subject?: string;
    asOf?: string;
    validAt?: string;
    modality?: Modality;
    minConfidence?: number;
  },
): Promise<readonly Belief[]> {
  const data = await graphql<{ beliefs: Belief[] }>(
    apiUrl,
    `query Beliefs($subject: String, $asOf: DateTime, $validAt: DateTime, $modality: Modality!, $minConfidence: Float!) {
      beliefs(subject: $subject, asOf: $asOf, validAt: $validAt, modality: $modality, minConfidence: $minConfidence) {
        ${BELIEF_FIELDS}
      }
    }`,
    {
      subject: params.subject ?? null,
      asOf: params.asOf ?? null,
      validAt: params.validAt ?? null,
      modality: params.modality ?? "INFERRED",
      minConfidence: params.minConfidence ?? 0,
    },
  );
  return data.beliefs;
}

/**
 * 不一致キューを読む（イベントストリームから）。
 *
 * GraphQL ではなく `/stream/{topic}` を読むのは、不一致がイベントであって信念ではない
 * からである（`bridge` はイベントを出すだけ、という層の規約がそのまま API の形に出る）。
 */
export async function fetchDiscrepancies(
  apiUrl: string,
  topic = "central.reasoning.discrepancy",
  limit = 50,
): Promise<readonly Discrepancy[]> {
  const response = await fetch(
    `${apiUrl}/stream/${encodeURIComponent(topic)}?limit=${limit}`,
  );
  if (!response.ok) {
    throw new ApiError(`ストリームが ${response.status} を返した`);
  }
  const payload = (await response.json()) as {
    events: { payload: Discrepancy }[];
  };
  return payload.events.map((event) => event.payload);
}

/** 期待ボードを読む。 */
export async function fetchExpectations(
  apiUrl: string,
  limit = 50,
): Promise<readonly Expectation[]> {
  const response = await fetch(
    `${apiUrl}/stream/central.reasoning.expectation?limit=${limit}`,
  );
  if (!response.ok) {
    throw new ApiError(`ストリームが ${response.status} を返した`);
  }
  const payload = (await response.json()) as {
    events: { payload: Expectation }[];
  };
  return payload.events.map((event) => event.payload);
}

/** 概念提案を読む。 */
export async function fetchProposals(
  apiUrl: string,
  limit = 50,
): Promise<readonly ConceptProposal[]> {
  const response = await fetch(
    `${apiUrl}/stream/central.reasoning.concept?limit=${limit}`,
  );
  if (!response.ok) {
    throw new ApiError(`ストリームが ${response.status} を返した`);
  }
  const payload = (await response.json()) as {
    events: { payload: ConceptProposal }[];
  };
  return payload.events.map((event) => event.payload);
}

/**
 * 解決を投入する（`/commands/*` 経由。**GraphQL の mutation ではない**）。
 *
 * 副作用の経路を読み取りと分けておくのは、承認フロー（設計書 §9.2）の迂回を容易にしない
 * ためである。UI が信念を直接書く経路は存在しない（ADR-0008）。
 */
export async function submitResolution(
  apiUrl: string,
  resolution: Resolution,
): Promise<{ readonly accepted: boolean; readonly detail: string }> {
  const response = await fetch(`${apiUrl}/commands/resolve_discrepancy`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(resolution),
  });
  const payload = (await response.json()) as Record<string, unknown>;
  if (!response.ok) {
    return {
      accepted: false,
      detail: String(payload["detail"] ?? `HTTP ${response.status}`),
    };
  }
  return { accepted: true, detail: String(payload["detail"] ?? "受け付けた") };
}

/** 概念提案の採否を投入する。 */
export async function submitCuration(
  apiUrl: string,
  decision: {
    proposalId: string;
    accepted: boolean;
    reason: string;
    decidedBy: string;
  },
): Promise<{ readonly accepted: boolean; readonly detail: string }> {
  const response = await fetch(`${apiUrl}/commands/curate_concept`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(decision),
  });
  const payload = (await response.json()) as Record<string, unknown>;
  if (!response.ok) {
    return {
      accepted: false,
      detail: String(payload["detail"] ?? `HTTP ${response.status}`),
    };
  }
  return { accepted: true, detail: String(payload["detail"] ?? "受け付けた") };
}
