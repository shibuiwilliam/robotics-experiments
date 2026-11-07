/**
 * アクションの承認（設計書 §9.2、M8-22、ADR-0021）。
 *
 * **投入が承認そのものである。** 理由は必須。対象・深刻度・不一致 ID は不一致から入れる（人が
 * 書き換えない）。**前提未達の上書きは、既定で閉じた折りたたみで理由を書かせる。**
 * **補償は実行しない**（H-08 の判断待ち）——契約の要約に補償先を表示するだけ。
 *
 * **結果を人に返す。** 投入は受け付けまでで、実行の成否は判断の状態として読む。失敗は理由を整形せず
 * 見せ、**自動では再試行されない**ので新しい判断として再投入するよう案内する。
 */

import { useEffect, useState } from "react";

import {
  RESUBMIT_NOTE,
  approvalNotice,
  buildActionRequest,
  checkAction,
  describeDecision,
  POLL_LIMIT,
  hasPending,
  nextPoll,
} from "../lib/actions";
import type { ActionRequest, ActionType, DecisionStatus, Discrepancy } from "../lib/types";

interface Props {
  readonly discrepancy: Discrepancy;
  readonly approvedBy: string;
  /** 契約の一覧（`undefined` は未読み込み、`error` は読めなかった理由）。 */
  readonly actionTypes: readonly ActionType[] | undefined;
  readonly actionTypesError: string | null;
  readonly onSubmit: (
    request: ActionRequest,
  ) => Promise<{ readonly accepted: boolean; readonly detail: string }>;
  readonly loadDecisions: ((subject: string) => Promise<readonly DecisionStatus[]>) | undefined;
}

/** 適用待ちが残るあいだ、状態を読み直す間隔（ミリ秒）。 */
const POLL_MS = 5000;



export function ActionApproval({
  discrepancy,
  approvedBy,
  actionTypes,
  actionTypesError,
  onSubmit,
  loadDecisions,
}: Props): React.JSX.Element {
  // 再投入のたびに別の冪等の鍵にするため、フォームを作り直す
  const [generation, setGeneration] = useState(0);

  if (actionTypesError !== null) {
    return (
      <p className="error" role="alert">
        アクション契約を読めなかった: {actionTypesError}
        <span className="error-note">
          <strong>「提案できるアクションが無い」ではない。</strong>
        </span>
      </p>
    );
  }
  if (actionTypes === undefined) return <p className="empty">アクション契約を読み込み中</p>;
  if (actionTypes.length === 0) {
    return (
      <p className="unwired" role="status">
        <strong>アクションを提案できない</strong>——契約が 1 件も登録されていない
      </p>
    );
  }
  return (
    <section className="actions" aria-label="アクションの承認">
      <h4>アクションを承認する</h4>
      <ActionForm
        key={`${discrepancy.discrepancyId}:${generation}`}
        discrepancy={discrepancy}
        approvedBy={approvedBy}
        actionTypes={actionTypes}
        onSubmit={onSubmit}
        onNew={() => setGeneration((value) => value + 1)}
      />
      <DecisionHistory
        key={discrepancy.discrepancyId}
        subjects={discrepancy.subjects}
        loadDecisions={loadDecisions}
        refreshKey={generation}
      />
    </section>
  );
}

