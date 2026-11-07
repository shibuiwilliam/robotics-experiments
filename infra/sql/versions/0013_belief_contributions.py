"""信念の三源の寄与を投影表に持たせる。

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-07

M8-17-2、ADR-0020。信念更新が出す三源の寄与（観測・記録・計画。対数尤度比の絶対値）は
メトリクスにしか使われず、アサーションの来歴（バンドル IRI）にも残っていなかった。
証拠の三源並置（設計書 §8.2、§10.3）はこれを読む。

**追記専用の投影**で、アサーション本体（Oxigraph）には触れない。主キーは ``assertion_id``。
belief-worker が **寄与 → アサーション** の順に書く（逆だと、源が分からない文が残る）。
寄与だけ残る孤児は無害（読み出しがアサーションと突き合わせる）。

**hypertable にしない。** 読み出しは「拠点 × 実体 × フルーエント × 時間範囲」で、行数は信念の
更新数に比例する。保持窓は運用で決める（ADR-0020 の未決）。
"""

from __future__ import annotations

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """寄与の投影表を作る。"""
    op.execute(
        """
        CREATE TABLE belief_contributions (
            assertion_id     uuid        NOT NULL PRIMARY KEY,
            site             text        NOT NULL,
            entity           text        NOT NULL,
            fluent           text        NOT NULL,
            at               timestamptz NOT NULL,
            prior            real        NOT NULL CHECK (prior BETWEEN 0 AND 1),
            posterior        real        NOT NULL CHECK (posterior BETWEEN 0 AND 1),
            observed         real        NOT NULL CHECK (observed >= 0),
            record           real        NOT NULL CHECK (record >= 0),
            plan             real        NOT NULL CHECK (plan >= 0),
            source_event_id  text        NOT NULL,
            excluded_records integer     NOT NULL DEFAULT 0 CHECK (excluded_records >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX belief_contributions_series ON belief_contributions "
        "(site, entity, fluent, at)"
    )
    op.execute(
        "COMMENT ON TABLE belief_contributions IS "
        "'信念更新の三源の寄与（対数尤度比の絶対値）。追記専用の投影。証拠の並置が読む。'"
    )


def downgrade() -> None:
    """投影表を落とす（過去の寄与は再計算できない。落とすと並置は「記録されていない」になる）。"""
    op.execute("DROP TABLE IF EXISTS belief_contributions")
