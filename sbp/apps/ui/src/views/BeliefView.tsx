/**
 * 信念ビュー（三源 + as-of スライダ、設計書 §7.1、§12.2、M7-04）。
 *
 * **軸を 2 本とも出す。** `asOf`（いつ時点でそう信じていたか）と `validAt`（いつの話か）は
 * 別の軸で、1 本にまとめると「過去を今の知識で書き換えた」表示になる——二時制を持っている
 * 理由がそこで失われる。
 *
 * **スライダの目盛りは信念が変わった時刻だけ**（`transactionTicks`）。等間隔にすると、
 * 何も起きていない区間を延々と動かすことになり、変化した瞬間を飛ばす。
 */

import { useMemo, useState } from "react";

import {
  bitemporalSlice,
  excludeHypothetical,
  formatConfidence,
  hasNoSource,
  isSuperseded,
  sourceContribution,
  transactionTicks,
} from "../lib/bitemporal";
import type { Belief } from "../lib/types";

interface Props {
  readonly beliefs: readonly Belief[];
  readonly subject: string;
}

export function BeliefView({ beliefs, subject }: Props): React.JSX.Element {
  const [showHypothetical, setShowHypothetical] = useState(false);
  const pool = showHypothetical ? beliefs : excludeHypothetical(beliefs);
  const ticks = useMemo(() => transactionTicks(pool), [pool]);
  const [tickIndex, setTickIndex] = useState(ticks.length - 1);
  const [validTime, setValidTime] = useState(ticks[ticks.length - 1] ?? "");

  const transactionTime = ticks[Math.min(tickIndex, ticks.length - 1)] ?? "";
  const slice =
    transactionTime === "" || validTime === ""
      ? []
      : bitemporalSlice(pool, { transactionTime, validTime });
  const contribution = sourceContribution(slice);

  return (
    <section aria-label="信念ビュー">
      <h2>信念 · {subject}</h2>

      <div className="axes">
        <label className="axis">
          <span className="axis-name">as-of（いつ時点でそう信じていたか）</span>
          <input
            type="range"
            min={0}
            max={Math.max(ticks.length - 1, 0)}
            value={Math.min(tickIndex, Math.max(ticks.length - 1, 0))}
            onChange={(event) => setTickIndex(Number(event.target.value))}
            disabled={ticks.length === 0}
            list="tx-ticks"
          />
          <output>{transactionTime || "（信念が無い）"}</output>
          <datalist id="tx-ticks">
            {ticks.map((tick) => (
              <option key={tick} value={tick} />
            ))}
          </datalist>
          <p className="axis-note">
            目盛りは<strong>信念が変わった時刻だけ</strong>（{ticks.length} 点）。
            訂正前の文がそのまま返る——訂正は旧文を閉じて新文を足すので、過去の判断は消えない。
          </p>
        </label>

        <label className="axis">
          <span className="axis-name">valid-at（いつの話か）</span>
          <select
            value={validTime}
            onChange={(event) => setValidTime(event.target.value)}
            disabled={ticks.length === 0}
          >
            {ticks.map((tick) => (
              <option key={tick} value={tick}>
                {tick}
              </option>
            ))}
          </select>
          <p className="axis-note">
            as-of とは別の軸である。こちらを戻すと「<strong>今の自分が</strong>過去について
            何を信じているか」——訂正を反映済みの答えが返る。
          </p>
        </label>
      </div>

      <div className="sources">
        <h3>三源の寄与</h3>
        {hasNoSource(contribution) ? (
          <p className="empty">
            この時点・この有効時刻には、どの源からの証拠も無い。
            <strong>確信度を按分していない</strong>ので、証拠が無い源に値は付かない。
          </p>
        ) : (
          <ul className="source-list">
            <li>
              <span className="source-name">観測</span>
              <span className="source-count">{contribution.observed}</span>
            </li>
            <li>
              <span className="source-name">記録・推定</span>
              <span className="source-count">{contribution.recorded}</span>
            </li>
            <li>
              <span className="source-name">計画</span>
              <span className="source-count">{contribution.planned}</span>
            </li>
          </ul>
        )}
      </div>

      <table className="beliefs">
        <caption>
          {slice.length} 件。<code>confidence</code> は較正済みの値のみ（不変条件 7）。
        </caption>
        <thead>
          <tr>
            <th scope="col">述語</th>
            <th scope="col">目的語</th>
            <th scope="col">様相</th>
            <th scope="col">確信度</th>
            <th scope="col">有効区間</th>
            <th scope="col">来歴</th>
          </tr>
        </thead>
        <tbody>
          {slice.map((belief) => (
            <tr
              key={belief.assertionId}
              className={isSuperseded(belief) ? "superseded" : undefined}
            >
              <td>
                <code>{belief.predicate}</code>
              </td>
              <td>{belief.object}</td>
              <td>{belief.modality}</td>
              <td className="num">{formatConfidence(belief.confidence)}</td>
              <td>
                [{belief.validFrom}, {belief.validTo ?? "…"})
              </td>
              <td>
                <code>{belief.provenance}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <label className="toggle">
        <input
          type="checkbox"
          checked={showHypothetical}
          onChange={(event) => setShowHypothetical(event.target.checked)}
        />
        仮説シナリオ（<code>hypothetical</code>）も表示する
        <span className="toggle-note">
          既定では出さない。混ぜると「決まったこと」として読まれる（不変条件 3）。
        </span>
      </label>
    </section>
  );
}
