"""取込ワーカーのカーソルを投影表に持たせる。

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-25

M8-25-5、ADR-0016。取込ワーカーは業務システムの表を「前回どこまで読んだか」（``updated_at`` の
最大）から差分で読む。カーソルがプロセス内のメモリにあると、再起動のたびに全行を読み直す
（決定的な ``event_id`` で消費側が弾くので壊れはしないが、表が大きいと無駄が大きい）。

**キーは (拠点, 情報源, 表)。** 同じ表名でも情報源が違えば別の業務システムである。

**送信の後に進める**（所見 52）。進めてから送ると、送信に失敗した行を二度と読まない。

**hypertable にしない。** 「拠点 × 情報源 × 表」ごとの最新状態の投影である（grounding_watermarks と
同じ形）。資産台帳は毎周期全行を読む（消えた行を知るため）ので、カーソルを持たない。
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """取込カーソルの投影表を作る。"""
    op.execute(
        """
        CREATE TABLE ingest_cursors (
            site           text        NOT NULL,
            source         text        NOT NULL,
            table_name     text        NOT NULL,
            pulled_through timestamptz NOT NULL,
            updated_at     timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (site, source, table_name)
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE ingest_cursors IS "
        "'取込ワーカーが業務システムの表を「どこまで読んだか」（updated_at の最大）。"
        "送信の後に進める。後退しない。'"
    )


def downgrade() -> None:
    """投影表を落とす（全行の読み直しで再構築できる）。"""
    op.execute("DROP TABLE IF EXISTS ingest_cursors")
