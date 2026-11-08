"""``learning_decisions.kind`` に ``quarantine_review``（隔離の確認）を加える。

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-08

M10-02、ADR-0035。隔離の確認は人の判断で、適用（隔離の行に確認の記録を立てる）は learning-worker が
行う。0012 が ``answer`` を加えたのと同じ形。
"""

from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_OLD = "kind IN ('resolution', 'curation', 'action', 'answer')"
_NEW = "kind IN ('resolution', 'curation', 'action', 'answer', 'quarantine_review')"


def upgrade() -> None:
    """``kind`` の CHECK に ``quarantine_review`` を加える。"""
    op.execute("ALTER TABLE learning_decisions DROP CONSTRAINT learning_decisions_kind_check")
    op.execute(
        "ALTER TABLE learning_decisions "
        f"ADD CONSTRAINT learning_decisions_kind_check CHECK ({_NEW})"
    )


def downgrade() -> None:
    """``quarantine_review`` を取り除く。行が残っていれば失敗する（人の判断を消さない）。"""
    op.execute("ALTER TABLE learning_decisions DROP CONSTRAINT learning_decisions_kind_check")
    op.execute(
        "ALTER TABLE learning_decisions "
        f"ADD CONSTRAINT learning_decisions_kind_check CHECK ({_OLD})"
    )
