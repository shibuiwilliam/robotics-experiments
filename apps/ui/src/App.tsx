/**
 * UI の外枠（M7-04）。4 ビューをタブで切り替える。
 *
 * 状態管理ライブラリを入れていない（ADR-0008）。4 ビューは互いに状態を共有せず、各ビューが
 * 自分のクエリ結果を持つだけで足りる。
 *
 * **開発用の要求者を固定で送っている。** ABAC（M7-06）は要求者を受け取る形まで作ったが、
 * UI の認証方式は未決（ADR-0008 の未決）。本番の認証を決めるまでこの UI を公開しない。
 */

import { useCallback, useEffect, useState } from "react";

import {
  fetchBeliefs,
  fetchDiscrepancies,
  fetchExpectations,
  fetchProposals,
  resolveApiUrl,
  submitCuration,
  submitResolution,
} from "./lib/api";
import type {
  Belief,
  ConceptProposal,
  Discrepancy,
  Expectation,
  Resolution,
} from "./lib/types";
import { BeliefView } from "./views/BeliefView";
import { ConceptCuration } from "./views/ConceptCuration";
import { DiscrepancyQueue } from "./views/DiscrepancyQueue";
import { ExpectationBoard } from "./views/ExpectationBoard";

type Tab = "queue" | "beliefs" | "expectations" | "concepts";

const TAB_LABEL: Readonly<Record<Tab, string>> = {
  queue: "不一致キュー",
  beliefs: "信念",
  expectations: "期待",
  concepts: "概念",
};

/** 開発用の既定の主語。本番では選択 UI から渡す。 */
const DEFAULT_SUBJECT = "P-101";

export function App(): React.JSX.Element {
  const [tab, setTab] = useState<Tab>("queue");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [discrepancies, setDiscrepancies] = useState<readonly Discrepancy[]>([]);
  const [beliefs, setBeliefs] = useState<readonly Belief[]>([]);
  const [expectations, setExpectations] = useState<readonly Expectation[]>([]);
  const [proposals, setProposals] = useState<readonly ConceptProposal[]>([]);
  // 読めなかったときの理由。**空と「読めない」を区別して画面に出す**（所見 32）
  const [proposalError, setProposalError] = useState<string | null>(null);
  const [subject] = useState(DEFAULT_SUBJECT);

  let apiUrl = "";
  let configError: string | null = null;
  try {
    // **拠点は要らなくなった。** すべての読み出しが GraphQL（API が拠点で絞る）になり、
    // UI がトピック名を組む必要が無い（M8-18 で期待ボードも資源に移した）
    apiUrl = resolveApiUrl(import.meta.env as Record<string, string | undefined>);
  } catch (caught) {
    configError = caught instanceof Error ? caught.message : String(caught);
  }

  const load = useCallback(async () => {
    if (apiUrl === "") return;
    try {
      // **概念提案も GraphQL になった**（M8-19）。それでも別に扱うのは残す——発見ループの
      // 周期実行が未配線（M8-09）で表が空のままなので、**空と「読めない」を画面で区別する**
      // 必要がある（所見 32）
      const [queue, board, rows] = await Promise.all([
        fetchDiscrepancies(apiUrl),
        fetchExpectations(apiUrl),
        fetchBeliefs(apiUrl, { subject }),
      ]);
      setDiscrepancies(queue);
      setExpectations(board);
      setBeliefs(rows);
      try {
        setProposals(await fetchProposals(apiUrl));
        setProposalError(null);
      } catch (caught) {
        // 空にしない（「提案が無い」と読まれる）。理由を画面に出す
        setProposals([]);
        setProposalError(
          caught instanceof Error ? caught.message : String(caught),
        );
      }
      setError(null);
    } catch (caught) {
      // 失敗を空の成功として描かない。空のキューは「不一致が無い」と読まれる
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, [apiUrl, subject]);

  useEffect(() => {
    void load();
  }, [load]);

  const onResolve = useCallback(
    async (resolution: Resolution) => {
      const result = await submitResolution(apiUrl, resolution);
      setNotice(result.detail);
      if (result.accepted) await load();
    },
    [apiUrl, load],
  );

  const onDecide = useCallback(
    async (decision: {
      proposalId: string;
      accepted: boolean;
      reason: string;
      decidedBy: string;
    }) => {
      // 冪等の鍵はここで作る（ADR-0012）。**概念 curation のフォームは再送しない**ので
      // 送信のたびに新しい値でよい——再送する形にするなら、解決と同じくフォーム側で持つ
      const result = await submitCuration(apiUrl, {
        decisionId: crypto.randomUUID(),
        ...decision,
      });
      setNotice(result.detail);
      if (result.accepted) await load();
    },
    [apiUrl, load],
  );

  if (configError !== null) {
    return (
      <main className="fatal">
        <h1>設定が足りない</h1>
        <p>{configError}</p>
      </main>
    );
  }

  const now = new Date().toISOString();

  return (
    <main>
      <header>
        <h1>SBP</h1>
        <nav>
          {(Object.keys(TAB_LABEL) as Tab[]).map((name) => (
            <button
              key={name}
              type="button"
              className={name === tab ? "tab active" : "tab"}
              onClick={() => setTab(name)}
            >
              {TAB_LABEL[name]}
            </button>
          ))}
        </nav>
        <button type="button" className="reload" onClick={() => void load()}>
          再読み込み
        </button>
      </header>

      {error !== null ? (
        <p className="error" role="alert">
          読み込みに失敗した: {error}
          <span className="error-note">
            表示は最後に成功した内容である。<strong>空のキューを「不一致なし」と読まないこと。</strong>
          </span>
        </p>
      ) : null}
      {notice !== null ? (
        <p className="notice" role="status">
          {notice}
        </p>
      ) : null}

      {tab === "queue" ? (
        <DiscrepancyQueue
          discrepancies={discrepancies}
          now={now}
          resolvedBy="https://sbp.example/id/person/dev"
          onResolve={(resolution) => void onResolve(resolution)}
        />
      ) : null}
      {tab === "beliefs" ? <BeliefView beliefs={beliefs} subject={subject} /> : null}
      {tab === "expectations" ? (
        <ExpectationBoard expectations={expectations} now={now} />
      ) : null}
      {tab === "concepts" ? (
        <>
          {proposalError === null ? null : (
            <p className="unwired" role="status">
              <strong>概念提案を読めなかった</strong>——{proposalError}
            </p>
          )}
          <ConceptCuration
            proposals={proposals}
            decidedBy="https://sbp.example/id/person/dev"
            onDecide={(decision) => void onDecide(decision)}
          />
        </>
      ) : null}
    </main>
  );
}
