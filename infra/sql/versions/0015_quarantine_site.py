"""隔離キューに拠点の列を足す。

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-08

M10-01、ADR-0035。``quarantine`` は 0001 で作ったが書く者がいなかった。拠点ごとに分けて読むため ``site`` を足す
（既存の行は無い）。確認済みでない行の索引は拠点つきに作り直す。
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """拠点の列を足す。"""
    op.execute("ALTER TABLE quarantine ADD COLUMN site text NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE quarantine ALTER COLUMN site DROP DEFAULT")
    op.execute("DROP INDEX IF EXISTS quarantine_unreviewed_idx")
    op.execute(
        "CREATE INDEX quarantine_unreviewed_idx ON quarantine (site, quarantined_at) "
        "WHERE reviewed_at IS NULL"
    )


def downgrade() -> None:
    """拠点の列を落とす。"""
    op.execute("DROP INDEX IF EXISTS quarantine_unreviewed_idx")
    op.execute(
        "CREATE INDEX quarantine_unreviewed_idx ON quarantine (quarantined_at) "
        "WHERE reviewed_at IS NULL"
    )
    op.execute("ALTER TABLE quarantine DROP COLUMN site")
