/**
 * 不一致キュー（設計書 §8.2、§12.2、M7-04）。
 *
 * **証拠を並置するだけで、どちらが正しいかを示さない。** UI が「観測が正しい」と暗示すると、
 * 人の判断が基盤の推定の追認になり、学習信号として機能しなくなる。
 *
 * **解決の入力に「正しい値」の欄を置かない。** 選ぶのは解決の種別——どの前提が崩れていたか——
 * であり、真偽の宣言ではない（ADR-0008）。種別がそのまま学習信号の種類を決める。
 */

import { useState } from "react";

import { RESOLUTION_EFFECTS, checkResolution, evidenceRows, isStale, visibleQueue } from "../lib/queue";
import { formatConfidence } from "../lib/bitemporal";
import type { Discrepancy, Resolution, ResolutionKind } from "../lib/types";

const KIND_LABEL: Readonly<Record<string, string>> = {
  EXISTENCE: "存在",
  TIME: "時刻",
  STATE: "状態",
  IDENTITY: "同一性",
  QUANTITY: "数量",
};

interface Props {
  readonly discrepancies: readonly Discrepancy[];
  readonly now: string;
  readonly resolvedBy: string;
  readonly onResolve: (resolution: Resolution) => void;
}

export function DiscrepancyQueue({
  discrepancies,
  now,
  resolvedBy,
  onResolve,
}: Props): React.JSX.Element {
  const queue = visibleQueue(discrepancies);
  const [selectedId, setSelectedId] = useState<string | null>(
    queue[0]?.discrepancyId ?? null,
  );
  const selected =
    queue.find((item) => item.discrepancyId === selectedId) ?? queue[0];

  return (
    <div className="split">
      <section className="queue" aria-label="不一致キュー">
        <h2>
          不一致キュー <span className="count">{queue.length}</span>
        </h2>
        {queue.length === 0 ? (
          <p className="empty">
            手を付けるべき不一致は無い。<strong>0 件であることと、照合が動いていないことは
            別である</strong>——起票数の指標（<code>discrepancies_raised</code>）も確認する。
          </p>
        ) : (
          <ol>
            {queue.map((item) => (
              <li key={item.discrepancyId}>
                <button
                  type="button"
                  className={item.discrepancyId === selected?.discrepancyId ? "row active" : "row"}
                  onClick={() => setSelectedId(item.discrepancyId)}
                >
                  <span className="severity" style={{ opacity: 0.35 + item.severity * 0.65 }}>
                    {item.severity.toFixed(3)}
                  </span>
                  <span className="kind">{KIND_LABEL[item.kind] ?? item.kind}</span>
                  <span className="subject">{shortIri(item.subject)}</span>
                  {isStale(item, now) ? <span className="stale">滞留</span> : null}
                </button>
              </li>
            ))}
          </ol>
        )}
      </section>

      {selected === undefined ? null : (
        <section className="detail" aria-label="証拠と解決">
          <h3>
            {KIND_LABEL[selected.kind] ?? selected.kind}の不一致 ·{" "}
            {shortIri(selected.subject)}
          </h3>
          <p className="meta">
            深刻度 {selected.severity.toFixed(3)} · 起票 {selected.raisedAt} · 状態{" "}
            {selected.state}
          </p>

          <table className="evidence">
            <caption>
              三源の証拠。<strong>どちらが正しいかは示さない</strong>——判断は人がする。
            </caption>
            <tbody>
              {evidenceRows(selected).map((row) => (
                <tr key={row.label} className={row.isEmpty ? "row-empty" : undefined}>
                  <th scope="row">{row.label}</th>
                  <td>
                    {row.isEmpty ? (
                      <span className="none">この源には証拠が無い</span>
                    ) : (
                      <ul>
                        {row.beliefs.map((belief) => (
                          <li key={belief.assertionId}>
                            <code>{shortIri(belief.predicate)}</code> = {belief.object}
                            <span className="conf">
                              {formatConfidence(belief.confidence)}
                            </span>
                            <span className="valid">
                              [{belief.validFrom}, {belief.validTo ?? "…"})
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <ResolutionForm
            discrepancyId={selected.discrepancyId}
            resolvedBy={resolvedBy}
            onResolve={onResolve}
          />
        </section>
      )}
    </div>
  );
}

interface FormProps {
  readonly discrepancyId: string;
  readonly resolvedBy: string;
  readonly onResolve: (resolution: Resolution) => void;
}

const KINDS: readonly ResolutionKind[] = [
  "RECORD_WRONG",
  "OBSERVATION_WRONG",
  "DEFINITION_MISMATCH",
  "SYSTEMATIC_BIAS",
  "NEEDS_OBSERVATION",
];

const KIND_TEXT: Readonly<Record<ResolutionKind, string>> = {
  RECORD_WRONG: "記録が誤っていた",
  OBSERVATION_WRONG: "観測（推定）が誤っていた",
  DEFINITION_MISMATCH: "どちらも正しく、定義が食い違っている",
  SYSTEMATIC_BIAS: "系統的な偏り（月末バッチ・シフト境界など）",
  NEEDS_OBSERVATION: "判断できない（追加観測が必要）",
};

function ResolutionForm({
  discrepancyId,
  resolvedBy,
  onResolve,
}: FormProps): React.JSX.Element {
  const [kind, setKind] = useState<ResolutionKind>("RECORD_WRONG");
  const [reason, setReason] = useState("");
  const resolution: Resolution = { discrepancyId, kind, reason, resolvedBy };
  const check = checkResolution(resolution);

  return (
    <form
      className="resolve"
      onSubmit={(event) => {
        event.preventDefault();
        if (check.valid) onResolve(resolution);
      }}
    >
      <fieldset>
        <legend>解決の種別</legend>
        {KINDS.map((option) => (
          <label key={option} className="choice">
            <input
              type="radio"
              name="kind"
              value={option}
              checked={kind === option}
              onChange={() => setKind(option)}
            />
            <span className="choice-label">{KIND_TEXT[option]}</span>
            <span className="choice-effect">→ {RESOLUTION_EFFECTS[option]}</span>
          </label>
        ))}
      </fieldset>

      <label className="reason">
        理由（必須。<strong>これが無いと学習信号にならない</strong>）
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={3}
          placeholder="何を見てそう判断したか"
        />
      </label>

      {check.problems.length > 0 ? (
        <ul className="problems">
          {check.problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}

      <button type="submit" disabled={!check.valid}>
        解決として投入する
      </button>
    </form>
  );
}

/** IRI の末尾だけを出す（画面幅を IRI で埋めない）。 */
function shortIri(iri: string): string {
  const tail = iri.split(/[#/]/).pop();
  return tail === undefined || tail === "" ? iri : tail;
}
