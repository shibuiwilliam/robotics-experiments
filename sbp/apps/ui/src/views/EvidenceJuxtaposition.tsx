/**
 * 証拠の三源並置（設計書 §8.2、§10.3、M8-17、ADR-0020）。
 *
 * **どちらが正しいかを示さない。** 観測・記録・計画の寄与を並べるだけで、判断は人がする
 * （ADR-0008）。**空欄にしない**——寄与が無い源は「寄与なし」、寄与が記録されていない信念は
 * 「記録されていない」と明示する。読み込みの失敗も画面に出す（失敗を「証拠なし」と読ませない）。
 */

import { useEffect, useState } from "react";

import { evidenceTargets, pointRows, sourceColumns } from "../lib/evidence";
import type { EvidenceTarget } from "../lib/evidence";
import type { BeliefEvidence, Discrepancy } from "../lib/types";

interface Props {
  readonly discrepancy: Discrepancy;
  readonly now: string;
  /** 並置を読む関数（未配線なら `undefined`。そのときは理由を出す）。 */
  readonly load: ((target: EvidenceTarget) => Promise<BeliefEvidence>) | undefined;
}

type Loaded =
  | { readonly status: "loading" }
  | { readonly status: "error"; readonly message: string }
  | { readonly status: "done"; readonly evidence: BeliefEvidence };

export function EvidenceJuxtaposition({ discrepancy, now, load }: Props): React.JSX.Element {
  const resolved = evidenceTargets(discrepancy, now);
  if (resolved.kind === "unavailable") {
    return (
      <p className="unwired" role="status">
        <strong>三源を並置できない</strong>——{resolved.reason}
      </p>
    );
  }
  if (load === undefined) {
    return (
      <p className="unwired" role="status">
        <strong>三源を並置できない</strong>——読み出しが配線されていない
      </p>
    );
  }
  return (
    <div className="juxtaposition">
      {resolved.targets.map((target) => (
        <TargetEvidence
          key={`${target.entity}|${target.fluent}|${target.start}|${target.end}`}
          target={target}
          load={load}
        />
      ))}
    </div>
  );
}

function TargetEvidence({
  target,
  load,
}: {
  readonly target: EvidenceTarget;
  readonly load: (target: EvidenceTarget) => Promise<BeliefEvidence>;
}): React.JSX.Element {
  const [state, setState] = useState<Loaded>({ status: "loading" });
  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    load(target).then(
      (evidence) => {
        if (!cancelled) setState({ status: "done", evidence });
      },
      (error: unknown) => {
        if (!cancelled) {
          setState({
            status: "error",
            message: error instanceof Error ? error.message : String(error),
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [target, load]);

  const heading = (
    <h4>
      <code>{shortIri(target.entity)}</code> · <code>{shortIri(target.fluent)}</code>
    </h4>
  );
  if (state.status === "loading") {
    return (
      <section aria-busy="true">
        {heading}
        <p className="empty">読み込み中</p>
      </section>
    );
  }
  if (state.status === "error") {
    return (
      <section>
        {heading}
        <p className="error" role="alert">
          並置を読めなかった: {state.message}
          <span className="error-note">
            <strong>「証拠が無い」ではない。</strong>
          </span>
        </p>
      </section>
    );
  }
  const columns = sourceColumns(state.evidence);
  const rows = pointRows(state.evidence.points, state.evidence);
  return (
    <section>
      {heading}
      <table className="juxtaposition-columns">
        <caption>
          三源の寄与。<strong>どちらが正しいかは示さない</strong>——判断は人がする。
        </caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.source} scope="col">
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            {columns.map((column) => (
              <td key={column.source} className={`state-${column.state}`}>
                <div>{column.note}</div>
                {column.state === "contributed" && column.unrecordedPoints > 0 ? (
                  <div className="none">
                    寄与が記録されていない点 {column.unrecordedPoints} 件を含まない
                  </div>
                ) : null}
                {column.state === "contributed" && column.unreadPoints > 0 ? (
                  <div className="none">
                    寄与を読めていない点 {column.unreadPoints} 件を含まない（読み出しが上限で切れた）
                  </div>
                ) : null}
                {column.source === "plan" ? (
                  <div>
                    {column.plannedSpans === 0
                      ? "区間と重なる計画なし"
                      : `区間と重なる計画 ${column.plannedSpans} 件`}
                  </div>
                ) : null}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
      {rows.length === 0 ? null : (
        <table className="juxtaposition-points">
          <thead>
            <tr>
              <th scope="col">時刻</th>
              <th scope="col">確率</th>
              <th scope="col">出どころ</th>
              <th scope="col">観測</th>
              <th scope="col">記録</th>
              <th scope="col">計画</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.assertionId}>
                <td className="num">{row.at}</td>
                <td className="num">{row.probability.toFixed(3)}</td>
                <td>{row.origin}</td>
                {row.shares === null ? (
                  <td colSpan={3} className="none">
                    {row.contribution}
                  </td>
                ) : (
                  <>
                    <td className="num">{percent(row.shares.observed, row.dominant === "observed")}</td>
                    <td className="num">{percent(row.shares.record, row.dominant === "record")}</td>
                    <td className="num">{percent(row.shares.plan, row.dominant === "plan")}</td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** 割合の表示（最も動かした源に印を付ける。印は「支配的だった」だけを言い、正しさを言わない）。 */
function percent(share: number, dominant: boolean): string {
  const text = `${(share * 100).toFixed(0)}%`;
  return dominant ? `${text} ●` : text;
}

function shortIri(iri: string): string {
  const tail = iri.split(/[#/]/).pop();
  return tail === undefined || tail === "" ? iri : tail;
}
