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
  BeliefEvidence,
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

const DISCREPANCY_FIELDS = `
  discrepancyId kind status subjects severity
  detectedAt detector assertions evidence assigneeRole ontologyVersion
  intervalStart intervalEnd
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

const EXPECTATION_FIELDS = `
  expectationId status actionType target createdAt deadline
  symbolicVerdict rolloutVerdict method effectCount ontologyVersion
`;

/**
 * 期待ボードを読む（GraphQL の `expectations`。M8-18）。
 *
 * **以前はイベントストリームを読んでいた**（`<site>.reasoning.expectation`）。しかし期待の
 * 全体はイベントの封筒に入らない（`expected_effects` / `verification` / `history` を入れる
 * 場所が無い。所見 62 / 86）ので、不一致と同じく資源を引く形にした。
 *
 * **既定で終端状態を除く。** 検証済みの履歴でボードが埋まらないようにする。
 */
export async function fetchExpectations(
  apiUrl: string,
  params: {
    target?: string;
    includeTerminal?: boolean;
    limit?: number;
  } = {},
): Promise<readonly Expectation[]> {
  const data = await graphql<{ expectations: Expectation[] }>(
    apiUrl,
    `query Expectations($target: String, $includeTerminal: Boolean!, $limit: Int!) {
      expectations(target: $target, includeTerminal: $includeTerminal, limit: $limit) {
        ${EXPECTATION_FIELDS}
      }
    }`,
    {
      target: params.target ?? null,
      includeTerminal: params.includeTerminal ?? false,
      limit: params.limit ?? 50,
    },
  );
  return data.expectations;
}

const PROPOSAL_FIELDS = `
  proposalId name definition superclass status rejectedAlternatives
  supportCount coverage signalCount proposedAt curationNote
`;

/**
 * curation 待ちの概念提案を読む（GraphQL の `proposals`。M8-19）。
 *
 * **以前は存在しないトピックを読んでいた**（`central.reasoning.concept`。所見 85）。トピックも
 * API 資源も無く、型も backend と別物だった。
 *
 * **支持の多い順に返る。** 偶然のクラスタと区別できない提案（標本が少ない）を先頭に置くと、
 * 人の判断の時間が弱い提案に使われる（設計書 §11.2）。
 *
 * **既定で採否が入ったものを除く。** 判断済みの履歴で画面が埋まらないようにする。
 */
export async function fetchProposals(
  apiUrl: string,
  params: { includeCurated?: boolean; limit?: number } = {},
): Promise<readonly ConceptProposal[]> {
  const data = await graphql<{ proposals: ConceptProposal[] }>(
    apiUrl,
    `query Proposals($includeCurated: Boolean!, $limit: Int!) {
      proposals(includeCurated: $includeCurated, limit: $limit) {
        ${PROPOSAL_FIELDS}
      }
    }`,
    {
      includeCurated: params.includeCurated ?? false,
      limit: params.limit ?? 50,
    },
  );
  return data.proposals;
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

/**
 * 実体 × フルーエント × 区間の証拠の三源並置を読む（GraphQL の `beliefEvidence`。ADR-0020）。
 *
 * **寄与が無い点は `contribution: null` のまま返る**（0 に丸めない）。画面は「記録されていない」と
 * 「寄与なし」を区別して出す。
 */
export async function fetchBeliefEvidence(
  apiUrl: string,
  params: { entity: string; fluent: string; start: string; end: string },
): Promise<BeliefEvidence> {
  const data = await graphql<{ beliefEvidence: BeliefEvidence }>(
    apiUrl,
    `query BeliefEvidence($entity: String!, $fluent: String!, $start: DateTime!, $end: DateTime!) {
      beliefEvidence(entity: $entity, fluent: $fluent, start: $start, end: $end) {
        entity fluent start end
        points {
          assertionId at probability origin
          contribution { observed record plan dominant prior sourceEventId excludedRecords }
        }
        planned { start end adherence }
      }
    }`,
    params,
  );
  return data.beliefEvidence;
}
