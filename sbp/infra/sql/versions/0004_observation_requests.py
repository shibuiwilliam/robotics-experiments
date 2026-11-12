"""追加観測の要求を作業キューとして持つ。

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-04

M8-05、ADR-0011。照合の解決が「判断できない」だったとき追加観測の要求が出る（M3-06）。
エピステミック・スケジューラはこれを周期ごとに評価して観測クエリを発するが、**要求を溜める
場所が無かった**ためメモリにしか置けず、再起動で消えていた。

**イベントログには載せられない。** `EventEnvelope` は「誰かの主張」を運ぶ形で、様相と信念を
必須に持つ。追加観測の要求は主張ではなく指令であり、様相格子（observed / inferred /
predicted / planned / normative / hypothetical）に席が無い。

**既存の `quarantine` と同じ形の作業キューである。** SHACL 違反の隔離キューが
`reviewed_at` / `resolution` を直接更新するのと同じで、世界の状態ではなく作業の状態を持つ。

**行を消さない。** 満たされた要求も `satisfied_at` を立てるだけで残す——「観測を要求したが
満たされなかった」履歴が消えると、スケジューラが機能しているかどうかを後から確かめられない。
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """作業キューを作る。"""
    op.execute(
        """
        CREATE TABLE observation_requests (
            request_id      text        NOT NULL PRIMARY KEY,
            site            text        NOT NULL,
            entity          text        NOT NULL,
            fluent          text        NOT NULL,
            valid_from      timestamptz NOT NULL,
            valid_to        timestamptz,
            reason          text        NOT NULL,
            utility         double precision NOT NULL CHECK (utility >= 0.0),
            discrepancy_id  text        NOT NULL,
            requested_at    timestamptz NOT NULL DEFAULT now(),
            issued_at       timestamptz,
            issued_query_id text,
            satisfied_at    timestamptz
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE observation_requests IS "
        "'追加観測の要求の作業キュー（ADR-0011）。要求は主張ではないのでイベントログに"
        "載せられない。行は消さず satisfied_at を立てる。'"
    )
    # 未充足の要求を拾うための部分索引。**スケジューラが毎周期引くのはこの集合だけ**で、
    # 満たされた履歴は走査に入れたくない
    op.execute(
        "CREATE INDEX observation_requests_outstanding_idx ON observation_requests "
        "(site, utility DESC, requested_at) WHERE satisfied_at IS NULL"
    )
    # 同じ実体・フルーエントの要求が何度も出たときに重複を見つけるための索引
    op.execute(
        "CREATE INDEX observation_requests_target_idx ON observation_requests "
        "(site, entity, fluent, requested_at DESC)"
    )


def downgrade() -> None:
    """作業キューを落とす。"""
    op.execute("DROP TABLE IF EXISTS observation_requests")
