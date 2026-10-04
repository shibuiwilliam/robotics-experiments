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

/**
 * 拠点を解決する（イベントログのトピック名に要る。M8-16）。
 *
 * **既定値を埋めない。** トピック名は `<site>.<layer>.<class>` で、拠点を間違えると
 * **存在しないトピックを読んで空を返す**——「イベントが無い」と「トピックが無い」が
 * 区別できない（所見 85）。
 */
export function resolveSiteId(
  env: Record<string, string | undefined>,
): string {
  const site = env["VITE_SBP_SITE_ID"];
  if (site === undefined || site.trim() === "") {
    throw new ConfigurationError(
      "VITE_SBP_SITE_ID が未設定。トピック名は <site>.<layer>.<class> で、拠点を間違えると存在しないトピックを読んで空を返す（所見 85）",
    );
  }
  return site.trim();
}

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

const DISCREPANCY_FIELDS = `
  discrepancyId kind status subjects severity
  detectedAt detector assertions evidence assigneeRole ontologyVersion
`;

/**
 * 不一致キューを読む（GraphQL の `discrepancies`。ADR-0013、M8-16）。
 *
 * **以前はイベントストリームを読んでいた**（`/stream/{topic}`）。しかし起票イベントは
 * **通知**であって不一致そのものではなく、`EventEnvelope` に全体を入れる場所が無い
 * （所見 62）。「各イベントの `payload` が `Discrepancy` である」という前提は成立していな
 * かったので、資源を引く形に変えた。
 *
 * **既定で終端状態を除く。** 解決済み・抑制済みを毎回返すと、キューの上位が履歴で埋まる。
 */
export async function fetchDiscrepancies(
  apiUrl: string,
  params: {
    kind?: number;
    includeTerminal?: boolean;
    limit?: number;
  } = {},
): Promise<readonly Discrepancy[]> {
  const data = await graphql<{ discrepancies: Discrepancy[] }>(
    apiUrl,
    `query Discrepancies($kind: Int, $includeTerminal: Boolean!, $limit: Int!) {
      discrepancies(kind: $kind, includeTerminal: $includeTerminal, limit: $limit) {
        ${DISCREPANCY_FIELDS}
      }
    }`,
    {
      kind: params.kind ?? null,
      includeTerminal: params.includeTerminal ?? false,
      limit: params.limit ?? 50,
    },
  );
  return data.discrepancies;
}

/**
 * 期待ボードを読む。
 *
 * **トピックは拠点ごとである**（`<site>.reasoning.expectation`）。以前は
 * `central.reasoning.expectation` を読んでいたが、**そのトピックは存在しない**
 * （`scripts/topics.py` に無い。所見 85）——存在しないトピックは空を返すので、
 * 「期待が無い」と読めてしまっていた。
 *
 * **型は API と一致していない**（所見 86）。`Expectation` の形はイベントの封筒に入らず、
 * 永続化と API 資源が未実装である（M8-18）。ここは経路だけを正しくしてある。
 */
export async function fetchExpectations(
  apiUrl: string,
  siteId: string,
  limit = 50,
): Promise<readonly Expectation[]> {
  const response = await fetch(
    `${apiUrl}/stream/${encodeURIComponent(`${siteId}.reasoning.expectation`)}?limit=${limit}`,
  );
  if (!response.ok) {
    throw new ApiError(`ストリームが ${response.status} を返した`);
  }
  const payload = (await response.json()) as {
    events: { payload: Expectation }[];
  };
  return payload.events.map((event) => event.payload);
}

/**
 * 概念提案を読む。**まだ読む先が無い。**
 *
 * 以前は `central.reasoning.concept` を読んでいたが、**そのトピックは存在しない**
 * （`scripts/topics.py` にも無く、提案を出す周期ジョブも未実装。所見 85）。
 *
 * **空の配列を返さない。** 返すと「提案が無い」と読まれ、発見ループが動いていないことと
 * 区別できない（所見 32）。配線されるまで明示的に失敗させる——永続化と API 資源は M8-19。
 */
export async function fetchProposals(
  _apiUrl: string,
  _limit = 50,
): Promise<readonly ConceptProposal[]> {
  throw new ApiError(
    "概念提案の読み出しは未配線（トピックも API 資源も無い。M8-19）。空を返さないのは、提案が無いことと発見ループが動いていないことを区別するため",
  );
}

/**
 * 解決を投入する（`/commands/*` 経由。**GraphQL の mutation ではない**）。
 *
 * 副作用の経路を読み取りと分けておくのは、承認フロー（設計書 §9.2）の迂回を容易にしない
 * ためである。UI が信念を直接書く経路は存在しない（ADR-0008）。
 *
 * **受領は「適用された」ではない**（ADR-0012）。判断は作業キューに積まれ、`learning-worker`
 * が適用する。適用の結果（観測要求が何件積まれたか、何が記録だけで止まっているか）を読む
 * 経路は未実装である。
 *
 * **引数名は API の契約そのもの**（snake_case）。`extra="forbid"` なので、camelCase を送ると
 * 400 で弾かれる——以前はそれで弾かれる形のまま出荷されていた（所見 83）。
 */
export async function submitResolution(
  apiUrl: string,
  resolution: Resolution,
): Promise<{ readonly accepted: boolean; readonly detail: string }> {
  const response = await fetch(`${apiUrl}/commands/resolve_discrepancy`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      decision_id: resolution.decisionId,
      discrepancy_id: resolution.discrepancyId,
      outcome: resolution.outcome,
      note: resolution.note,
      resolved_by: resolution.resolvedBy,
    }),
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
    /** 冪等の鍵（再送では同じ値を送る。ADR-0012）。 */
    decisionId: string;
    proposalId: string;
    accepted: boolean;
    reason: string;
    decidedBy: string;
  },
): Promise<{ readonly accepted: boolean; readonly detail: string }> {
  const response = await fetch(`${apiUrl}/commands/curate_concept`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    // API の契約は snake_case（`extra="forbid"` なので camelCase は弾かれる）
    body: JSON.stringify({
      decision_id: decision.decisionId,
      proposal_id: decision.proposalId,
      accepted: decision.accepted,
      reason: decision.reason,
      decided_by: decision.decidedBy,
    }),
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
