"""期待を作業項目として持つ。

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-04

M8-18。不一致（alembic 0006、ADR-0013）と同じ形である——期待も状態機械を持つ作業項目で、
``EventEnvelope`` に全体が入らない（``expected_effects`` / ``verification`` / ``history`` を
入れる場所が無い。所見 62）。

**UI が読む先がこれである。** 期待ボードは `<site>.reasoning.expectation` を読んでいたが、
イベントに全体が入らないので中身を読めなかった（所見 85 / 86）。

**問い合わせに使う列だけを展開し、本体は jsonb で持つ**（ADR-0013 の問い 3 と同じ理由）。
期限（`deadline`）を列に出すのは、期待ボードが「期限が近い順」「期限切れ」で絞るためである
——`expected_effects` の中の最小値なので、読み出しで計算すると並べ替えができない。
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """期待の作業項目表を作る。"""
    op.execute(
        """
        CREATE TABLE expectations (
            expectation_id uuid        NOT NULL PRIMARY KEY,
            site           text        NOT NULL,
            status         text        NOT NULL,
            status_rank    smallint    NOT NULL CHECK (status_rank BETWEEN 0 AND 3),
            action_type    text        NOT NULL,
            target         text        NOT NULL,
            created_at     timestamptz NOT NULL,
            deadline       timestamptz,
            terminal       boolean     NOT NULL DEFAULT false,
            payload        jsonb       NOT NULL,
            updated_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE expectations IS "
        "'期待の作業項目（M8-18）。不一致と同じく**純粋な投影ではない**——イベントに全体が"
        "入らないため、生成した側が直接書く。本体は payload(jsonb)。'"
    )
    # 期待ボードが最初に見る集合: 未決着のものを期限の近い順に。
    # **終端状態を除く**のは、検証済みの履歴でボードが埋まらないようにするため
    op.execute(
        "CREATE INDEX expectations_board_idx ON expectations "
        "(site, deadline NULLS LAST, created_at DESC) WHERE NOT terminal"
    )
    # 対象実体から期待を引く（信念ビューから「この設備に何を期待しているか」へ飛ぶ）
    op.execute(
        "CREATE INDEX expectations_target_idx ON expectations (site, target, created_at DESC)"
    )


def downgrade() -> None:
    """作業項目表を落とす。"""
    op.execute("DROP TABLE IF EXISTS expectations")
