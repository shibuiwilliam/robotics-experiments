/**
 * 概念 curation（設計書 §11.2、M7-04）。
 *
 * **却下した代替案が無い提案は採択ボタンを押せない。** 「この新概念が必要だ」という主張は、
 * 既存の概念で説明できないことを示して初めて意味を持つ。代替案の検討が無い提案は、単に
 * 「クラスタが見つかった」と言っているだけである（`checkProposal`）。
 *
 * **却下にも理由を書かせる。** 却下理由が残らないと、同じ提案が次の発見ループで再び上がり、
 * 同じ検討を繰り返す。
 */

import { useState } from "react";

import { checkProposal } from "../lib/expectations";
import type { ConceptProposal } from "../lib/types";

interface Props {
  readonly proposals: readonly ConceptProposal[];
  readonly decidedBy: string;
  readonly onDecide: (decision: {
    proposalId: string;
    accepted: boolean;
    reason: string;
    decidedBy: string;
  }) => void;
}

export function ConceptCuration({
  proposals,
  decidedBy,
  onDecide,
}: Props): React.JSX.Element {
  return (
    <section aria-label="概念 curation">
      <h2>
        概念提案 <span className="count">{proposals.length}</span>
      </h2>
      {proposals.length === 0 ? (
        <p className="empty">
          検討中の提案は無い。概念発見は被覆率の低いクラスタから始まるので、
          <strong>提案が無いことは「語彙が足りている」ことを意味しない</strong>——
          発見ループが回っているかを確認する。
        </p>
      ) : (
        <ul className="proposals">
          {proposals.map((proposal) => (
            <ProposalCard
              key={proposal.proposalId}
              proposal={proposal}
              decidedBy={decidedBy}
              onDecide={onDecide}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

interface CardProps {
  readonly proposal: ConceptProposal;
  readonly decidedBy: string;
  readonly onDecide: Props["onDecide"];
}

function ProposalCard({
  proposal,
  decidedBy,
  onDecide,
}: CardProps): React.JSX.Element {
  const [reason, setReason] = useState("");
  const check = checkProposal(proposal);
  const reasonGiven = reason.trim().length >= 10;

  return (
    <li className="proposal">
      <h3>{proposal.name}</h3>
      <p className="rationale">{proposal.definition}</p>
      <p className="stats">
        支持 {proposal.supportCount} 件 · 既存概念での被覆率{" "}
        {(proposal.coverage * 100).toFixed(1)}% · 根拠 {proposal.signalCount} 件
        {proposal.superclass === "" ? null : ` · 上位 ${proposal.superclass}`}
      </p>

      <details open={proposal.rejectedAlternatives.length > 0}>
        <summary>
          却下した代替案 ({proposal.rejectedAlternatives.length})
        </summary>
        {proposal.rejectedAlternatives.length === 0 ? (
          <p className="none">
            無い。<strong>これが無いと採択できない</strong>——既存概念で説明できないことを
            示していないため。
          </p>
        ) : (
          <ul>
            {proposal.rejectedAlternatives.map((alternative) => (
              <li key={alternative}>{alternative}</li>
            ))}
          </ul>
        )}
      </details>

      {check.problems.length > 0 ? (
        <ul className="problems">
          {check.problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}

      <label className="reason">
        判断の理由（採択・却下の両方で必須）
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={2}
          placeholder="なぜ採択するか / なぜ却下するか"
        />
      </label>

      <div className="decide">
        <button
          type="button"
          disabled={!check.ready || !reasonGiven}
          onClick={() =>
            onDecide({
              proposalId: proposal.proposalId,
              accepted: true,
              reason,
              decidedBy,
            })
          }
        >
          採択（オントロジー変更の手順に進む）
        </button>
        <button
          type="button"
          className="reject"
          disabled={!reasonGiven}
          onClick={() =>
            onDecide({
              proposalId: proposal.proposalId,
              accepted: false,
              reason,
              decidedBy,
            })
          }
        >
          却下
        </button>
      </div>
      <p className="note">
        採択はオントロジー変更の<strong>開始</strong>であって完了ではない。TTL への定義、
        SHACL 形状、<code>make gen</code> と <code>make ontology-check</code> を経てから
        コードに入る（CLAUDE.md のオントロジー変更の手順）。
      </p>
    </li>
  );
}