function ActionForm({
  discrepancy,
  approvedBy,
  actionTypes,
  onSubmit,
  onNew,
}: {
  readonly discrepancy: Discrepancy;
  readonly approvedBy: string;
  readonly actionTypes: readonly ActionType[];
  readonly onSubmit: Props["onSubmit"];
  readonly onNew: () => void;
}): React.JSX.Element {
  const [actionType, setActionType] = useState("");
  const [target, setTarget] = useState(discrepancy.subjects[0] ?? "");
  const [reason, setReason] = useState("");
  const [override, setOverride] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");
  const [result, setResult] = useState<{ accepted: boolean; detail: string } | null>(null);
  const [sending, setSending] = useState(false);
  // 冪等の鍵はフォームごとに 1 つ（再送で同じ値。ADR-0012 / ADR-0014）
  const [decisionId] = useState(() => crypto.randomUUID());

  const contract = actionTypes.find((item) => item.actionType === actionType);
  const request = buildActionRequest({
    decisionId,
    discrepancy,
    actionType,
    target,
    approvedBy,
    reason,
    overrideReason: override ? overrideReason : null,
  });
  const check = checkAction(request, actionTypes);

  return (
    <form
      className="resolve"
      onSubmit={(event) => {
        event.preventDefault();
        if (!check.valid || sending) return;
        setSending(true);
        onSubmit(request)
          .then(setResult)
          .catch((error: unknown) =>
            setResult({
              accepted: false,
              detail: error instanceof Error ? error.message : String(error),
            }),
          )
          .finally(() => setSending(false));
      }}
    >
      <label>
        アクション
        <select value={actionType} onChange={(event) => setActionType(event.target.value)}>
          <option value="">選ぶ</option>
          {actionTypes.map((item) => (
            <option key={item.actionType} value={item.actionType}>
              {shortIri(item.actionType)}（{item.riskClass}）
            </option>
          ))}
        </select>
      </label>

      {discrepancy.subjects.length > 1 ? (
        <label>
          対象
          <select value={target} onChange={(event) => setTarget(event.target.value)}>
            {discrepancy.subjects.map((subject) => (
              <option key={subject} value={subject}>
                {shortIri(subject)}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <p className="meta">
          対象 <code>{shortIri(target)}</code> · 深刻度 {discrepancy.severity.toFixed(3)}（不一致から）
        </p>
      )}

      {contract === undefined ? null : <ContractSummary contract={contract} />}

      <label className="reason">
        理由（必須。<strong>投入が承認になる</strong>）
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={2}
          placeholder="なぜこのアクションを承認するか"
        />
      </label>

      <details>
        <summary>前提未達を上書きする（安全ガードを外す操作）</summary>
        <label className="choice">
          <input
            type="checkbox"
            checked={override}
            onChange={(event) => setOverride(event.target.checked)}
          />
          <span className="choice-label">上書きして実行する</span>
        </label>
        {override ? (
          <label className="reason">
            上書きの理由（必須）
            <textarea
              value={overrideReason}
              onChange={(event) => setOverrideReason(event.target.value)}
              rows={2}
            />
          </label>
        ) : null}
      </details>

      {check.problems.length > 0 && (actionType !== "" || reason !== "") ? (
        <ul className="problems">
          {check.problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}

      {override ? (
        <p className="problems" role="status">
          <strong>前提未達の上書きが有効</strong>——安全ガードを外して投入する（折りたたみの中の設定）
        </p>
      ) : null}
      <button type="submit" disabled={!check.valid || sending || result?.accepted === true}>
        {override ? "前提未達を上書きして承認として投入する" : "承認として投入する"}
      </button>
      {result === null ? null : (
        <p className={result.accepted ? "notice" : "error"} role="status">
          {result.accepted ? "受け付けた" : "受け付けられなかった"}: {result.detail}
          {result.accepted ? (
            <span className="error-note">
              実行の成否は下の「投入した判断」で読む（受け付けただけで、実行はまだ）。
              <button type="button" onClick={onNew}>
                新しい判断を作る
              </button>
            </span>
          ) : null}
        </p>
      )}
    </form>
  );
}

function ContractSummary({ contract }: { readonly contract: ActionType }): React.JSX.Element {
  return (
    <div className="contract">
      <p className="meta">{approvalNotice(contract)}</p>
      <dl>
        {contract.preconditions.map((face) => (
          <div key={face.face}>
            <dt>前提（{face.face}）</dt>
            <dd>{face.descriptions.join(" / ")}</dd>
          </div>
        ))}
        {contract.effects.map((face) => (
          <div key={face.face}>
            <dt>期待する効果（{face.face}）</dt>
            <dd>
              {face.effects.map((item) => `${item.description}（${item.within} 以内）`).join(" / ")}
            </dd>
          </div>
        ))}
        {contract.rateLimit === null ? null : (
          <div>
            <dt>反復の制限</dt>
            <dd>
              同一対象に {contract.rateLimit.window} で {contract.rateLimit.max} 回まで
            </dd>
          </div>
        )}
        <div>
          <dt>補償</dt>
          <dd>
            <code>{shortIri(contract.compensation)}</code>（表示のみ。実行する入口は無い）
          </dd>
        </div>
      </dl>
    </div>
  );
}

function DecisionHistory({
  subjects,
  loadDecisions,
  refreshKey,
}: {
  readonly subjects: readonly string[];
  readonly loadDecisions: Props["loadDecisions"];
  readonly refreshKey: number;
}): React.JSX.Element {
  const [statuses, setStatuses] = useState<readonly DecisionStatus[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  // 自動で読み直した回数（人が押した更新は数えない）
  const [autoPolls, setAutoPolls] = useState(0);

  const subjectsKey = subjects.join("|");
  useEffect(() => {
    if (loadDecisions === undefined) return;
    let cancelled = false;
    Promise.all(subjects.map((subject) => loadDecisions(subject))).then(
      (groups) => {
        if (cancelled) return;
        const flat = groups.flat().filter((item) => item.kind === "action");
        flat.sort((a, b) => b.receivedAt.localeCompare(a.receivedAt));
        setStatuses(flat);
        setError(null);
      },
      (failure: unknown) => {
        if (!cancelled) setError(failure instanceof Error ? failure.message : String(failure));
      },
    );
    return () => {
      cancelled = true;
    };
    // subjects は配列の同一性ではなく中身（subjectsKey）で比べる
  }, [subjectsKey, loadDecisions, refreshKey, tick]);

  // 適用待ちが残るあいだだけ読み直す（待ちが無いのに叩き続けない）
  const waiting = statuses !== null && hasPending(statuses);
  const capped = autoPolls >= POLL_LIMIT;
  const [wake, setWake] = useState(0);
  useEffect(() => {
    if (!waiting) return;
    const timer = setTimeout(() => {
      const action = nextPoll({ pending: waiting, autoPolls, hidden: document.hidden });
      if (action === "wait") {
        // 非表示のタブは読み直さない（見ていない画面のために API を叩かない）。次の周期を待つ
        setWake((value) => value + 1);
      } else if (action === "fetch") {
        setAutoPolls((value) => value + 1);
        setTick((value) => value + 1);
      }
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [waiting, autoPolls, tick, wake]);

  if (loadDecisions === undefined) {
    return (
      <p className="unwired" role="status">
        <strong>判断の状態を読めない</strong>——読み出しが配線されていない
      </p>
    );
  }
  if (error !== null) {
    return (
      <p className="error" role="alert">
        判断の状態を読めなかった: {error}
        <span className="error-note">
          <strong>「実行されていない」ではない。</strong>
        </span>
      </p>
    );
  }
  return (
    <div className="decisions">
      <h5>
        投入した判断{" "}
        <button
          type="button"
          onClick={() => {
            setAutoPolls(0);
            setTick((value) => value + 1);
          }}
        >
          状態を更新
        </button>
      </h5>
      {capped && waiting ? (
        <p className="hint" role="status">
          自動更新を止めた（適用待ちのまま {POLL_LIMIT} 回読んだ）。learning-worker が止まっていないか
          確認し、「状態を更新」で読み直す。
        </p>
      ) : null}
      {statuses === null ? (
        <p className="empty">読み込み中</p>
      ) : statuses.length === 0 ? (
        <p className="empty">この不一致の対象に、投入したアクションの判断は無い</p>
      ) : (
        <ul>
          {statuses.map((status) => {
            const view = describeDecision(status);
            return (
              <li key={status.decisionId} className={`decision-${view.state}`}>
                <strong>{view.label}</strong> · {status.receivedAt} ·{" "}
                <code>{shortIri(status.subject)}</code>
                <div className="decision-message">{view.message}</div>
                {view.canResubmit ? <div className="hint">{RESUBMIT_NOTE}</div> : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function shortIri(iri: string): string {
  const tail = iri.split(/[#/]/).pop();
  return tail === undefined || tail === "" ? iri : tail;
}

