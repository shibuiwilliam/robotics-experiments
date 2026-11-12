"""``learning_decisions.kind`` に ``answer``（人の確認回答）を加える。

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-07

M8-29、ADR-0018。人の確認回答は判断であり、適用（主張イベントの送信と観測要求の充足）は
learning-worker が行う。0008 が ``action`` を加えたのと同じ形。
"""

from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_OLD = "kind IN ('resolution', 'curation', 'action')"
_NEW = "kind IN ('resolution', 'curation', 'action', 'answer')"


def upgrade() -> None:
    """``kind`` の CHECK に ``answer`` を加える。"""
    op.execute("ALTER TABLE learning_decisions DROP CONSTRAINT learning_decisions_kind_check")
    op.execute(
        "ALTER TABLE learning_decisions "
        f"ADD CONSTRAINT learning_decisions_kind_check CHECK ({_NEW})"
    )


def downgrade() -> None:
    """``answer`` を取り除く。

    **行が残っていれば失敗する。** 消してから狭めるのは危険で（人の判断を消すことになる）、
    失敗させて運用者に判断させる。
    """
    op.execute("ALTER TABLE learning_decisions DROP CONSTRAINT learning_decisions_kind_check")
    op.execute(
        "ALTER TABLE learning_decisions "
        f"ADD CONSTRAINT learning_decisions_kind_check CHECK ({_OLD})"
    )
