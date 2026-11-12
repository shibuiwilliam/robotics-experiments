"""判断の種類に「アクション実行」を加える。

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-04

M8-21、ADR-0014。アクション実行には**人の承認が要る**（設計書 §9.3、`riskClass ≥ medium`）。
承認は判断であり、実行はその適用なので、既にある判断キュー（0005）に `kind='action'` として
入れる。

**専用の表を作らない。** 冪等の鍵（`decision_id` + `ON CONFLICT DO NOTHING`）が既にあり、
**アクションの二重実行はこの基盤で最悪の失敗である**（業務システムを 2 回変える）。同じ仕掛けを
作り直す理由が無い（ADR-0014 の選択肢 B を参照）。

CHECK を広げるだけで、既存の行には影響しない。
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_OLD = "kind IN ('resolution', 'curation')"
_NEW = "kind IN ('resolution', 'curation', 'action')"


def upgrade() -> None:
    """``kind`` の CHECK に ``action`` を加える。"""
    op.execute("ALTER TABLE learning_decisions DROP CONSTRAINT learning_decisions_kind_check")
    op.execute(
        f"ALTER TABLE learning_decisions ADD CONSTRAINT learning_decisions_kind_check CHECK ({_NEW})"
    )


def downgrade() -> None:
    """``action`` を取り除く。

    **行が残っていれば失敗する。** 消してから狭めるのは危険で（人の判断を消すことになる）、
    失敗させて運用者に判断させる。
    """
    op.execute("ALTER TABLE learning_decisions DROP CONSTRAINT learning_decisions_kind_check")
    op.execute(
        f"ALTER TABLE learning_decisions ADD CONSTRAINT learning_decisions_kind_check CHECK ({_OLD})"
    )
