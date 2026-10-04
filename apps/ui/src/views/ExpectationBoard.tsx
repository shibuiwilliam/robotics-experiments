/**
 * 期待ボード（設計書 §9.3、M7-04）。
 *
 * **違反件数ではなく帰属を見せる。** 「違反 3 件」だけ出すと、毎回人が証拠を開いて
 * 「規則が悪いのか WM が悪いのか」を同じ手順で判断することになる。記号面と rollout 面の
 * 判定の組み合わせから読みを出す（`blameFor`）。
 *
 * **空の列も出す。** 「違反 0 件」と「違反の列が無い」は違う情報である——後者は違反を
 * 検出する経路が動いていない可能性を含む。
 */

import { BLAME_EXPLANATION, blameFor, board, dueSoon } from "../lib/expectations";
import type { Expectation, ExpectationStatus } from "../lib/types";

// 鍵は API が返す状態の値そのもの（M8-18 で揃えた。所見 86）
const STATUS_LABEL: Readonly<Record<ExpectationStatus, string>> = {
  Created: "作成",
  Active: "検証中",
  Fulfilled: "成立",
  Violated: "違反",
  Undetermined: "判定不能",
  Learned: "学習済み",
};

interface Props {
  readonly expectations: readonly Expectation[];
  readonly now: string;
}

export function ExpectationBoard({
  expectations,
  now,
}: Props): React.JSX.Element {
  const columns = board(expectations);
  const soon = dueSoon(expectations, now);

  return (
    <section aria-label="期待ボード">
      <h2>期待ボード</h2>

      {soon.length > 0 ? (
        <p className="due-soon">
          期限が近い検証が {soon.length} 件（30 分以内）:{" "}
          {soon.map((expectation) => shortIri(expectation.target)).join(", ")}
        </p>
      ) : null}

      <div className="board">
        {columns.map((column) => (
          <div
            key={column.status}
            className={`column ${column.status.toLowerCase()}`}
          >
            <h3>
              {STATUS_LABEL[column.status]}{" "}
              <span className="count">{column.expectations.length}</span>
            </h3>
            {column.expectations.length === 0 ? (
              <p className="empty-column">
                0 件
                {column.status === "Violated" ? (
                  <span className="hint">
                    ——違反が無いのか、検証が走っていないのか。
                    <code>lifting_delay</code> と期待の生成数を確認する
                  </span>
                ) : null}
              </p>
            ) : (
              <ul>
                {column.expectations.map((expectation) => {
                  const blame = blameFor(expectation);
                  return (
                    <li key={expectation.expectationId} className="card">
                      <p className="card-title">
                        {shortIri(expectation.actionType)} →{" "}
                        {shortIri(expectation.target)}
                      </p>
                      <p className="card-deadline">
                        {/* `Created` は効果をまだ持たないので期限が無い。空欄で誤魔化さない */}
                        期限 {expectation.deadline ?? "（未設定——期待効果がまだ無い）"}
                        {expectation.effectCount === 0 ? (
                          <span className="hint">
                            ——期待効果が 0 件（何も期待していない）
                          </span>
                        ) : null}
                      </p>
                      <dl className="verdicts">
                        <dt>記号面</dt>
                        <dd>{renderVerdict(expectation.symbolicVerdict)}</dd>
                        <dt>rollout 面</dt>
                        <dd>{renderVerdict(expectation.rolloutVerdict)}</dd>
                      </dl>
                      {column.status === "Violated" || blame === "unknown" ? (
                        <p className={`blame blame-${blame}`}>
                          {BLAME_EXPLANATION[blame]}
                        </p>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

/** 判定の表示。`null` を「当たり」として描かない（未観測と成立は別である）。 */
function renderVerdict(verdict: boolean | null): string {
  if (verdict === null) return "未判定";
  return verdict ? "成立" : "不成立";
}

function shortIri(iri: string): string {
  const tail = iri.split(/[#/]/).pop();
  return tail === undefined || tail === "" ? iri : tail;
}
