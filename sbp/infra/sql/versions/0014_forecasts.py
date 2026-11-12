"""予測の保存（採点のため）。

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-08

M9-02、ADR-0032。予測は ``predicted`` のアサーション（平均だけ）としてグラフに書くが、採点（CRPS・被覆率）には
区間と標本が要る。ここに予測の全体（JSON）を持ち、地平を過ぎたら採点して ``scored_at`` を入れる。

**追記専用に近い投影。** 行は書いたら変えない。変わるのは ``scored_at``（一度だけ）。
``horizon_end`` は採点の期限の索引（起点 + 最も遠い先行時間）。
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """予測の表を作る。"""
    op.execute(
        """
        CREATE TABLE forecasts (
            forecast_id     uuid        NOT NULL PRIMARY KEY,
            site            text        NOT NULL,
            entity          text        NOT NULL,
            fluent          text        NOT NULL,
            horizon_origin  timestamptz NOT NULL,
            horizon_end     timestamptz NOT NULL,
            wm_version      text        NOT NULL,
            interval_level  real        NOT NULL CHECK (interval_level > 0 AND interval_level < 1),
            payload         jsonb       NOT NULL,
            scored_at       timestamptz,
            UNIQUE (site, entity, fluent, wm_version, horizon_origin)
        )
        """
    )
    op.execute(
        "CREATE INDEX forecasts_due ON forecasts (site, horizon_end) WHERE scored_at IS NULL"
    )
    op.execute("CREATE INDEX forecasts_series ON forecasts (site, entity, fluent, horizon_origin)")
    op.execute(
        "COMMENT ON TABLE forecasts IS "
        "'予測の全体（区間・標本つき）。地平を過ぎたら採点し scored_at を入れる。'"
    )


def downgrade() -> None:
    """表を落とす（採点前の予測は再現できない）。"""
    op.execute("DROP TABLE IF EXISTS forecasts")
