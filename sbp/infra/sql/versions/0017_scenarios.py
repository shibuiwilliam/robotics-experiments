"""What-if のシナリオの索引。

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-08

M10-03、ADR-0036。結果の ``hypothetical`` アサーションはシナリオ用の名前付きグラフに書く（主信念に入れない。
不変条件 3）。ここには、どのシナリオがあるか・基準と介入ありの確率の曲線を持つ（読み出し用）。
行は書いたら変えない。
"""

from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """シナリオの表を作る。"""
    op.execute(
        """
        CREATE TABLE scenarios (
            scenario_id  uuid        NOT NULL PRIMARY KEY,
            site         text        NOT NULL,
            entity       text        NOT NULL,
            fluent       text        NOT NULL,
            wm_version   text        NOT NULL,
            origin       timestamptz NOT NULL,
            action_type  text        NOT NULL,
            payload      jsonb       NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX scenarios_recent ON scenarios (site, origin DESC)")
    op.execute(
        "COMMENT ON TABLE scenarios IS "
        "'What-if のシナリオの索引（基準と介入ありの確率の曲線）。結果の文はシナリオ用グラフにある。'"
    )


def downgrade() -> None:
    """表を落とす（結果の文はシナリオ用グラフに残る）。"""
    op.execute("DROP TABLE IF EXISTS scenarios")
