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

import {
  RESOLUTION_EFFECTS,
  checkResolution,
  isStale,
  referenceRows,
  visibleQueue,
} from "../lib/queue";
import type { EvidenceTarget } from "../lib/evidence";
import { DISCREPANCY_KIND_LABEL } from "../lib/types";
import type {
  BeliefEvidence,
  Discrepancy,
  Resolution,
  ResolutionOutcome,
} from "../lib/types";
import { EvidenceJuxtaposition } from "./EvidenceJuxtaposition";

interface Props {
  readonly discrepancies: readonly Discrepancy[];
  readonly now: string;
  readonly resolvedBy: string;
  readonly onResolve: (resolution: Resolution) => void;
  /** 三源の並置を読む関数（M8-17。未配線なら並置できない理由を画面に出す）。 */
  readonly loadEvidence?: (target: EvidenceTarget) => Promise<BeliefEvidence>;
}

export function DiscrepancyQueue({
  discrepancies,
  now,
  resolvedBy,
  onResolve,
  loadEvidence,
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
                  <span className="kind">{DISCREPANCY_KIND_LABEL[item.kind]}</span>
                  <span className="subject">{subjectLabel(item)}</span>
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
            {DISCREPANCY_KIND_LABEL[selected.kind]}の不一致 · {subjectLabel(selected)}
          </h3>
          <p className="meta">
            深刻度 {selected.severity.toFixed(3)} · 検出 {selected.detectedAt} · 状態{" "}
            {selected.status} · 検出器 <code>{selected.detector}</code> · オントロジー{" "}
            {selected.ontologyVersion}
          </p>

          <table className="evidence">
            <caption>
              不一致が参照しているもの。<strong>どちらが正しいかは示さない</strong>
              ——判断は人がする。三源（観測 / 記録 / 計画）の並置は下に出る。
            </caption>
            <tbody>
              {referenceRows(selected).map((row) => (
                <tr
                  key={row.label}
                  className={row.isEmpty ? "row-empty" : undefined}
                >
                  <th scope="row">{row.label}</th>
                  <td>
                    {row.isEmpty ? (
                      <span className="none">参照が無い</span>
                    ) : (
                      <ul>
                        {row.iris.map((iri) => (
                          <li key={iri}>
                            <code>{shortIri(iri)}</code>
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <EvidenceJuxtaposition discrepancy={selected} now={now} load={loadEvidence} />

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

/**
 * 選べる解決結果（設計書 §8.2 の表の 4 行）。
 *
 * **抑制（系統誤差）は選択肢に無い。** 抑制は解決結果ではなく別の遷移（`Open → Suppressed`）
 * であり、信頼性モデルで説明できる系統誤差に限られる（人がここで選ぶものではない）。
 * 以前はここに `SYSTEMATIC_BIAS` があり、**API が受け付けない値を人に選ばせていた**（所見 83）。
 */
const OUTCOMES: readonly ResolutionOutcome[] = [
  "physical_correct",
  "record_correct",
  "both_wrong",
  "undetermined",
];

/** 解決結果の表示（名前の向きに注意: 物理が正しい = 記録が誤っていた）。 */
const OUTCOME_TEXT: Readonly<Record<ResolutionOutcome, string>> = {
  physical_correct: "物理が正しかった（記録が誤っていた）",
  record_correct: "記録が正しかった（観測・推定が誤っていた）",
  both_wrong: "どちらも誤っていた（定義が食い違っている）",
  undetermined: "判断できない（追加観測が必要）",
};

function ResolutionForm({
  discrepancyId,
  resolvedBy,
  onResolve,
}: FormProps): React.JSX.Element {
  const [outcome, setOutcome] = useState<ResolutionOutcome>("physical_correct");
  const [note, setNote] = useState("");
  // **冪等の鍵はフォームごとに 1 つ。** 再送（ネットワークの再試行、人の二度押し）で同じ値を
  // 送る必要があるので、送信のたびに作らない（ADR-0012）
  const [decisionId] = useState(() => crypto.randomUUID());
  const resolution: Resolution = {
    decisionId,
    discrepancyId,
    outcome,
    note,
    resolvedBy,
  };
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
        {OUTCOMES.map((option) => (
          <label key={option} className="choice">
            <input
              type="radio"
              name="outcome"
              value={option}
              checked={outcome === option}
              onChange={() => setOutcome(option)}
            />
            <span className="choice-label">{OUTCOME_TEXT[option]}</span>
            <span className="choice-effect">→ {RESOLUTION_EFFECTS[option]}</span>
          </label>
        ))}
      </fieldset>

      <label className="reason">
        理由（必須。<strong>これが無いと学習信号にならない</strong>）
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
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
      <p className="hint">
        投入は<strong>受け付けまで</strong>である（ADR-0012）。適用は
        <code>learning-worker</code> が行い、何が適用されたかは判断の記録に残る。
      </p>
    </form>
  );
}

/**
 * 主体の表示。**複数ある場合は全部出す**（類型 4 は競合する 2 つの主張である）。
 *
 * 1 つだけ出すと、どちらの実体の話か分からない不一致ができる。
 */
function subjectLabel(discrepancy: Discrepancy): string {
  if (discrepancy.subjects.length === 0) return "(主体なし)";
  return discrepancy.subjects.map(shortIri).join(" / ");
}

/** IRI の末尾だけを出す（画面幅を IRI で埋めない）。 */
function shortIri(iri: string): string {
  const tail = iri.split(/[#/]/).pop();
  return tail === undefined || tail === "" ? iri : tail;
}
